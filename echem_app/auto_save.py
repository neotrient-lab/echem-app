"""auto_save.py — automatic measurement persistence.

Every completed CV / SWV measurement is written to disk immediately, with
no "Save" button needed.  Files land under:

    ~/Documents/Neotrient/results/YYYY-MM-DD/

A new dated folder is created on the first measurement of the day.  If
nobody runs anything on a given day, no folder exists for that day.

Each measurement produces three files (timestamped + sample-id'd):

    HHMMSS_<sample_id>.csv       self-describing data (header + per-point)
    HHMMSS_<sample_id>.json      params + metadata sidecar (kept for
                                 backwards compat with v0.1.x tooling)
    log.txt                      append-only daily log of every run

CSV format (v0.2.0+) — self-describing, one technique per file:

    # experiment=CV
    # operator=neo-001
    # device_id=ES4T-01
    # sample_id=DI-1
    # generated_at=2026-05-06T16:53:33.361604
    # e_begin=1.0
    # ...full preset parameter dump...
    # status=ok
    # notes=
    index,potential_V,current_A,cycle
    0,0.999977,8.66e-04,1
    ...

For SWV the columns become:
    index,potential_V,current_fwd_A,current_rev_A,current_diff_A
and the metadata header carries SWV-specific keys (frequency_hz,
e_amplitude, e_end, e_dep, t_dep, t_equil instead of CV's vertices /
scan_rate / pretreat_*).

All paths are absolute and OS-independent.  Override the base folder
via the ECHEM_RESULTS_DIR environment variable if needed.
"""
from __future__ import annotations

import csv
import json
import logging
import os
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Sequence

LOG = logging.getLogger(__name__)
_LOG_LOCK = threading.Lock()


def _safe_filename(text: str, maxlen: int = 40) -> str:
    """Sanitize an arbitrary string into a filename-safe slug."""
    if not text:
        return "unknown"
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(text)).strip("_")
    return (cleaned or "unknown")[:maxlen]


def get_base_dir() -> Path:
    """Top-level results folder.  Default = ~/Documents/Neotrient/results.

    Override with ECHEM_RESULTS_DIR (e.g. for institutional shared drives)."""
    override = os.environ.get("ECHEM_RESULTS_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / "Documents" / "Neotrient" / "results"


def get_today_dir(create: bool = True) -> Path:
    """Return today's folder, creating it lazily on first call of the day."""
    folder = get_base_dir() / datetime.now().strftime("%Y-%m-%d")
    if create:
        folder.mkdir(parents=True, exist_ok=True)
    return folder


def append_log(metadata: Dict[str, Any]) -> None:
    """Append one event line to today's log.txt.

    `metadata` should include at minimum the keys: operator, sample_id,
    preset_name, status.  Extra keys are flattened into key=value pairs."""
    folder = get_today_dir()
    log_path = folder / "log.txt"
    ts = datetime.now().isoformat(timespec="seconds")

    pieces = [f"[{ts}]"]
    # Pin the most useful fields first, in a stable order
    for k in ("operator", "sample_id", "preset_name", "status"):
        if k in metadata:
            pieces.append(f"{k}={metadata[k]}")
    # Then everything else
    for k, v in metadata.items():
        if k in {"operator", "sample_id", "preset_name", "status"}:
            continue
        if isinstance(v, (dict, list)):
            v = json.dumps(v, default=str)
        pieces.append(f"{k}={v}")

    line = "  ".join(pieces) + "\n"
    with _LOG_LOCK:
        with log_path.open("a", encoding="utf-8") as f:
            f.write(line)


# ---------------------------------------------------------------------------
# CSV header / data column layout (v0.2.0+)
# ---------------------------------------------------------------------------
# Order of metadata keys in the `# key=value` header block.  Any key NOT
# in this list is appended at the end in dict order, so adding new
# parameters doesn't require updating the writer.  The order shown here
# is what an operator naturally reads top-down: identification, then
# sweep, then chip config, then status.
_HEADER_KEY_ORDER_CV = [
    "experiment", "operator", "device_id", "sample_id", "generated_at",
    "e_begin", "e_vtx1", "e_vtx2", "e_step", "scan_rate", "n_scans",
    "pretreat_potential_v", "pretreat_duration_s", "pretreat_interval_s",
    "pgstat_mode", "max_bandwidth_hz", "acquisition_frac_autoadjust",
    "current_range", "auto_range_low", "auto_range_high", "enable_autoranging",
    "cell_on_settle_s",
    "status", "notes",
]
_HEADER_KEY_ORDER_SWV = [
    "experiment", "operator", "device_id", "sample_id", "generated_at",
    "e_begin", "e_end", "e_step", "frequency_hz", "e_amplitude",
    "e_dep", "t_dep", "t_equil",
    "pgstat_mode", "max_bandwidth_hz", "acquisition_frac_autoadjust",
    "current_range", "auto_range_low", "auto_range_high", "enable_autoranging",
    "cell_on_settle_s",
    "status", "notes",
]

# Internal-only metadata keys — these don't belong in the CSV header
# (they're paths, sidecars, or housekeeping) but stay in the JSON sidecar
# and the daily log.txt.
_HEADER_HIDDEN_KEYS = {"saved_at", "csv", "json", "folder", "log",
                       "preset_name", "save_error", "raw_packets"}


def _format_value(v: Any) -> str:
    """Stringify a metadata value for the `# key=value` header line.

    Booleans → `True` / `False` (Python style — matches the user-supplied
    example).  Floats → plain repr (no exponent collapsing).  Everything
    else → str().  Newlines are stripped so a stray newline in `notes`
    can't corrupt the header.
    """
    if v is None:
        return ""
    s = str(v)
    return s.replace("\r", " ").replace("\n", " ")


def _write_metadata_header(f, metadata: Dict[str, Any], technique: str) -> None:
    """Emit `# key=value` lines for every metadata key in canonical order.

    `technique` is "CV" or "SWV" — drives which key order is used as
    the canonical preamble.  Keys not in the canonical list are still
    written, just after the canonical block in dict order.
    """
    order = _HEADER_KEY_ORDER_SWV if technique.upper() == "SWV" else _HEADER_KEY_ORDER_CV
    seen: set = set()
    for k in order:
        if k in metadata:
            f.write(f"# {k}={_format_value(metadata[k])}\n")
            seen.add(k)
    # Then any extras (preserves forward-compat for new fields)
    for k, v in metadata.items():
        if k in seen or k in _HEADER_HIDDEN_KEYS:
            continue
        f.write(f"# {k}={_format_value(v)}\n")


def save_measurement(
    *,
    sample_id: str,
    operator: str,
    preset_name: str = "",
    technique: str = "CV",
    cv_points: Optional[Iterable[Sequence[float]]] = None,
    swv_points: Optional[Iterable[Sequence[float]]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    status: str = "ok",
) -> Dict[str, str]:
    """Write a CV or SWV measurement to today's folder + log it.

    Pass `cv_points` (default) for CV runs — accepts iterables of
    (V, I) or (V, I, cycle).  Pass `swv_points` for SWV runs — accepts
    iterables of (V, I_fwd, I_rev) or (V, I_fwd, I_rev, I_diff).
    The `technique` parameter labels the run for the metadata header
    and selects the column layout.

    Returns a dict with paths the caller can echo to the UI:
        { "csv": "...", "json": "...", "folder": "...", "log": "..." }

    Failures never raise — the measurement should not be lost just
    because the disk is full.  Errors are logged to the application
    logger and the returned paths may be empty.
    """
    technique = (technique or "CV").upper()
    metadata = dict(metadata or {})
    metadata.setdefault("experiment",  technique)
    metadata.setdefault("sample_id",   sample_id)
    metadata.setdefault("operator",    operator)
    metadata.setdefault("preset_name", preset_name)
    metadata.setdefault("status",      status)
    # `generated_at` is what surfaces in the CSV header; `saved_at`
    # is the older internal sidecar key kept for backwards compat.
    now_iso = datetime.now().isoformat()
    metadata["generated_at"] = now_iso
    metadata["saved_at"]     = datetime.now().isoformat(timespec="seconds")

    paths = {"csv": "", "json": "", "folder": "", "log": ""}
    try:
        folder = get_today_dir()
        paths["folder"] = str(folder)
        paths["log"]    = str(folder / "log.txt")

        ts = datetime.now().strftime("%H%M%S")
        slug = _safe_filename(sample_id)
        base = folder / f"{ts}_{slug}"

        # ------------------------------------------------------------------
        # CSV — only if we actually have data.
        # Header: `# key=value` block, then a single `index,...` row, then
        # one row per data point.  csv.writer doesn't quote unless needed,
        # which matches the manual-readable shape the operator expects.
        # ------------------------------------------------------------------
        if technique == "SWV" and swv_points is not None:
            csv_path = base.with_suffix(".csv")
            with csv_path.open("w", newline="", encoding="utf-8") as f:
                _write_metadata_header(f, metadata, technique)
                w = csv.writer(f)
                w.writerow(["index", "potential_V",
                            "current_fwd_A", "current_rev_A", "current_diff_A"])
                for i, pt in enumerate(swv_points):
                    # Accept (V, I_fwd, I_rev) or (V, I_fwd, I_rev, I_diff)
                    if len(pt) >= 4:
                        v, ifwd, irev, idiff = pt[0], pt[1], pt[2], pt[3]
                    else:
                        v, ifwd, irev = pt[0], pt[1], pt[2]
                        try:
                            idiff = float(ifwd) - float(irev)
                        except (TypeError, ValueError):
                            idiff = ""
                    w.writerow([i, v, ifwd, irev, idiff])
            paths["csv"] = str(csv_path)

        elif cv_points is not None:
            csv_path = base.with_suffix(".csv")
            with csv_path.open("w", newline="", encoding="utf-8") as f:
                _write_metadata_header(f, metadata, technique)
                w = csv.writer(f)
                w.writerow(["index", "potential_V", "current_A", "cycle"])
                for i, pt in enumerate(cv_points):
                    # accept (V, I) or (V, I, cycle)
                    cycle = pt[2] if len(pt) > 2 else ""
                    w.writerow([i, pt[0], pt[1], cycle])
            paths["csv"] = str(csv_path)

        # JSON metadata sidecar — kept for v0.1.x backwards compat
        # (downstream tools may still read it).  The CSV is now self-
        # describing, so this file is redundant for new tooling.
        json_path = base.with_suffix(".json")
        with json_path.open("w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, default=str)
        paths["json"] = str(json_path)

        # Append to daily log
        log_meta = {**metadata, "csv": paths["csv"], "json": paths["json"]}
        append_log(log_meta)

        LOG.info("Auto-saved %s measurement to %s", technique, folder)
    except Exception as exc:
        LOG.warning("Auto-save failed: %s", exc, exc_info=True)
        # Still try to log the attempt so we don't lose the record
        try:
            append_log({**metadata, "save_error": str(exc)})
        except Exception:
            pass
    return paths


def log_session_event(operator: str, event: str, **extra) -> None:
    """Convenience: log a non-measurement event (session start/cancel,
    connection lost, error recovery, etc.) to today's log.txt."""
    try:
        append_log({"operator": operator, "event": event, **extra})
    except Exception:
        LOG.debug("log_session_event failed", exc_info=True)
