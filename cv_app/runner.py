"""High-level streaming runners for CV and SWV.

Two flavours of API are exposed:

  - `iter_samples_cv(params, ...)`  / `iter_samples_swv(params, ...)`
        Generators that yield `Sample` objects one by one.  Use these when you
        want to wire the stream into a custom UI, save samples to a file,
        forward them to an ML model, etc.

  - `run_cv(params, ...)` / `run_swv(params, ...)`
        Convenience wrappers that block until the run finishes, optionally
        show a live plot, and return the full list of samples (and a small
        run-summary dict).

Both work in two modes:

  - Hardware mode (default): connect to the EmStat4/EmStat4T over USB and run
    the experiment.

  - Replay mode (`replay=Path("cv_result.txt")`): re-emit samples from an
    existing PSTrace-style text dump, which is invaluable for offline
    development and for testing the live-plot / analysis pipeline without
    the device attached.
"""

from __future__ import annotations

import csv
import logging
import re
import time
from dataclasses import asdict
from pathlib import Path
from typing import Callable, Iterable, Iterator, List, Optional, Union

from .params import CVParameters, SWVParameters, Sample
from .script_builder import build_cv_script, build_swv_script

LOG = logging.getLogger(__name__)


SampleCallback = Callable[[Sample], None]


# ---------------------------------------------------------------------------
# Offline replay
# ---------------------------------------------------------------------------


_REPLAY_RE = re.compile(
    r"Applied potential\s*=\s*([+-]?[0-9.eE+-]+)\s*V\s*\|\s*"
    r"WE current\s*=\s*([+-]?[0-9.eE+-]+)\s*A"
    r"(?:\s*\|\s*STATUS:\s*([^|\n]+?))?"
    r"(?:\s*\|\s*range:\s*([^|\n]+))?"
    r"\s*$"
)


def replay_samples_from_file(path: Union[str, Path],
                             pace_s: float = 0.0) -> Iterator[Sample]:
    """Yield Sample objects parsed from a console_example-style text file.

    `pace_s` injects a small sleep between samples to simulate the device
    streaming rate when feeding the live plot.
    """
    path = Path(path)
    with path.open("r", encoding="utf-8") as fh:
        index = 0
        for line in fh:
            m = _REPLAY_RE.search(line)
            if not m:
                continue
            potential = float(m.group(1))
            current = float(m.group(2))
            status = m.group(3).strip() if m.group(3) else None
            current_range = m.group(4).strip() if m.group(4) else None
            yield Sample(
                potential_v=potential,
                current_a=current,
                index=index,
                scan=0,
                status=status,
                current_range=current_range,
            )
            index += 1
            if pace_s > 0:
                time.sleep(pace_s)


# ---------------------------------------------------------------------------
# CV streaming
# ---------------------------------------------------------------------------


def _stream_cv_from_device(params: CVParameters,
                           port: Optional[str],
                           baudrate: Optional[int]) -> Iterator[Sample]:
    # Imported lazily so users can run replay/build flows without pyserial.
    from .device import DeviceConnection

    script = build_cv_script(params)
    LOG.debug("Generated CV MethodSCRIPT:\n%s", script)

    with DeviceConnection(port=port, baudrate=baudrate) as dev:
        LOG.info("Device: %s, FW %s, S/N %s",
                 dev.info.device_type, dev.info.firmware_version,
                 dev.info.serial_number)
        dev.send_script_text(script)

        # CV data packages from the example script have two variables:
        # var p (Applied potential, "da") and var c (WE current, "ba").
        index = 0
        scan = 0
        last_potential: Optional[float] = None
        for pkg in dev.iter_data_packages():
            potential = None
            current = None
            status = None
            current_range = None
            for var in pkg:
                vt = var.type
                if vt.id == "da" or (vt.unit == "V" and potential is None):
                    potential = var.value
                elif vt.id == "ba" or (vt.unit == "A" and current is None):
                    current = var.value
                    if "status" in var.metadata:
                        from palmsens.mscript import metadata_status_to_text  # type: ignore
                        status = metadata_status_to_text(var.metadata["status"])
                    if "range" in var.metadata:
                        from palmsens.mscript import metadata_range_to_text   # type: ignore
                        current_range = metadata_range_to_text(
                            dev.device_type_str, vt, var.metadata["range"]
                        )
            if potential is None or current is None:
                continue

            # Heuristic: a new scan begins when the direction reverses and
            # we're back near e_begin.  Mostly cosmetic, used for colouring.
            if last_potential is not None and params.n_scans > 1:
                if (
                    abs(potential - params.e_begin) < params.e_step
                    and abs(last_potential - params.e_begin) >= params.e_step
                ):
                    scan += 1
            last_potential = potential

            yield Sample(
                potential_v=potential,
                current_a=current,
                index=index,
                scan=scan,
                status=status,
                current_range=current_range,
            )
            index += 1


def iter_samples_cv(params: CVParameters,
                    *,
                    port: Optional[str] = None,
                    baudrate: Optional[int] = None,
                    replay: Optional[Union[str, Path]] = None,
                    replay_pace_s: float = 0.03) -> Iterator[Sample]:
    """Yield CV samples either from the device or from a text replay file."""
    if replay is not None:
        LOG.warning(
            "*** REPLAY MODE *** — reading old data from %s, NOT measuring "
            "the EmStat4T.  Drop a different sample, you'll see the same "
            "curve.  Remove --replay to do a real measurement.", replay,
        )
        yield from replay_samples_from_file(replay, pace_s=replay_pace_s)
    else:
        LOG.info("HARDWARE MODE — talking to the EmStat4T over USB.")
        yield from _stream_cv_from_device(params, port, baudrate)


# ---------------------------------------------------------------------------
# SWV streaming
# ---------------------------------------------------------------------------


def _stream_swv_from_device(params: SWVParameters,
                            port: Optional[str],
                            baudrate: Optional[int]) -> Iterator[Sample]:
    from .device import DeviceConnection

    script = build_swv_script(params)
    LOG.debug("Generated SWV MethodSCRIPT:\n%s", script)

    with DeviceConnection(port=port, baudrate=baudrate) as dev:
        LOG.info("Device: %s, FW %s, S/N %s",
                 dev.info.device_type, dev.info.firmware_version,
                 dev.info.serial_number)
        dev.send_script_text(script)

        index = 0
        for pkg in dev.iter_data_packages():
            potential = None
            current = None
            current_fwd = None
            current_rev = None
            status = None
            current_range = None
            # SWV packages contain p, c, f, r in that order
            for n, var in enumerate(pkg):
                vt = var.type
                if n == 0:
                    potential = var.value
                elif n == 1:
                    current = var.value
                    if "status" in var.metadata:
                        from palmsens.mscript import metadata_status_to_text  # type: ignore
                        status = metadata_status_to_text(var.metadata["status"])
                    if "range" in var.metadata:
                        from palmsens.mscript import metadata_range_to_text   # type: ignore
                        current_range = metadata_range_to_text(
                            dev.device_type_str, vt, var.metadata["range"]
                        )
                elif n == 2:
                    current_fwd = var.value
                elif n == 3:
                    current_rev = var.value
            if potential is None or current is None:
                continue
            yield Sample(
                potential_v=potential,
                current_a=current,
                index=index,
                scan=0,
                status=status,
                current_range=current_range,
                current_fwd_a=current_fwd,
                current_rev_a=current_rev,
            )
            index += 1


def iter_samples_swv(params: SWVParameters,
                     *,
                     port: Optional[str] = None,
                     baudrate: Optional[int] = None,
                     replay: Optional[Union[str, Path]] = None,
                     replay_pace_s: float = 0.03) -> Iterator[Sample]:
    """Yield SWV samples either from the device or from a text replay file."""
    if replay is not None:
        LOG.warning(
            "*** REPLAY MODE *** — reading old data from %s, NOT measuring.",
            replay,
        )
        yield from replay_samples_from_file(replay, pace_s=replay_pace_s)
    else:
        LOG.info("HARDWARE MODE — talking to the EmStat4T over USB.")
        yield from _stream_swv_from_device(params, port, baudrate)


# ---------------------------------------------------------------------------
# Blocking convenience runners
# ---------------------------------------------------------------------------


def _save_csv(path: Path, samples: List[Sample], params: object, kind: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        # Top header: parameter dump as comments
        fh.write(f"# experiment={kind}\n")
        for k, v in (asdict(params) if hasattr(params, "__dataclass_fields__") else {}).items():
            fh.write(f"# {k}={v}\n")
        writer = csv.writer(fh)
        writer.writerow(
            ["index", "scan", "potential_V", "current_A",
             "current_fwd_A", "current_rev_A", "status", "current_range"]
        )
        for s in samples:
            writer.writerow([
                s.index, s.scan, s.potential_v, s.current_a,
                s.current_fwd_a if s.current_fwd_a is not None else "",
                s.current_rev_a if s.current_rev_a is not None else "",
                s.status or "",
                s.current_range or "",
            ])


def run_cv(params: CVParameters,
           *,
           port: Optional[str] = None,
           baudrate: Optional[int] = None,
           on_sample: Optional[SampleCallback] = None,
           live_plot: bool = True,
           save_csv: Optional[Union[str, Path]] = None,
           replay: Optional[Union[str, Path]] = None,
           replay_pace_s: float = 0.03) -> dict:
    """Run a CV experiment end-to-end and return a result dict.

    Returns: ``{"samples": [...], "params": {...}, "device": "..."}``.
    """
    title = "CV (REPLAY — not measuring)" if replay else "CV — live measurement"
    return _run_generic(
        kind="CV",
        params=params,
        sample_iter=iter_samples_cv(
            params, port=port, baudrate=baudrate,
            replay=replay, replay_pace_s=replay_pace_s,
        ),
        on_sample=on_sample,
        live_plot=live_plot,
        save_csv=save_csv,
        plot_title=title,
    )


def run_swv(params: SWVParameters,
            *,
            port: Optional[str] = None,
            baudrate: Optional[int] = None,
            on_sample: Optional[SampleCallback] = None,
            live_plot: bool = True,
            save_csv: Optional[Union[str, Path]] = None,
            replay: Optional[Union[str, Path]] = None,
            replay_pace_s: float = 0.03) -> dict:
    """Run an SWV experiment end-to-end and return a result dict."""
    title = "SWV (REPLAY — not measuring)" if replay else "SWV — live measurement"
    return _run_generic(
        kind="SWV",
        params=params,
        sample_iter=iter_samples_swv(
            params, port=port, baudrate=baudrate,
            replay=replay, replay_pace_s=replay_pace_s,
        ),
        on_sample=on_sample,
        live_plot=live_plot,
        save_csv=save_csv,
        plot_title=title,
    )


def _run_generic(*,
                 kind: str,
                 params,
                 sample_iter: Iterable[Sample],
                 on_sample: Optional[SampleCallback],
                 live_plot: bool,
                 save_csv: Optional[Union[str, Path]],
                 plot_title: Optional[str] = None) -> dict:
    samples: List[Sample] = []

    plot = None
    if live_plot:
        # Imported lazily so headless / unit-test runs don't need matplotlib.
        from .liveplot import LivePlot
        plot = LivePlot(title=plot_title or f"{kind} measurement",
                        xlabel="Potential (V)",
                        ylabel="Current (A)")
        plot.start()

    try:
        for sample in sample_iter:
            samples.append(sample)
            if on_sample is not None:
                on_sample(sample)
            if plot is not None:
                plot.add_sample(sample)
    finally:
        if plot is not None:
            plot.finish()

    if save_csv is not None:
        _save_csv(Path(save_csv), samples, params, kind)
        LOG.info("Saved %d samples to %s", len(samples), save_csv)

    return {
        "kind": kind,
        "samples": samples,
        "params": params.to_dict(),
        "n_samples": len(samples),
    }
