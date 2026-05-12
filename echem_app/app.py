"""Flask entry point for the Neotrient Echem Analysis App.

Run:

    python -m echem_app.app

Browser opens at http://127.0.0.1:5050  (port 5000 is hijacked by macOS
AirPlay Receiver, so we default to 5050; override with ECHEM_PORT).
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event, Lock
from typing import Any, Dict, List, Optional

from flask import (
    Flask, Response, jsonify, render_template, request, send_file,
    stream_with_context, url_for,
)

# Make sibling cv_app package importable
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from cv_app.analysis import basic_features

from . import ai_model, config_store
from .audit import AuditLog
from .connection import resolve_device_connection, status_default_device
from .exporter import build_export_bundle
from .lims_client import post_to_lims
from .measurement import (
    MeasurementOptions, cv_params_from_preset, swv_params_from_preset,
    params_from_preset, date_subfolder,
    iter_measurement,
)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


HERE = Path(__file__).resolve().parent
EXPORTS_ROOT = HERE / "exports"
EXPORTS_ROOT.mkdir(parents=True, exist_ok=True)
MODELS_DIR = HERE / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)

# Optional internal replay (NOT exposed in the operator UI — used only for
# automated tests via the `--replay` env override).
REPLAY_DEFAULT = (
    _PROJECT_ROOT / "MethodSCRIPT_Examples-master" / "Example_Python"
    / "Example_Python" / "cv_result.txt"
)


# ---------------------------------------------------------------------------
# In-memory session state
# ---------------------------------------------------------------------------


@dataclass
class MeasuredSample:
    """A measurement that's been completed in this session.

    A session can hold many of these: the operator measures sample A, then
    sample B, then sample C, runs analysis on all of them at once, and
    saves a single batch zip.
    """
    sample_id: str
    sample_meta: Dict[str, Any]
    csv_path: str
    measured_at: str
    n_samples: int
    cancelled: bool = False
    # Filled in when the operator runs analysis on this sample
    predictions: Optional[Dict[str, Any]] = None
    linear: Optional[Dict[str, Any]] = None
    features: Optional[Dict[str, Any]] = None
    review_flags: List[str] = field(default_factory=list)
    banner: str = "pending"
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sample_id": self.sample_id,
            "sample_meta": self.sample_meta,
            "csv_path": self.csv_path,
            "measured_at": self.measured_at,
            "n_samples": self.n_samples,
            "cancelled": self.cancelled,
            "predictions": self.predictions,
            "linear": self.linear,
            "features": self.features,
            "review_flags": self.review_flags,
            "banner": self.banner,
            "notes": self.notes,
            "analyzed": self.predictions is not None,
            "per_cycle": getattr(self, "per_cycle", []) or [],
        }


@dataclass
class SessionState:
    operator: str = ""
    device_id: str = ""
    device_name: str = ""
    device_record_id: str = ""
    preset_record_id: str = ""
    preset_name: str = ""
    preset: Optional[Dict[str, Any]] = None
    # v0.2.0: which Sample Identification form variant Phase 1 should
    # render.  "clinical" = the v0.1.15 hospital fields (Sample ID,
    # Donor pouch, Collection date, Storage location, Aliquot purpose,
    # Clinical site, Notes).  "standard" = calibration / standard-
    # solution fields (Solution name, Concentration, Prep date, Lot,
    # Solvent, Notes).
    sample_id_preset: str = "clinical"
    started_at: Optional[str] = None
    # Currently identified sample (the one Phase 1 just scanned).
    sample_id: Optional[str] = None
    sample_meta: Dict[str, Any] = field(default_factory=dict)
    # Most recent measurement output — used during the brief window between
    # Phase 3 finish and the sample being added to `measured_samples`.
    last_csv_path: Optional[str] = None
    # Batch of completed measurements for this session.
    measured_samples: List[MeasuredSample] = field(default_factory=list)
    audit: Optional[AuditLog] = None
    cancel_event: Event = field(default_factory=Event)


_STATE = SessionState()
_STATE_LOCK = Lock()


# ---------------------------------------------------------------------------
# Persistent device connection
# ---------------------------------------------------------------------------
#
# Opening a BLE link to PS-539B takes 30-60 s the first time (scan, pairing,
# MethodSCRIPT validation).  Doing that for every measurement is unworkable.
# Instead we open it ONCE on Begin session and keep it alive until the
# operator cancels the session or closes the app.  Each measurement
# borrows the held connection.
#
# Sequential measurements only — Flask is threaded but we serialise device
# I/O behind _CONN_LOCK so two requests can't talk to the chip at once.

_HELD_CONN = None      # BleDeviceConnection or cv_app.device.DeviceConnection
_CONN_LOCK = Lock()


def _open_connection_for_active_device() -> None:
    """Open + hold a connection to the active device.  Idempotent: if a
    connection is already held, this is a no-op."""
    global _HELD_CONN
    with _CONN_LOCK:
        if _HELD_CONN is not None:
            return
        with _STATE_LOCK:
            record = (config_store.devices().get(_STATE.device_record_id)
                      if _STATE.device_record_id else
                      config_store.devices().get_default()) or {}
        port, baud = resolve_device_connection(record)
        if port and port.startswith("ble://"):
            from .ble_transport import BleDeviceConnection
            ble_name = port[len("ble://"):]
            conn = BleDeviceConnection(ble_name)
        else:
            from cv_app.device import DeviceConnection
            conn = DeviceConnection(port=port, baudrate=baud)
        conn.open()
        _HELD_CONN = conn


def _close_connection() -> None:
    """Release the held connection (if any).  Safe to call multiple times."""
    global _HELD_CONN
    with _CONN_LOCK:
        if _HELD_CONN is not None:
            try:
                _HELD_CONN.close()
            except Exception:
                pass
            _HELD_CONN = None


def _held_connection():
    """Return the currently-held connection, or None."""
    return _HELD_CONN


# Mock LIMS lookup table (until v9 .zip ingestion ships)
_FAKE_SAMPLES = {
    "DP-0001-V2": {
        "sample_id": "DP-0001-V2",
        "donor_pouch": "DP-0001",
        "collection_date": "2026-04-18",
        "storage_location": "Freezer A · Rack 3 · Slot B7",
        "aliquot_purpose": "Echem (Vial 2 of 3)",
        "clinical_site": "Suzhou Maternity #2",
        "from": "LIMS",
    },
    "DP-0002-V1": {
        "sample_id": "DP-0002-V1",
        "donor_pouch": "DP-0002",
        "collection_date": "2026-04-19",
        "storage_location": "Freezer A · Rack 3 · Slot B8",
        "aliquot_purpose": "Echem (Vial 1 of 3)",
        "clinical_site": "Suzhou Maternity #2",
        "from": "LIMS",
    },
}


# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------


app = Flask(__name__, template_folder=str(HERE / "templates"),
            static_folder=str(HERE / "static"))
app.json.compact = False
# Disable static-file caching: lab PCs run a single browser session for
# weeks, and aggressive caching means UI changes don't show up until a
# manual hard-refresh.  This makes every reload pick up the latest CSS/JS.
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
app.config["TEMPLATES_AUTO_RELOAD"] = True
LOG = logging.getLogger(__name__)


@app.after_request
def _add_no_cache_headers(response):
    # Belt-and-braces: also tell the browser explicitly not to cache.
    if request.path.startswith("/static/") or request.path == "/":
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
    return response


# ---------- index ----------------------------------------------------------


@app.get("/")
def index():
    from . import __version__
    return render_template("index.html", app_version=__version__)


@app.get("/sequence")
def sequence_page():
    """v0.2.0 Sequence builder.

    Sequence is now embedded as a phase inside the main SPA (index.html)
    rather than living at its own route — operators reach it by picking
    "Sequence" run mode in Configuration and pressing Begin session.
    This route exists only as a back-compat redirect for any cached
    bookmark from the early v0.2.0 build.
    """
    from flask import redirect
    return redirect("/", code=302)


@app.get("/server")
def server_status():
    """Minimal landing page for the operator's Mac.

    Shows the LAN URL the operator should open from their phone or
    iPad (same-WiFi remote control).  Includes a QR code so they can
    scan it with the phone's camera and open the app instantly.
    """
    from . import __version__
    port = DEFAULT_PORT
    ip   = _local_ip()
    lan_url  = f"http://{ip}:{port}" if ip and ip != "your-mac-ip" \
                                     else f"http://localhost:{port}"
    mdns_url = f"http://neotrient.local:{port}" if DEFAULT_HOST == "0.0.0.0" \
                                                else None
    qr_svg = _qr_svg(lan_url, scale=8)
    return render_template("server.html",
                           version=__version__,
                           lan_url=lan_url,
                           mdns_url=mdns_url,
                           qr_svg=qr_svg)


def _qr_svg(text: str, scale: int = 8) -> str:
    """Return an SVG <svg>…</svg> string encoding `text` as a QR code.

    Uses the well-tested `qrcode` library — our in-tree minimal encoder
    had subtle bugs that made iPhone refuse to decode the result.  We
    keep using `qrcode`'s matrix output (no PIL dependency) and emit
    SVG ourselves so the bundle stays slim.

    Returns an empty string on failure; the page just shows the URL.
    """
    try:
        import qrcode
        q = qrcode.QRCode(
            version=None,                   # auto-pick smallest version
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=1,
            border=2,
        )
        q.add_data(text)
        q.make(fit=True)
        modules = q.get_matrix()            # 2-D list of booleans
    except Exception as exc:
        LOG.warning("QR encode failed: %s", exc)
        return ""
    n = len(modules)
    size = n * scale
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {size} {size}" width="{size}" height="{size}" '
        f'shape-rendering="crispEdges">'
        f'<rect width="{size}" height="{size}" fill="white"/>'
    ]
    for r, row in enumerate(modules):
        for c, on in enumerate(row):
            if on:
                parts.append(
                    f'<rect x="{c*scale}" y="{r*scale}" '
                    f'width="{scale}" height="{scale}" fill="#1A1F36"/>'
                )
    parts.append("</svg>")
    return "".join(parts)


@app.get("/api/version")
def api_version():
    from . import __version__
    return jsonify({"version": __version__})


@app.get("/api/status")
def api_status():
    palmsens = _palmsens_status()
    ai = ai_model.status_summary()
    with _STATE_LOCK:
        s = _STATE
        return jsonify({
            "operator": s.operator,
            "device_name": s.device_name,
            "device_id": s.device_id,
            "preset_name": s.preset_name,
            "session_started_at": s.started_at,
            "sample_id": s.sample_id,
            "palmsens": palmsens,
            "ai_model": ai,
            # True iff we actually hold an open connection right now.
            # The sidebar uses this to show Connected vs Disconnected
            # instead of inferring it from whether a record exists.
            "connection_held": _held_connection() is not None,
        })


def _palmsens_status() -> Dict[str, Any]:
    """Connection status for the device the operator selected for THIS
    session — falling back to the registered default only if no session
    has begun yet.

    Honours the device record's `connection_type` (USB / Bluetooth /
    manual) so a paired BT/BLE device shows up as "connected" without
    triggering a useless USB auto-detect probe (which used to spam the
    log with "0 candidates found" and trigger the USB-flavored "Device
    not detected" modal even when the operator was actually on BLE).
    """
    from .connection import status_for_record
    with _STATE_LOCK:
        active_id = _STATE.device_record_id
    if active_id:
        rec = config_store.devices().get(active_id)
        if rec:
            return status_for_record(rec)
    # No active session yet — fall back to the registered default.
    return status_default_device()


# ---------------------------------------------------------------------------
# Registries — Presets / Devices / Models
# ---------------------------------------------------------------------------


@app.get("/api/presets")
def api_presets_list():
    return jsonify({
        "items": config_store.presets().list_all(),
        "default_id": (config_store.presets().get_default() or {}).get("id"),
    })


@app.post("/api/presets")
def api_presets_create():
    body = request.get_json(force=True, silent=True) or {}
    rec = config_store.presets().add(body, set_default=bool(body.get("set_default")))
    return jsonify({"ok": True, "preset": rec})


@app.put("/api/presets/<pid>")
def api_presets_update(pid):
    body = request.get_json(force=True, silent=True) or {}
    rec = config_store.presets().update(pid, body)
    if rec is None:
        return jsonify({"ok": False, "error": "not found"}), 404
    return jsonify({"ok": True, "preset": rec})


@app.post("/api/presets/<pid>/set_default")
def api_presets_default(pid):
    ok = config_store.presets().set_default(pid)
    return jsonify({"ok": ok})


@app.delete("/api/presets/<pid>")
def api_presets_delete(pid):
    """Delete a preset record.

    v0.2.0: presets flagged with ``protected: true`` (the team-supplied
    originals like preset-1, preset-swv-1, Dummy cell test) require the
    admin password to delete — same pattern as default-device deletion.
    Operators can still delete their own presets without a password.
    """
    DEFAULT_DELETE_PASSWORD = "NEO-001"
    rec = config_store.presets().get(pid)
    if rec and rec.get("protected"):
        body = request.get_json(force=True, silent=True) or {}
        supplied = (body.get("password")
                    or request.headers.get("X-Delete-Password")
                    or "")
        if supplied != DEFAULT_DELETE_PASSWORD:
            return jsonify({
                "ok": False,
                "needs_password": True,
                "error": "Password required to delete this protected preset.",
            }), 403
    return jsonify({"ok": config_store.presets().delete(pid)})


@app.get("/api/devices")
def api_devices_list():
    return jsonify({
        "items": config_store.devices().list_all(),
        "default_id": (config_store.devices().get_default() or {}).get("id"),
    })


@app.post("/api/devices")
def api_devices_create():
    body = request.get_json(force=True, silent=True) or {}
    rec = config_store.devices().add(body, set_default=bool(body.get("set_default")))
    return jsonify({"ok": True, "device": rec})


@app.put("/api/devices/<did>")
def api_devices_update(did):
    body = request.get_json(force=True, silent=True) or {}
    rec = config_store.devices().update(did, body)
    if rec is None:
        return jsonify({"ok": False, "error": "not found"}), 404
    return jsonify({"ok": True, "device": rec})


@app.post("/api/devices/<did>/set_default")
def api_devices_default(did):
    ok = config_store.devices().set_default(did)
    return jsonify({"ok": ok})


@app.delete("/api/devices/<did>")
def api_devices_delete(did):
    """Delete a device record.

    Deleting the *default* device requires a confirmation password
    (`NEO-001`).  This isn't a security boundary — it's a guard against
    accidental clicks on a record that's painful to recreate.  The
    password is documented in PASSWORDS.txt at the project root.
    """
    DEFAULT_DELETE_PASSWORD = "NEO-001"
    rec = config_store.devices().get(did)
    if rec and rec.get("is_default"):
        body = request.get_json(force=True, silent=True) or {}
        supplied = (body.get("password")
                    or request.headers.get("X-Delete-Password")
                    or "")
        if supplied != DEFAULT_DELETE_PASSWORD:
            return jsonify({
                "ok": False,
                "needs_password": True,
                "error": "Password required to delete the default device.",
            }), 403
    return jsonify({"ok": config_store.devices().delete(did)})


@app.get("/api/models")
def api_models_list():
    return jsonify({
        "items": ai_model.list_models(),
        "default_id": (config_store.models().get_default() or {}).get("id"),
    })


@app.post("/api/models")
def api_models_create():
    body = request.get_json(force=True, silent=True) or {}
    name = body.get("name") or "Unnamed model"
    path = body.get("path") or ""
    rec = ai_model.register_model_from_file(
        name=name,
        path=path,
        ions=body.get("ions"),
        notes=body.get("notes", ""),
        set_default=bool(body.get("set_default")),
    )
    return jsonify({"ok": True, "model": rec})


@app.put("/api/models/<mid>")
def api_models_update(mid):
    body = request.get_json(force=True, silent=True) or {}
    rec = config_store.models().update(mid, body)
    if rec is None:
        return jsonify({"ok": False, "error": "not found"}), 404
    return jsonify({"ok": True, "model": rec})


@app.post("/api/models/<mid>/set_default")
def api_models_default(mid):
    ok = config_store.models().set_default(mid)
    return jsonify({"ok": ok})


@app.delete("/api/models/<mid>")
def api_models_delete(mid):
    """Delete a model record.

    v0.2.0: models flagged with ``protected: true`` require the admin
    password to delete (same NEO-001 pattern as protected presets and
    default devices)."""
    DEFAULT_DELETE_PASSWORD = "NEO-001"
    rec = config_store.models().get(mid)
    if rec and rec.get("protected"):
        body = request.get_json(force=True, silent=True) or {}
        supplied = (body.get("password")
                    or request.headers.get("X-Delete-Password")
                    or "")
        if supplied != DEFAULT_DELETE_PASSWORD:
            return jsonify({
                "ok": False,
                "needs_password": True,
                "error": "Password required to delete this protected model.",
            }), 403
    return jsonify({"ok": config_store.models().delete(mid)})


# ---------------------------------------------------------------------------
# Phase 0 — Session start
# ---------------------------------------------------------------------------


@app.post("/api/connection/test")
def api_connection_test():
    """Open the active device's connection and play a confirmation
    beep.  This is the slow path — for BLE PS-539B the first run takes
    ~30-60 s (scan + pair + validation).  Once held, every subsequent
    measurement is instant.

    Returns:
        {ok: True, info: {...}} on success
        {ok: False, error: "..."} on failure
    """
    try:
        _open_connection_for_active_device()
    except Exception as exc:
        LOG.exception("connection test failed")
        return jsonify({"ok": False, "error": str(exc)}), 500

    conn = _held_connection()
    info = conn.info if conn and getattr(conn, "info", None) else None
    info_dict = ({
        "device_type": info.device_type,
        "firmware_version": info.firmware_version,
        "mscript_version": info.mscript_version,
        "serial_number": info.serial_number,
    } if info else None)

    # The beep test was removed in v0.1.11 — on some EmStat4T firmware
    # revs the post-script drain (`iter_data_packages`) doesn't emit a
    # clean end-of-script marker, so the loop would hang while still
    # holding _CONN_LOCK, blocking out every subsequent measurement
    # with a "device busy" error.  The connection itself is already
    # validated by the open() handshake above (abort_and_sync +
    # get_device_type + get_firmware_version etc.), and the operator
    # has visual confirmation via the green "● Connected" pill in the
    # sidebar.  Skipping the beep is a strict win for reliability.
    beeped = False

    if _STATE.audit:
        _STATE.audit.event("connection_test_ok",
                           device_type=info_dict.get("device_type") if info_dict else None,
                           beeped=beeped)
    return jsonify({"ok": True, "info": info_dict, "beeped": beeped})


@app.post("/api/connection/release")
def api_connection_release():
    """Drop the held connection (e.g. when cancelling the session)."""
    _close_connection()
    if _STATE.audit:
        _STATE.audit.event("connection_released")
    return jsonify({"ok": True})


_BEEP_SCRIPT = (
    "e\n"
    "beep 24 70 200m\n"
    "wait 120m\n"
    "beep 24 70 200m\n"
    "wait 120m\n"
    "beep 24 70 200m\n"
    "wait 120m\n"
    "\n"
)


@app.post("/api/session/start")
def api_session_start():
    body = request.get_json(force=True, silent=True) or {}
    operator = (body.get("operator") or "").strip()
    device_id = body.get("device_id") or ""
    preset_id = body.get("preset_id") or ""
    # v0.2.0: which Sample Identification form variant should Phase 1
    # render — "clinical" or "standard".  Defaults to clinical (the
    # v0.1.15 layout) so older clients get the previous behaviour.
    sample_id_preset = (body.get("sample_id_preset") or "clinical").strip().lower()
    if sample_id_preset not in {"clinical", "standard"}:
        sample_id_preset = "clinical"

    device = (config_store.devices().get(device_id)
              or config_store.devices().get_default())
    preset = (config_store.presets().get(preset_id)
              or config_store.presets().get_default())

    if not operator:
        return jsonify({"ok": False, "error": "Operator name is required."}), 400
    if device is None:
        return jsonify({"ok": False, "error": "No measurement device registered."}), 400
    if preset is None:
        return jsonify({"ok": False, "error": "No preset registered."}), 400

    with _STATE_LOCK:
        _STATE.operator = operator
        _STATE.device_record_id = device["id"]
        _STATE.device_name = device.get("name", "")
        _STATE.device_id = device.get("device_id", "")
        _STATE.preset_record_id = preset["id"]
        _STATE.preset_name = preset.get("name", "")
        _STATE.preset = preset
        _STATE.sample_id_preset = sample_id_preset
        _STATE.started_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        _STATE.audit = AuditLog(
            log_path=date_subfolder(EXPORTS_ROOT) / "audit.log",
            session_id=_STATE.started_at,
        )
        _STATE.audit.event("session_start",
                           operator=operator,
                           device=device,
                           preset=preset)

    return jsonify({
        "ok": True,
        "operator": operator,
        "device": device,
        "preset": preset,
        "sample_id_preset": sample_id_preset,
        "palmsens": _palmsens_status(),
    })


# ---------------------------------------------------------------------------
# Phase 1 — Sample lookup
# ---------------------------------------------------------------------------


@app.post("/api/sample/manual")
def api_sample_manual():
    """Operator-typed sample metadata when the QR can't be scanned."""
    body = request.get_json(force=True, silent=True) or {}
    sample_id = (body.get("sample_id") or "").strip()
    if not sample_id:
        return jsonify({"ok": False, "error": "sample_id required"}), 400
    # v0.2.0: two sample-identification form variants share this
    # endpoint.  The frontend sends `sample_id_preset` to disambiguate;
    # if it's "standard", we capture solution-specific fields, otherwise
    # the v0.1.15 clinical fields.  All fields are stored loosely so the
    # downstream save layer is agnostic.
    variant = (body.get("sample_id_preset") or "clinical").strip().lower()
    if variant not in {"clinical", "standard"}:
        variant = "clinical"

    meta = {
        "sample_id": sample_id,
        "sample_id_preset": variant,
        "notes": body.get("notes", ""),
        "from": "MANUAL",
    }
    if variant == "standard":
        meta.update({
            # v0.2.x round 8: form now sends sample_type (Standard /
            # Blank / Sample / QC) instead of the old free-text
            # solution_name.  Accept either for back-compat with any
            # already-saved meta sent by older clients.
            "sample_type":         body.get("sample_type") or body.get("solution_name", ""),
            "concentration_value": body.get("concentration_value", ""),
            "concentration_unit":  body.get("concentration_unit", ""),
            "prep_date":           body.get("prep_date", ""),
            "lot":                 body.get("lot", ""),
            "solvent":             body.get("solvent", ""),
        })
    else:
        meta.update({
            "donor_pouch":      body.get("donor_pouch", ""),
            "collection_date":  body.get("collection_date", ""),
            "storage_location": body.get("storage_location", ""),
            "aliquot_purpose":  body.get("aliquot_purpose", ""),
            "clinical_site":    body.get("clinical_site") or body.get("hospital_site", ""),
        })
    with _STATE_LOCK:
        _STATE.sample_id = sample_id
        _STATE.sample_meta = meta
        if _STATE.audit:
            _STATE.audit.event("sample_manual_entry", sample_id=sample_id)
    return jsonify({"ok": True, "meta": meta, "from": "MANUAL"})


@app.get("/api/sample/lookup")
def api_sample_lookup():
    sample_id = (request.args.get("sample_id") or "").strip()
    if not sample_id:
        return jsonify({"ok": False, "error": "sample_id required"}), 400

    meta = _FAKE_SAMPLES.get(sample_id)
    if meta:
        with _STATE_LOCK:
            _STATE.sample_id = sample_id
            _STATE.sample_meta = meta
            if _STATE.audit:
                _STATE.audit.event("sample_lookup", sample_id=sample_id, source="LIMS")
        return jsonify({"ok": True, "meta": meta, "from": "LIMS"})

    with _STATE_LOCK:
        _STATE.sample_id = sample_id
        _STATE.sample_meta = {"sample_id": sample_id, "from": "MANUAL"}
        if _STATE.audit:
            _STATE.audit.event("sample_lookup", sample_id=sample_id, source="MANUAL")
    return jsonify({"ok": True, "meta": _STATE.sample_meta, "from": "MANUAL"})


# ---------------------------------------------------------------------------
# Phases 2+3 — Measurement (single SSE stream)
# ---------------------------------------------------------------------------


@app.post("/api/measure/start")
def api_measure_start():
    body = request.get_json(force=True, silent=True) or {}
    use_replay = bool(body.get("use_replay", False))   # internal/dev only
    # "force_manual" — operator-side escape hatch when drop-detect gets
    # stuck (dry SPE, OCP noise floor, etc.).  Bypasses the drop-detect
    # wait while keeping every other preset parameter intact.
    force_manual = bool(body.get("force_manual", False))

    with _STATE_LOCK:
        preset = _STATE.preset or config_store.presets().get_default() or {}
        # v0.2.0: dispatch by technique (CV → CVParameters, SWV → SWVParameters).
        # Both end up wrapped by MeasurementOptions which builds the right
        # script downstream.
        technique, params = params_from_preset(preset)
        trigger_mode = (preset or {}).get("trigger_mode", "drop_detect")
        if force_manual:
            trigger_mode = "manual"
        sample_id = _STATE.sample_id or "unknown"
        operator = _STATE.operator or "operator"
        device_id_str = _STATE.device_id or _STATE.device_name or "device"
        _STATE.cancel_event = Event()
        cancel_event = _STATE.cancel_event
        if _STATE.audit:
            _STATE.audit.event("measure_start",
                               sample_id=sample_id,
                               trigger_mode=trigger_mode,
                               force_manual=force_manual,
                               params=params.to_dict())

    use_drop_detect = (trigger_mode == "drop_detect")
    start_countdown_s = int((preset or {}).get("start_countdown_s", 0) or 0)
    post_drop_settle_s = int((preset or {}).get("post_drop_settle_s", 0) or 0)
    drop_detect_method = (preset or {}).get("drop_detect_method", "voltage") or "voltage"
    voltage_threshold_mv = int((preset or {}).get("voltage_threshold_mv", 30) or 30)

    # Resolve the active device's connection (USB / Bluetooth / manual).
    # If the resolution fails (e.g., BT device not paired), surface a
    # clear error to the SSE stream rather than letting cv_app's
    # auto-detect blow up.
    resolved_port: Optional[str] = None
    resolved_baud: Optional[int] = None
    resolve_error: Optional[str] = None
    if not use_replay:
        try:
            device_record = (config_store.devices().get(_STATE.device_record_id)
                             if _STATE.device_record_id
                             else config_store.devices().get_default())
            resolved_port, resolved_baud = resolve_device_connection(device_record or {})
        except Exception as exc:
            resolve_error = str(exc)

    # Reuse the connection opened on Begin session so each measurement
    # doesn't have to wait through scan + pair + validation again.
    held = _held_connection() if not use_replay else None

    opts = MeasurementOptions(
        params=params,
        sample_id=sample_id,
        operator=operator,
        device_id=device_id_str,
        output_root=EXPORTS_ROOT,
        use_drop_detect=use_drop_detect,
        drop_detect_method=drop_detect_method,
        voltage_threshold_mv=voltage_threshold_mv,
        start_countdown_s=start_countdown_s,
        post_drop_settle_s=post_drop_settle_s,
        port=resolved_port,
        baudrate=resolved_baud,
        held_connection=held,
        replay_path=REPLAY_DEFAULT if use_replay else None,
        cancel_event=cancel_event,
    )

    @stream_with_context
    def gen():
        yield "retry: 2000\n\n"
        if resolve_error:
            yield f"data: {json.dumps({'event':'error','message':resolve_error})}\n\n"
            yield "event: close\ndata: {}\n\n"
            return
        # Serialize all serial-port I/O behind _CONN_LOCK so a measurement
        # can never overlap with the beep drain in /api/connection/test
        # (or vice versa).  Without this guard, two requests can hit the
        # same pyserial Serial object on different threads and pyserial
        # raises:
        #     SerialException: device reports readiness to read but
        #     returned no data (device disconnected or multiple access
        #     on port?)
        # Wait up to 10 seconds for any in-flight connect-test to finish;
        # if longer, surface a clear error instead of blocking forever.
        if not _CONN_LOCK.acquire(timeout=10.0):
            yield ('data: ' + json.dumps({
                'event': 'error',
                'message': ('The device is busy with another operation '
                            '(connect handshake or another measurement). '
                            'Wait a few seconds and try again.')
            }) + '\n\n')
            yield "event: close\ndata: {}\n\n"
            return
        try:
            for evt in iter_measurement(opts):
                payload = json.dumps(evt, default=str)
                yield f"data: {payload}\n\n"
                if evt.get("event") == "finished":
                    with _STATE_LOCK:
                        if evt.get("csv_path"):
                            _STATE.last_csv_path = evt.get("csv_path")
                        # Push this measurement into the session-wide batch list
                        # (replacing any prior measurement with the same sample_id
                        # so re-measuring overwrites cleanly).
                        sid = _STATE.sample_id or "unknown"
                        _STATE.measured_samples = [
                            m for m in _STATE.measured_samples if m.sample_id != sid
                        ]
                        _STATE.measured_samples.append(MeasuredSample(
                            sample_id=sid,
                            sample_meta=dict(_STATE.sample_meta),
                            csv_path=evt.get("csv_path") or "",
                            measured_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                            n_samples=evt.get("n_samples", 0),
                        ))
                        if _STATE.audit:
                            _STATE.audit.event("measure_finished",
                                               sample_id=sid,
                                               csv_path=evt.get("csv_path"),
                                               n_samples=evt.get("n_samples"))
                    # Auto-save: writes ~/Documents/Neotrient/results/<date>/
                    # so data is never lost even if the operator forgets to
                    # click Save Data.  Idempotent / no-op on failure.
                    try:
                        from . import auto_save as _auto
                        _auto.save_measurement(
                            sample_id=sid,
                            operator=operator,
                            preset_name=(preset or {}).get("name", ""),
                            cv_points=evt.get("data") or evt.get("points"),
                            metadata={
                                "device_id": device_id_str,
                                "csv_path":  evt.get("csv_path"),
                                "n_samples": evt.get("n_samples"),
                                "trigger_mode": trigger_mode,
                                "params":   params.to_dict(),
                                "session":  _STATE.session_id if hasattr(_STATE, "session_id") else "",
                            },
                            status="ok",
                        )
                    except Exception as exc:
                        LOG.warning("auto_save.save_measurement failed: %s", exc)
                elif evt.get("event") == "cancelled":
                    with _STATE_LOCK:
                        if evt.get("csv_path"):
                            _STATE.last_csv_path = evt.get("csv_path")
                    # Log the cancellation in the daily log too
                    try:
                        from . import auto_save as _auto
                        _auto.log_session_event(
                            operator,
                            "measurement_cancelled",
                            sample_id=sample_id,
                            preset=(preset or {}).get("name", ""),
                        )
                    except Exception:
                        pass
        finally:
            try: _CONN_LOCK.release()
            except RuntimeError: pass
        yield "event: close\ndata: {}\n\n"

    return Response(gen(), mimetype="text/event-stream", headers={
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
    })


@app.post("/api/measure/cancel")
def api_measure_cancel():
    """Signal the in-flight measurement to stop. Returns immediately."""
    with _STATE_LOCK:
        _STATE.cancel_event.set()
        if _STATE.audit:
            _STATE.audit.event("measure_cancel")
    # Best-effort: also send abort to the device if we can find it.
    try:
        from cv_app.device import DeviceConnection
        with DeviceConnection() as dev:
            dev.device.abort_and_sync()
    except Exception as exc:
        LOG.debug("Device abort_and_sync failed (probably no device): %s", exc)
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Phase 4 — Inference
# ---------------------------------------------------------------------------


@app.post("/api/predict")
def api_predict():
    """Run analysis on one of the measured samples.

    Body:
        sample_id  (optional)  — which measured sample. Default = the most
                                 recent (or the currently active) one.
        model_id   (optional)  — which model. Default = registry default.
        cycles     (optional)  — list of scan indices to include
                                 (e.g. [0, 2]); None = all cycles.
    """
    body = request.get_json(force=True, silent=True) or {}
    model_id = body.get("model_id")
    requested_cycles = body.get("cycles")
    sample_id = body.get("sample_id")

    # Locate the right measurement entry.
    with _STATE_LOCK:
        if sample_id:
            entry = next((m for m in _STATE.measured_samples
                          if m.sample_id == sample_id), None)
        else:
            # Fall back to the most recent
            entry = (_STATE.measured_samples[-1]
                     if _STATE.measured_samples else None)

    if entry is None or not entry.csv_path or not Path(entry.csv_path).exists():
        return jsonify({"ok": False,
                        "error": "No measurement on file for that sample."}), 400

    all_samples = _load_samples_from_csv(Path(entry.csv_path))

    # Apply cycle filter if specified
    if requested_cycles is not None and len(requested_cycles) > 0:
        wanted = set(int(c) for c in requested_cycles)
        samples = [s for s in all_samples if s.scan in wanted]
    else:
        samples = all_samples

    if not samples:
        return jsonify({"ok": False,
                        "error": "No samples remain after cycle filter."}), 400

    features = basic_features(samples)

    main = ai_model.predict(model_id, features, sample_id=entry.sample_id)
    linear_record = next(
        (m for m in config_store.models().list_all() if m.get("kind") == "linear"),
        None,
    )
    linear = ai_model.predict(linear_record["id"] if linear_record else None,
                              features, sample_id=entry.sample_id)
    flags = ai_model.disagreement_flags(main, linear, threshold=0.30)

    banner = "ok"
    if flags:
        banner = "review"
    if any((s.status or "").startswith("OVERLOAD") for s in samples):
        banner = "anomaly"

    available_cycles = sorted({s.scan for s in all_samples})

    # Per-cycle predictions: feeds the Box / Swarm charts in the Overall
    # analysis section.  We re-run the model on each cycle's data alone so
    # each sample carries a distribution (one value per cycle) per ion.
    # Honor `requested_cycles` so the box/swarm distributions match what
    # the operator selected — without this filter the chart would always
    # show every available cycle, even when the user un-ticked some.
    cycles_for_distribution = (
        sorted(c for c in available_cycles if c in requested_cycles)
        if requested_cycles
        else available_cycles
    )
    per_cycle: List[Dict[str, Any]] = []
    if len(cycles_for_distribution) > 1:
        for cy in cycles_for_distribution:
            cycle_samples = [s for s in all_samples if s.scan == cy]
            if not cycle_samples:
                continue
            cf = basic_features(cycle_samples)
            cm = ai_model.predict(model_id, cf, sample_id=entry.sample_id)
            per_cycle.append({
                "cycle": cy,
                "predictions": [
                    {"ion": p.ion, "value_mg_l": p.value_mg_l,
                     "below_lod": p.below_lod}
                    for p in cm.predictions
                ],
            })

    # Persist on the measurement entry.
    with _STATE_LOCK:
        entry.features = features
        entry.predictions = main.to_dict()
        entry.linear = linear.to_dict()
        entry.review_flags = flags
        entry.banner = banner
        # Stash per-cycle preds on the entry too so the frontend can plot
        # distributions across re-renders.  The MeasuredSample dataclass
        # has no `per_cycle` field yet so we attach it as a regular attr.
        setattr(entry, "per_cycle", per_cycle)
        if _STATE.audit:
            _STATE.audit.event("predict",
                               sample_id=entry.sample_id,
                               cycles=requested_cycles or "all",
                               model=main.model_name,
                               banner=banner)

    return jsonify({
        "ok": True,
        "sample_id": entry.sample_id,
        "available_cycles": available_cycles,
        "applied_cycles": (sorted(list(requested_cycles))
                           if requested_cycles else available_cycles),
        "features": features,
        "primary": main.to_dict(),
        "linear": linear.to_dict(),
        "per_cycle": per_cycle,
        "review_flags": flags,
        "banner": banner,
    })


# ---------------------------------------------------------------------------
# Batch-of-measurements API (the "Save Data" page)
# ---------------------------------------------------------------------------


@app.get("/api/session/samples")
def api_session_samples_list():
    with _STATE_LOCK:
        return jsonify({
            "items": [m.to_dict() for m in _STATE.measured_samples],
            "active_sample_id": _STATE.sample_id,
        })


@app.delete("/api/session/samples/<sid>")
def api_session_samples_delete(sid):
    """Drop a measured sample from the session AND remove its files on disk.

    Operators expect "delete" to actually delete — both the in-memory
    record and the saved CSV (and any sibling artifacts: log, zip).
    Files are unlinked best-effort; failures are reported but do not
    block the in-memory removal.
    """
    removed_files: List[str] = []
    failed_files: List[str] = []
    with _STATE_LOCK:
        # Snapshot CSV paths for the matching samples before we drop them.
        targets = [m for m in _STATE.measured_samples if m.sample_id == sid]
        for m in targets:
            csv = Path(m.csv_path) if m.csv_path else None
            if not csv:
                continue
            # Sibling artifacts share the CSV's stem (log file, export zip,
            # etc.).  Globbing the parent dir for sample_<sid>_* picks up
            # everything that belongs to this acquisition.
            try:
                for p in csv.parent.glob(f"sample_{sid}_*"):
                    try:
                        if p.is_file():
                            p.unlink()
                            removed_files.append(str(p))
                    except OSError:
                        failed_files.append(str(p))
                # The csv itself, if not matched by the glob (e.g. custom name)
                if csv.exists():
                    try:
                        csv.unlink()
                        removed_files.append(str(csv))
                    except OSError:
                        failed_files.append(str(csv))
            except OSError:
                failed_files.append(str(csv))

        before = len(_STATE.measured_samples)
        _STATE.measured_samples = [m for m in _STATE.measured_samples
                                   if m.sample_id != sid]
        deleted = before != len(_STATE.measured_samples)
        if _STATE.audit:
            _STATE.audit.event("delete_sample",
                               sample_id=sid,
                               files_removed=len(removed_files))
    return jsonify({
        "ok": True,
        "deleted": deleted,
        "files_removed": removed_files,
        "files_failed": failed_files,
    })


@app.post("/api/session/samples/<sid>/select")
def api_session_samples_select(sid):
    """Make `sid` the active sample (e.g. operator clicked it on Save Data)."""
    with _STATE_LOCK:
        entry = next((m for m in _STATE.measured_samples
                      if m.sample_id == sid), None)
        if entry is None:
            return jsonify({"ok": False, "error": "not found"}), 404
        _STATE.sample_id = entry.sample_id
        _STATE.sample_meta = entry.sample_meta
        _STATE.last_csv_path = entry.csv_path
    return jsonify({"ok": True})


def _load_samples_from_csv(path: Path):
    from cv_app.params import Sample
    out: List[Sample] = []
    if not path.exists():
        return out
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("index"):
                continue
            parts = line.split(",")
            try:
                idx = int(parts[0]); scan = int(parts[1])
                pot = float(parts[2]); cur = float(parts[3])
            except (ValueError, IndexError):
                continue
            status = parts[4] if len(parts) > 4 else None
            cr = parts[5] if len(parts) > 5 else None
            out.append(Sample(potential_v=pot, current_a=cur,
                              index=idx, scan=scan,
                              status=status or None, current_range=cr or None))
    return out


# ---------------------------------------------------------------------------
# Phase 5 — Save Data
# ---------------------------------------------------------------------------


@app.post("/api/save")
def api_save():
    """Save **every** measured sample currently in the session as one batch.

    Body:
        notes         (optional) — operator notes shared by the whole batch
        reason        (optional) — override justification (required if any
                                   sample's banner is amber/red)
        sample_notes  (optional) — {sample_id: "per-sample note"}
    """
    body = request.get_json(force=True, silent=True) or {}
    notes = body.get("notes", "")
    sign_reason = body.get("reason", "")
    per_sample_notes = body.get("sample_notes") or {}

    with _STATE_LOCK:
        if not _STATE.measured_samples:
            return jsonify({"ok": False,
                            "error": "No measurements in this session yet."}), 400
        operator_block = {
            "name": _STATE.operator,
            "device_id": _STATE.device_id,
            "device_name": _STATE.device_name,
            "signed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "sign_reason": sign_reason,
        }
        session_meta = {
            "started_at": _STATE.started_at,
            "preset": _STATE.preset,
            "ai_model": ai_model.status_summary(),
        }
        audit_lines = _STATE.audit.entries() if _STATE.audit else []
        export_dir = date_subfolder(EXPORTS_ROOT)
        samples_snapshot = list(_STATE.measured_samples)

    if _STATE.audit:
        _STATE.audit.event("sign", reason=sign_reason,
                           n_samples=len(samples_snapshot))

    # Build one bundle per sample inside the date subfolder, plus a
    # single batch JSON summarising the run.
    bundle_paths: List[Path] = []
    per_sample_results = []
    for entry in samples_snapshot:
        if not entry.predictions:
            # Skip un-analysed samples — they go into the summary as
            # "pending" so the operator sees what was measured but never
            # analysed.
            per_sample_results.append({
                "sample_id": entry.sample_id,
                "status": "not_analyzed",
            })
            continue
        bundle_path = build_export_bundle(
            sample_id=entry.sample_id,
            session_meta=session_meta,
            sample_meta=entry.sample_meta,
            operator=operator_block,
            raw_csv=Path(entry.csv_path),
            predictions=entry.predictions,
            linear=entry.linear or {},
            notes=per_sample_notes.get(entry.sample_id, "") or notes,
            plots=None,
            audit_lines=audit_lines + [
                {"action": "sign", "sample_id": entry.sample_id,
                 "reason": sign_reason},
            ],
            exports_dir=export_dir,
        )
        bundle_paths.append(bundle_path)
        per_sample_results.append({
            "sample_id": entry.sample_id,
            "bundle_name": bundle_path.name,
            "download_url": url_for("download_export",
                                    date=bundle_path.parent.name,
                                    name=bundle_path.name),
            "banner": entry.banner,
            "predictions": entry.predictions,
        })

    # Optional LIMS POST per sample
    lims_results = []
    for entry in samples_snapshot:
        if entry.predictions:
            r = post_to_lims({
                "sample_id": entry.sample_id,
                "predictions": entry.predictions,
                "operator": operator_block,
            })
            lims_results.append({"sample_id": entry.sample_id, **r})
            if _STATE.audit:
                _STATE.audit.event("lims_post",
                                   sample_id=entry.sample_id,
                                   result=r)

    return jsonify({
        "ok": True,
        "saved": per_sample_results,
        "lims": lims_results,
        "n_bundles": len(bundle_paths),
        "export_dir": str(export_dir),
    })


@app.get("/api/exports/<date>/<name>")
def download_export(date: str, name: str):
    safe_date = Path(date).name
    safe_name = Path(name).name
    p = EXPORTS_ROOT / safe_date / safe_name
    if not p.exists():
        return jsonify({"ok": False, "error": "Not found"}), 404
    return send_file(p, as_attachment=True, download_name=safe_name)


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------


@app.post("/api/session/reset_sample")
def api_reset_sample():
    """Clear ONLY the current Phase-1 sample (so a new cryovial can be
    scanned).  Acquired-sample batch and analysis results are preserved."""
    with _STATE_LOCK:
        _STATE.sample_id = None
        _STATE.sample_meta = {}
        _STATE.last_csv_path = None
        if _STATE.audit:
            _STATE.audit.event("reset_sample")
    return jsonify({"ok": True})


@app.post("/api/session/reset_all")
def api_reset_all():
    """Wipe the entire session — current sample, measured-samples batch,
    cancel any in-flight measurement, release the held device connection,
    force the operator back to Phase 0."""
    with _STATE_LOCK:
        try:
            _STATE.cancel_event.set()
        except Exception:
            pass
        _STATE.sample_id = None
        _STATE.sample_meta = {}
        _STATE.last_csv_path = None
        _STATE.measured_samples = []
        if _STATE.audit:
            _STATE.audit.event("session_reset_all")
    # Release the held BLE / USB connection so the next session re-tests
    # cleanly (the BLE bond persists in macOS keychain, so re-connect is
    # fast — typically <5 s once paired).
    _close_connection()
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


DEFAULT_PORT = int(os.environ.get("ECHEM_PORT", "5050"))

# Bind address.  Default = loopback (Mac-only).  Set ECHEM_HOST=0.0.0.0
# to also serve to phones / tablets on the same WiFi network.  See
# README for the security trade-off (anyone on the LAN can then open
# the UI).
DEFAULT_HOST = os.environ.get("ECHEM_HOST", "127.0.0.1")


def _local_ip() -> str:
    """Best-effort guess of the host's LAN IP, for the boot-log hint.

    Tries each common WiFi/Ethernet interface name first (en0/en1 on
    macOS, eth0/wlan0 on Linux) so a VPN like OpenVPN — which would
    otherwise capture the default 8.8.8.8 route and return its tunnel
    IP — doesn't hide the real LAN address that phones on the same
    WiFi can actually reach.
    """
    import socket
    import subprocess

    # 1. Override via env var — if the operator knows their IP, use it.
    forced = os.environ.get("ECHEM_LAN_IP")
    if forced:
        return forced

    # 2. macOS / Linux: query each common interface directly.
    for iface in ("en0", "en1", "en2",
                  "wlan0", "wlp0s20f3", "wlp2s0",
                  "eth0", "eno1"):
        try:
            out = subprocess.check_output(
                ["ipconfig", "getifaddr", iface],
                timeout=1, text=True, stderr=subprocess.DEVNULL).strip()
        except Exception:
            out = ""
        if out and not out.startswith("169.254") and not out.startswith("10.8."):
            return out

    # 3. Fallback: UDP socket trick.  On VPN-laden machines this can
    #    return the tunnel IP — that's why steps (1) and (2) come
    #    first.  We still filter out obviously-tunnel ranges.
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip and not ip.startswith("10.8."):
            return ip
    except Exception:
        pass

    return "your-mac-ip"


def _publish_mdns(port: int):
    """Publish a stable mDNS name so phones can use http://neotrient.local
    instead of a fragile IP address.  Returns the Zeroconf instance so
    the caller can keep it alive (it shuts down on garbage collection).
    """
    if os.environ.get("ECHEM_MDNS", "0") != "1":
        return None
    try:
        import socket
        from zeroconf import Zeroconf, ServiceInfo
    except Exception as exc:
        LOG.info("mDNS unavailable: %s", exc)
        return None
    try:
        ip = _local_ip()
        if not ip or ip == "your-mac-ip":
            return None
        info = ServiceInfo(
            "_http._tcp.local.",
            "Neotrient._http._tcp.local.",
            addresses=[socket.inet_aton(ip)],
            port=port,
            properties={"app": "neotrient-echem"},
            server="neotrient.local.",
        )
        zc = Zeroconf()
        zc.register_service(info, allow_name_change=True)
        LOG.info("mDNS published — phones can also use:  "
                 "http://neotrient.local:%d", port)
        return zc
    except Exception as exc:
        LOG.info("mDNS publish failed: %s", exc)
        return None


def _open_browser():
    try:
        # Mac browser opens to the small "server status" landing — the
        # full operator UI lives on the phone/tablet.  Add ?desktop=1
        # to force the full UI on the same machine if needed.
        webbrowser.open(f"http://127.0.0.1:{DEFAULT_PORT}/server")
    except Exception:
        pass


def main():
    logging.basicConfig(level=logging.INFO,
                        format="[%(asctime)s] [%(name)s] %(message)s")
    if not os.environ.get("ECHEM_NO_BROWSER"):
        from threading import Timer
        Timer(0.8, _open_browser).start()
    LOG.info("Starting Echem app at http://127.0.0.1:%d", DEFAULT_PORT)
    mdns_handle = None
    if DEFAULT_HOST == "0.0.0.0":
        LOG.info("LAN mode enabled — phones / tablets on the same WiFi can "
                 "reach the app at:  http://%s:%d",
                 _local_ip(), DEFAULT_PORT)
        mdns_handle = _publish_mdns(DEFAULT_PORT)
    try:
        app.run(host=DEFAULT_HOST, port=DEFAULT_PORT, debug=False,
                threaded=True)
    finally:
        if mdns_handle is not None:
            try:
                mdns_handle.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
