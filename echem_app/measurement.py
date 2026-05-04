"""Measurement layer — wraps the existing `cv_app` package.

Three responsibilities:

  1. `build_dropdetect_cv_script(params)` — generates a MethodSCRIPT that
     follows PalmSens's official `MSExample016-Drop_detect.mscr` pattern
     (GPIO_D0 enable + GPIO_D1 sense), then runs the CV automatically once
     a drop is detected.

  2. `iter_measurement(...)` — a generator that yields events suitable for
     Server-Sent Events.  Honours a thread-safe `cancel` Event so the
     Flask layer can stop a measurement instantly when the operator clicks
     "Cancel Measurement".

  3. Output-path helpers that build a date-organised, self-describing
     filename per run:
         exports/2026-04-27/sample_DP-0001-V2_Poom_ES4T-01_133045.csv
"""

from __future__ import annotations

import csv
import logging
import re
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Union

# Make sibling cv_app package importable when launched from this directory.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from cv_app.params import CVParameters, Sample
from cv_app.script_builder import (
    _v_to_mV, _vps_to_mV_per_s, _seconds_to_ms, build_cv_script,
)

LOG = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# MethodSCRIPT formatting helpers
# ---------------------------------------------------------------------------

def _format_bandwidth_hz(value) -> str:
    """Format max-bandwidth value for `set_max_bandwidth`.

    PSTrace emits sub-Hz precision via the milli-Hz suffix
    (e.g. 29.253 Hz → `29253m`).  Whole-Hertz values are emitted
    plainly (e.g. 40 → `40`).  Matches PSTrace's own formatting so
    our scripts can be diffed line-by-line.
    """
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    if f == int(f):
        return str(int(f))
    # Fractional value → emit as integer milli-Hz with the "m" suffix.
    return f"{int(round(f * 1000))}m"


def _bandwidth_lines(params, indent: str = "") -> List[str]:
    """Emit `set_max_bandwidth` and the optional
    `set_acquisition_frac_autoadjust` line that PSTrace adds right
    after.  Returns a list of script lines (already indented).
    """
    out = [f"{indent}set_max_bandwidth {_format_bandwidth_hz(params.max_bandwidth_hz)}"]
    afa = getattr(params, "acquisition_frac_autoadjust", None)
    if afa is not None:
        out.append(f"{indent}set_acquisition_frac_autoadjust {int(afa)}")
    return out


# ---------------------------------------------------------------------------
# Output folder + filename helpers
# ---------------------------------------------------------------------------


def _slug(s: str, max_len: int = 24) -> str:
    s = re.sub(r"[^A-Za-z0-9_-]+", "-", (s or "").strip()) or "x"
    return s.strip("-_")[:max_len] or "x"


def date_subfolder(base: Path, *, when: Optional[datetime] = None) -> Path:
    when = when or datetime.now()
    out = Path(base) / when.strftime("%Y-%m-%d")
    out.mkdir(parents=True, exist_ok=True)
    return out


def build_run_filename(*,
                       sample_id: str,
                       operator: str,
                       device_id: str,
                       when: Optional[datetime] = None,
                       extension: str = "csv") -> str:
    when = when or datetime.now()
    return (
        f"sample_{_slug(sample_id)}_"
        f"{_slug(operator)}_{_slug(device_id)}_"
        f"{when.strftime('%H%M%S')}.{extension}"
    )


# ---------------------------------------------------------------------------
# MethodSCRIPT generation
# ---------------------------------------------------------------------------


# Magic marker value emitted by the combined script between drop-detect
# and CV.  Picked to be unique and unlikely to appear in any real
# measurement variable.
DROP_DETECT_MARKER_VALUE = 7777


def build_voltage_dropdetect_cv_script(params: CVParameters,
                                       *,
                                       voltage_threshold_mv: int = 30,
                                       post_drop_settle_s: float = 0.0,
                                       baseline_seconds: int = 1,
                                       max_wait_seconds: int = 300) -> str:
    """One-shot script: run OCP, watch for |ΔV| > threshold, then run CV.

    Works on **any** cell (including the PalmSens dummy cell) because it
    doesn't rely on the drop-detect GPIO contacts on the SPE — instead it
    detects the open-circuit potential shift that happens when liquid
    bridges WE and RE.

    Pattern follows MSExample034-Trigger_on_measured_current.mscr:
      1. Configure cell for OCP (cell must be off during OCP)
      2. Run an OCP loop for `baseline_seconds` to capture the baseline
      3. Run a second OCP loop, comparing each new reading against the
         baseline; on `|p - baseline| > threshold`, set a `detected` flag
         and `breakloop`
      4. If detected: emit marker packet + (optional) wait + CV
      5. If timeout: just end (script terminates without emitting a CV)
    """
    e_begin = _v_to_mV(params.e_begin)
    e_vtx1 = _v_to_mV(params.e_vtx1)
    e_vtx2 = _v_to_mV(params.e_vtx2)
    e_step = _v_to_mV(params.e_step)
    scan_rate = _vps_to_mV_per_s(params.scan_rate)
    nscans_clause = f" nscans({int(params.n_scans)})" if params.n_scans > 1 else ""

    threshold_mv = max(1, int(voltage_threshold_mv))

    lines = [
        "e",
        "var p",
        "var c",
        "var p_baseline",
        "var diff",
        "var pos_threshold",
        "var neg_threshold",
        "var marker",
        "var detected",
        f"store_var pos_threshold {threshold_mv}m ja",
        f"store_var neg_threshold -{threshold_mv}m ja",
        f"store_var marker {DROP_DETECT_MARKER_VALUE}i ja",
        "store_var detected 0i aa",
        # OCP setup (mirrors MSLoopExample011-OCP) ---------------------
        "set_pgstat_chan 0",
        "set_pgstat_mode 3",
        "set_max_bandwidth 60",
        "set_range ab 4",
        "set_autoranging ab 4 4",
        "set_range ba 2m",
        "set_autoranging ba 2m 2m",
        "cell_off",
        # Baseline capture ---------------------------------------------
        f"meas_loop_ocp p_baseline 100m {baseline_seconds}",
        "endloop",
        # Threshold-watch loop ------------------------------------------
        f"meas_loop_ocp p 100m {max_wait_seconds}",
        "    copy_var p diff",
        "    sub_var diff p_baseline",
        "    if diff > pos_threshold",
        "        store_var detected 1i ja",
        "        breakloop",
        "    endif",
        "    if diff < neg_threshold",
        "        store_var detected 1i ja",
        "        breakloop",
        "    endif",
        "endloop",
        # Only emit marker + run CV if a real drop was detected.
        "if detected == 1i",
        "    pck_start",
        "        pck_add marker",
        "    pck_end",
    ]
    if post_drop_settle_s and post_drop_settle_s > 0:
        settle_ms = int(round(post_drop_settle_s * 1000.0))
        lines.append(f"    wait {settle_ms}m")
    # CV setup + loop (indented, inside the if block).
    lines.extend([
        f"    set_pgstat_mode {params.pgstat_mode}",
        *_bandwidth_lines(params, indent="    "),
        f"    set_range_minmax da {e_vtx2} {e_vtx1}",
        f"    set_range ba {params.current_range}",
    ])
    if params.enable_autoranging:
        lines.append(
            f"    set_autoranging ba {params.auto_range_low} {params.auto_range_high}"
        )
    lines.extend([
        f"    set_e {e_begin}",
        "    cell_on",
    ])
    if params.pretreat_duration_s > 0:
        pretreat_potential = _v_to_mV(params.pretreat_potential_v)
        pretreat_interval = _seconds_to_ms(params.pretreat_interval_s)
        pretreat_duration = int(round(params.pretreat_duration_s))
        lines.extend([
            f"    meas_loop_ca p c {pretreat_potential} {pretreat_interval} {pretreat_duration}",
            "    endloop",
        ])
    lines.extend([
        f"    meas_loop_cv p c {e_begin} {e_vtx1} {e_vtx2} {e_step} {scan_rate}{nscans_clause}",
        "        pck_start",
        "            pck_add p",
        "            pck_add c",
        "        pck_end",
        "    endloop",
        "endif",
        "on_finished:",
        "    cell_off",
        "",
    ])
    return "\n".join(lines) + "\n"


def build_combined_dropdetect_cv_script(params: CVParameters,
                                        post_drop_settle_s: float = 0.0) -> str:
    """One-shot script: arm drop-detect, wait, emit marker packet, settle,
    then run CV.  This is the approach that was proven to work with the
    PalmSens dummy cell.

    The marker packet (a single 'ja'-typed variable with value 7777) is
    the signal Python uses to know "drop has fired" — it shows up on the
    serial stream between the GPIO loop terminating and the chip-side
    `wait` for the post-drop settle.  Python emits its own visible
    countdown to the UI in parallel with the chip's wait so the
    operator sees a real countdown that ends just as the sweep begins.
    """
    e_begin = _v_to_mV(params.e_begin)
    e_vtx1 = _v_to_mV(params.e_vtx1)
    e_vtx2 = _v_to_mV(params.e_vtx2)
    e_step = _v_to_mV(params.e_step)
    scan_rate = _vps_to_mV_per_s(params.scan_rate)
    nscans_clause = f" nscans({int(params.n_scans)})" if params.n_scans > 1 else ""

    gpio_out_mask = "0b0001"
    gpio_in_mask = "0b000010"

    lines = [
        "e",
        "var p",
        "var c",
        "var marker",
        "var gpio_out_mask",
        "var gpio_in_mask",
        f"store_var gpio_out_mask {gpio_out_mask} ja",
        f"set_gpio_cfg {gpio_out_mask} 1",
        f"store_var gpio_in_mask {gpio_in_mask} ja",
        f"set_gpio_cfg {gpio_in_mask} 0",
        "set_gpio_msk gpio_out_mask 1i",
        "wait 10m",
        "var dropdetect",
        "store_var dropdetect 2i ja",
        "loop dropdetect == gpio_in_mask",
        "    get_gpio dropdetect",
        "    bit_xor_var dropdetect gpio_in_mask",
        "endloop",
        "set_gpio_msk gpio_out_mask 0i",
        # ----- MARKER: drop has been sensed -----
        f"store_var marker {DROP_DETECT_MARKER_VALUE}i ja",
        "pck_start",
        "    pck_add marker",
        "pck_end",
    ]
    # Chip-side wait for post-drop settle (Python runs its own countdown
    # in parallel so the operator sees the timer tick).
    if post_drop_settle_s and post_drop_settle_s > 0:
        settle_ms = int(round(post_drop_settle_s * 1000.0))
        lines.append(f"wait {settle_ms}m")
    # Now the CV (mirrors build_cv_script with the required pgstat_chan +
    # range_minmax that the chip needs to actually engage the cell).
    lines.extend([
        "set_pgstat_chan 0",
        f"set_pgstat_mode {params.pgstat_mode}",
        *_bandwidth_lines(params),
        f"set_range_minmax da {e_vtx2} {e_vtx1}",
        f"set_range ba {params.current_range}",
    ])
    if params.enable_autoranging:
        lines.append(
            f"set_autoranging ba {params.auto_range_low} {params.auto_range_high}"
        )
    lines.extend([
        f"set_e {e_begin}",
        "cell_on",
    ])
    if params.pretreat_duration_s > 0:
        pretreat_potential = _v_to_mV(params.pretreat_potential_v)
        pretreat_interval = _seconds_to_ms(params.pretreat_interval_s)
        pretreat_duration = int(round(params.pretreat_duration_s))
        lines.extend([
            f"meas_loop_ca p c {pretreat_potential} {pretreat_interval} {pretreat_duration}",
            "endloop",
        ])
    lines.extend([
        f"meas_loop_cv p c {e_begin} {e_vtx1} {e_vtx2} {e_step} {scan_rate}{nscans_clause}",
        "    pck_start",
        "        pck_add p",
        "        pck_add c",
        "    pck_end",
        "endloop",
        "on_finished:",
        "    cell_off",
        "",
    ])
    return "\n".join(lines) + "\n"


def build_dropdetect_only_script() -> str:
    """LEGACY: drop-detect-only script (kept for reference / fallback).

    The active code path uses build_combined_dropdetect_cv_script() with
    a marker packet — that's more robust than splitting into two scripts
    because some chip/firmware combos don't accept a second script
    cleanly right after the first ends.
    """
    gpio_out_mask = "0b0001"   # GPIO_D0 — drop-detect enable
    gpio_in_mask  = "0b000010" # GPIO_D1 — drop-detected signal
    return "\n".join([
        "e",
        "var gpio_out_mask",
        "var gpio_in_mask",
        f"store_var gpio_out_mask {gpio_out_mask} ja",
        f"set_gpio_cfg {gpio_out_mask} 1",
        f"store_var gpio_in_mask {gpio_in_mask} ja",
        f"set_gpio_cfg {gpio_in_mask} 0",
        "set_gpio_msk gpio_out_mask 1i",
        "wait 10m",
        "var dropdetect",
        "store_var dropdetect 2i ja",
        "loop dropdetect == gpio_in_mask",
        "    get_gpio dropdetect",
        "    bit_xor_var dropdetect gpio_in_mask",
        "endloop",
        "set_gpio_msk gpio_out_mask 0i",
        "",
    ]) + "\n"


def build_dropdetect_cv_script(params: CVParameters,
                               post_drop_settle_s: float = 0.0) -> str:
    """LEGACY: combined drop-detect + CV in one script.

    Kept for reference / backwards-compat.  iter_measurement no longer
    uses this — it sends `build_dropdetect_only_script()` followed by
    `build_cv_script()` so the post-drop settle can be timed by Python
    and shown in the UI.

    `post_drop_settle_s` inserts a `wait Nm` after the GPIO loop.
    """
    e_begin = _v_to_mV(params.e_begin)
    e_vtx1 = _v_to_mV(params.e_vtx1)
    e_vtx2 = _v_to_mV(params.e_vtx2)
    e_step = _v_to_mV(params.e_step)
    scan_rate = _vps_to_mV_per_s(params.scan_rate)
    nscans_clause = f" nscans({int(params.n_scans)})" if params.n_scans > 1 else ""

    gpio_out_mask = "0b0001"   # GPIO_D0 — drop-detect enable (EmStat4T)
    gpio_in_mask  = "0b000010" # GPIO_D1 — drop-detected signal

    lines = [
        "e",
        "var p",
        "var c",
        "var gpio_out_mask",
        "var gpio_in_mask",
        f"store_var gpio_out_mask {gpio_out_mask} ja",
        f"set_gpio_cfg {gpio_out_mask} 1",
        f"store_var gpio_in_mask {gpio_in_mask} ja",
        f"set_gpio_cfg {gpio_in_mask} 0",
        "# Enable drop-detect circuit",
        "set_gpio_msk gpio_out_mask 1i",
        "wait 10m",
        "# Wait for drop detection (EmStat4T uses '==' termination)",
        "var dropdetect",
        "store_var dropdetect 2i ja",
        "loop dropdetect == gpio_in_mask",
        "    get_gpio dropdetect",
        "    bit_xor_var dropdetect gpio_in_mask",
        "endloop",
        "# Drop detected; disable drop-detect circuit",
        "set_gpio_msk gpio_out_mask 0i",
    ]
    # Optional settle time after the drop is detected and before the CV
    # begins.  Implemented in the MethodSCRIPT itself so timing is exact.
    if post_drop_settle_s and post_drop_settle_s > 0:
        settle_ms = int(round(post_drop_settle_s * 1000.0))
        lines.extend([
            f"# Settle after drop ({post_drop_settle_s} s)",
            f"wait {settle_ms}m",
        ])
    lines.extend([
        "# CV after drop-detect",
        "set_pgstat_chan 0",
        f"set_pgstat_mode {params.pgstat_mode}",
        *_bandwidth_lines(params),
        f"set_range_minmax da {e_vtx2} {e_vtx1}",
        f"set_range ba {params.current_range}",
    ])
    if params.enable_autoranging:
        lines.append(
            f"set_autoranging ba {params.auto_range_low} {params.auto_range_high}"
        )
    lines.extend([
        f"set_e {e_begin}",
        "cell_on",
    ])
    if params.pretreat_duration_s > 0:
        pretreat_potential = _v_to_mV(params.pretreat_potential_v)
        pretreat_interval = _seconds_to_ms(params.pretreat_interval_s)
        pretreat_duration = int(round(params.pretreat_duration_s))
        lines.extend([
            "# Equilibration after drop",
            f"meas_loop_ca p c {pretreat_potential} {pretreat_interval} {pretreat_duration}",
            "endloop",
        ])
    lines.extend([
        f"meas_loop_cv p c {e_begin} {e_vtx1} {e_vtx2} {e_step} {scan_rate}{nscans_clause}",
        "    pck_start",
        "        pck_add p",
        "        pck_add c",
        "    pck_end",
        "endloop",
        "on_finished:",
        "    cell_off",
        "",
    ])
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Streaming iterator for the Flask SSE endpoint
# ---------------------------------------------------------------------------


@dataclass
class MeasurementOptions:
    params: CVParameters
    sample_id: str
    operator: str
    device_id: str
    output_root: Path
    use_drop_detect: bool = True
    drop_detect_method: str = "voltage"   # "voltage" | "gpio"
    voltage_threshold_mv: int = 30
    port: Optional[str] = None
    baudrate: Optional[int] = None
    # If supplied, iter_measurement uses this pre-opened connection
    # instead of creating + opening + closing a new one.  Caller is
    # responsible for the connection's lifetime.
    held_connection: Any = None
    # Timing (from preset)
    start_countdown_s: int = 0     # UI countdown before the script is sent
    post_drop_settle_s: int = 0    # in-script wait after drop-detect fires
    # Replay mode (development without hardware) — kept for internal/dev use,
    # NOT exposed in the operator UI per requirements.
    replay_path: Optional[Path] = None
    replay_pace_s: float = 0.05
    cancel_event: Optional[threading.Event] = None


def _save_csv(path: Path, samples: List[Sample], opts: MeasurementOptions):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        fh.write("# experiment=CV\n")
        fh.write(f"# operator={opts.operator}\n")
        fh.write(f"# device_id={opts.device_id}\n")
        fh.write(f"# sample_id={opts.sample_id}\n")
        fh.write(f"# generated_at={datetime.now().isoformat()}\n")
        for k, v in asdict(opts.params).items():
            fh.write(f"# {k}={v}\n")
        w = csv.writer(fh)
        w.writerow([
            "index", "scan", "potential_V", "current_A", "status", "current_range"
        ])
        for s in samples:
            w.writerow([
                s.index, s.scan, s.potential_v, s.current_a,
                s.status or "", s.current_range or "",
            ])


def _resolve_csv_path(opts: MeasurementOptions) -> Path:
    folder = date_subfolder(opts.output_root)
    name = build_run_filename(
        sample_id=opts.sample_id,
        operator=opts.operator,
        device_id=opts.device_id,
    )
    return folder / name


def _cancelled(opts: MeasurementOptions) -> bool:
    return bool(opts.cancel_event and opts.cancel_event.is_set())


def iter_measurement(opts: MeasurementOptions) -> Iterator[Dict[str, Any]]:
    """Yield JSON-friendly events for SSE.

    Event vocabulary the frontend listens for:
        drop_armed, drop_detected, sample, scan_complete,
        finished, cancelled, error
    """
    csv_path = _resolve_csv_path(opts)
    samples: List[Sample] = []

    # ---------- shared: pre-script start countdown ----------------------
    # Pre-script countdown.  Only emitted in MANUAL mode — in drop-detect
    # mode the JS already runs a Phase-2 standby for the same duration, so
    # emitting it here would double-count and prematurely navigate the
    # operator to Phase 3 before they've dropped the sample.
    if (opts.start_countdown_s and opts.start_countdown_s > 0
            and not opts.use_drop_detect):
        for remaining in range(int(opts.start_countdown_s), 0, -1):
            if _cancelled(opts):
                yield {"event": "cancelled", "n_samples": 0}
                return
            yield {"event": "countdown", "remaining_s": remaining,
                   "phase": "pre_script"}
            time.sleep(1.0)

    # ---------- replay mode (dev only) ----------------------------------
    if opts.replay_path is not None:
        from cv_app.runner import replay_samples_from_file
        # Mirror hardware flow: drop_armed (with the real use_drop_detect
        # flag so the UI behaves correctly) → simulated detection delay →
        # drop_detected → post-drop countdown → samples.
        yield {"event": "drop_armed",
               "use_drop_detect": opts.use_drop_detect,
               "message": "Replay mode."}
        time.sleep(0.2)
        if _cancelled(opts):
            yield {"event": "cancelled"}
            return
        yield {"event": "drop_detected"}
        if opts.post_drop_settle_s and opts.post_drop_settle_s > 0:
            for remaining in range(int(opts.post_drop_settle_s), 0, -1):
                if _cancelled(opts):
                    yield {"event": "cancelled", "n_samples": 0}
                    return
                yield {"event": "countdown", "remaining_s": remaining,
                       "phase": "post_drop"}
                time.sleep(1.0)
        last_scan = -1
        for s in replay_samples_from_file(opts.replay_path,
                                          pace_s=opts.replay_pace_s):
            if _cancelled(opts):
                yield {"event": "cancelled", "n_samples": len(samples)}
                _save_csv(csv_path, samples, opts)  # save partial
                return
            samples.append(s)
            if s.scan != last_scan and last_scan >= 0:
                yield {"event": "scan_complete", "scan": last_scan}
            last_scan = s.scan
            yield {
                "event": "sample",
                "potential_v": s.potential_v,
                "current_a": s.current_a,
                "index": s.index,
                "scan": s.scan,
                "status": s.status,
                "range": s.current_range,
            }
        if last_scan >= 0:
            yield {"event": "scan_complete", "scan": last_scan}
        _save_csv(csv_path, samples, opts)
        yield {"event": "finished",
               "n_samples": len(samples),
               "csv_path": str(csv_path)}
        return

    # ---------- hardware mode -------------------------------------------
    try:
        from cv_app.device import DeviceConnection
    except ImportError as exc:
        yield {"event": "error",
               "message": f"Could not import device layer: {exc}"}
        return

    # CV body for both modes — for manual mode we may prepend a wait so
    # the operator's "post_drop_settle_s" still applies as a settle time
    # at the start of the sweep.
    cv_script = build_cv_script(opts.params)
    if (not opts.use_drop_detect
            and opts.post_drop_settle_s and opts.post_drop_settle_s > 0):
        ms = int(round(opts.post_drop_settle_s * 1000.0))
        cv_script = cv_script.replace("cell_on\n",
                                      f"cell_on\nwait {ms}m\n", 1)

    # Per-cycle path length used to derive the cycle index from the
    # cumulative |Δp|.  A standard CV cycle traces e_begin → e_vtx1 →
    # e_vtx2 → e_begin, so the path length is the sum of those three legs.
    per_cycle_path = (
        abs(opts.params.e_vtx1 - opts.params.e_begin)
        + abs(opts.params.e_vtx2 - opts.params.e_vtx1)
        + abs(opts.params.e_begin - opts.params.e_vtx2)
    )
    cumulative_path = 0.0
    last_potential_for_path: Optional[float] = None

    # Acquire the device connection.  If the caller pre-opened one (typical
    # in production where Begin session opens it once and reuses it for
    # every measurement) we borrow it without owning it.
    owns_connection = False
    if opts.held_connection is not None:
        dev = opts.held_connection
        LOG.info("Reusing held connection (%s)", type(dev).__name__)
    else:
        if opts.port and opts.port.startswith("ble://"):
            from .ble_transport import BleDeviceConnection
            ble_name = opts.port[len("ble://"):]
            dev = BleDeviceConnection(ble_name)
        else:
            dev = DeviceConnection(port=opts.port, baudrate=opts.baudrate)
        try:
            dev.open()
        except Exception as exc:
            yield {"event": "error", "message": f"Could not open device: {exc}"}
            return
        owns_connection = True

    try:
            # ------------- DROP-DETECT MODE -------------
            # ONE combined script: GPIO loop → marker packet → wait → CV.
            # Python detects the marker packet to know when the drop fires
            # and emits a visible countdown that runs in parallel with the
            # chip's own `wait` for the post-drop settle.
            #
            # We chose the combined-script approach over splitting because
            # some EmStat4T firmware revisions don't reliably accept a
            # second script right after the first one terminates — the
            # CV script would be sent but never executed, leaving the
            # operator stuck after the post-drop countdown.
            if opts.use_drop_detect:
                if opts.drop_detect_method == "gpio":
                    script = build_combined_dropdetect_cv_script(
                        opts.params,
                        post_drop_settle_s=opts.post_drop_settle_s,
                    )
                    LOG.info("Sending GPIO drop-detect + CV MethodSCRIPT (%d bytes)",
                             len(script))
                else:   # default = voltage
                    script = build_voltage_dropdetect_cv_script(
                        opts.params,
                        voltage_threshold_mv=int(opts.voltage_threshold_mv or 30),
                        post_drop_settle_s=opts.post_drop_settle_s,
                    )
                    LOG.info("Sending voltage-threshold drop-detect + CV "
                             "MethodSCRIPT (%d bytes, threshold=%d mV)",
                             len(script), opts.voltage_threshold_mv)
                yield {"event": "drop_armed",
                       "device_type": dev.device_type_str,
                       "use_drop_detect": True,
                       "drop_detect_method": opts.drop_detect_method}
                dev.send_script_text(script)
            else:
                # MANUAL MODE — straight to CV (with optional script-side
                # settle wait already inserted above).
                LOG.info("Sending CV MethodSCRIPT (%d bytes)", len(cv_script))
                yield {"event": "drop_armed",
                       "device_type": dev.device_type_str,
                       "use_drop_detect": False}
                dev.send_script_text(cv_script)

            seen_first_sample = False
            seen_drop_marker = False
            last_scan = -1
            index = 0
            for pkg in dev.iter_data_packages():
                if _cancelled(opts):
                    try:
                        dev.device.abort_and_sync()
                    except Exception:
                        pass
                    _save_csv(csv_path, samples, opts)
                    yield {"event": "cancelled", "n_samples": len(samples)}
                    return

                # Intercept the drop-detect marker packet (one variable of
                # type 'ja' with our magic value).  Emit drop_detected and
                # tick the post-drop countdown in parallel with the chip's
                # own wait, then continue reading sample packets.
                if (opts.use_drop_detect and not seen_drop_marker
                        and len(pkg) == 1
                        and pkg[0].type.id == 'ja'
                        and abs(pkg[0].raw_value - DROP_DETECT_MARKER_VALUE) < 1):
                    seen_drop_marker = True
                    yield {"event": "drop_detected"}
                    for remaining in range(int(opts.post_drop_settle_s), 0, -1):
                        if _cancelled(opts):
                            try: dev.device.abort_and_sync()
                            except Exception: pass
                            _save_csv(csv_path, samples, opts)
                            yield {"event": "cancelled", "n_samples": len(samples)}
                            return
                        yield {"event": "countdown",
                               "remaining_s": remaining,
                               "phase": "post_drop"}
                        time.sleep(1.0)
                    continue   # marker is not a sample

                potential = current = None
                status = current_range = None
                for var in pkg:
                    if potential is None and var.type.unit == "V":
                        potential = var.value
                    elif current is None and var.type.unit == "A":
                        current = var.value
                        if "status" in var.metadata:
                            from palmsens.mscript import metadata_status_to_text  # type: ignore
                            status = metadata_status_to_text(var.metadata["status"])
                        if "range" in var.metadata:
                            from palmsens.mscript import metadata_range_to_text  # type: ignore
                            current_range = metadata_range_to_text(
                                dev.device_type_str, var.type, var.metadata["range"])
                if potential is None or current is None:
                    continue

                if not seen_first_sample:
                    seen_first_sample = True
                    # In manual mode the first sample marks "started";
                    # drop-detect mode already emitted drop_detected before
                    # the countdown, so we don't double-emit here.
                    if not opts.use_drop_detect:
                        yield {"event": "drop_detected"}

                # Cycle index from cumulative path length.  Robust against
                # PalmSens CV ordering (e_begin → e_vtx1 → e_vtx2 → e_begin)
                # because we don't depend on which point is "near e_begin".
                if last_potential_for_path is not None:
                    cumulative_path += abs(potential - last_potential_for_path)
                last_potential_for_path = potential
                if per_cycle_path > 0 and opts.params.n_scans > 1:
                    scan = min(int(opts.params.n_scans) - 1,
                               int(cumulative_path // per_cycle_path))
                else:
                    scan = 0

                s = Sample(
                    potential_v=potential, current_a=current,
                    index=index, scan=scan,
                    status=status, current_range=current_range,
                )
                samples.append(s)

                if last_scan >= 0 and scan != last_scan:
                    yield {"event": "scan_complete", "scan": last_scan}
                last_scan = scan

                yield {
                    "event": "sample",
                    "potential_v": potential,
                    "current_a": current,
                    "index": index,
                    "scan": scan,
                    "status": status,
                    "range": current_range,
                }
                index += 1
    except Exception as exc:
        LOG.exception("Measurement failed")
        yield {"event": "error", "message": str(exc)}
        return
    finally:
        # Close the connection only if we created it for this measurement.
        # Connections held by the caller (e.g. opened on Begin session)
        # are left alone.
        if owns_connection:
            try: dev.close()
            except Exception: pass

    if last_scan >= 0:
        yield {"event": "scan_complete", "scan": last_scan}

    _save_csv(csv_path, samples, opts)
    yield {"event": "finished",
           "n_samples": len(samples),
           "csv_path": str(csv_path)}


# ---------------------------------------------------------------------------
# Helper to materialise CVParameters from a preset record
# ---------------------------------------------------------------------------


def cv_params_from_preset(preset: Dict[str, Any]) -> CVParameters:
    cv = (preset or {}).get("cv") or {}
    p = CVParameters()
    for k, v in cv.items():
        if hasattr(p, k):
            try:
                # Cast to the dataclass field's type when feasible
                cur = getattr(p, k)
                if isinstance(cur, bool):
                    setattr(p, k, bool(v))
                elif isinstance(cur, int) and not isinstance(cur, bool):
                    setattr(p, k, int(v))
                elif isinstance(cur, float):
                    setattr(p, k, float(v))
                else:
                    setattr(p, k, v)
            except (ValueError, TypeError):
                pass
    return p
