"""Generate MethodSCRIPT text from parameter dataclasses.

PalmSens MethodSCRIPT uses unit-suffixed numbers ("500m" = 0.5, "100u" = 100e-6,
"1n" = 1e-9) and the various commands need parameters in the device's preferred
unit (mostly milli-volts and milli-volts/second for CV).  This module hides
those conversions behind two functions:

    build_cv_script(params) -> str
    build_swv_script(params) -> str

The returned string can be sent verbatim to the device with
`Instrument.write(text)` (followed by a newline).  Useful for testing the
generator without the hardware: just print the result.
"""

from __future__ import annotations

import math

from .params import CVParameters, SWVParameters


def _v_to_mV(value_v: float) -> str:
    """Format a voltage in V as a MethodSCRIPT integer-millivolt string.

    MethodSCRIPT accepts decimal numbers with SI prefixes (e.g. "500m", "1500m",
    "-300m").  We round to the nearest integer mV which is what the EmStat
    expects for CV / SWV sweep parameters.
    """
    mv = int(round(value_v * 1000.0))
    return f"{mv}m"


def _vps_to_mV_per_s(value_vps: float) -> str:
    """Format scan rate in V/s as a MethodSCRIPT mV/s string."""
    mvps = int(round(value_vps * 1000.0))
    return f"{mvps}m"


def _seconds_to_ms(value_s: float) -> str:
    """Format seconds as MethodSCRIPT milliseconds string."""
    ms = int(round(value_s * 1000.0))
    return f"{ms}m"


def _format_bandwidth_hz(value) -> str:
    """Format a bandwidth value for `set_max_bandwidth`.

    PalmSens MethodSCRIPT does NOT accept fractional Hz directly
    (e.g. `set_max_bandwidth 29.253` is silently rejected by the chip,
    which then never starts the script).  Sub-Hz precision must be
    expressed via the milli-Hz suffix:  29.253 Hz → `29253m`.

    Whole-Hz values are emitted plainly (`40` → `40`).  Matches PSTrace's
    own formatting so generated scripts can be diffed line-by-line.
    """
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    if f == int(f):
        return str(int(f))
    return f"{int(round(f * 1000))}m"


def _bandwidth_lines(params, indent: str = ""):
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
# CV
# ---------------------------------------------------------------------------


def build_cv_script(params: CVParameters) -> str:
    """Return a MethodSCRIPT program for a CV experiment from `params`."""

    e_begin = _v_to_mV(params.e_begin)
    e_vtx1 = _v_to_mV(params.e_vtx1)
    e_vtx2 = _v_to_mV(params.e_vtx2)
    e_step = _v_to_mV(params.e_step)
    scan_rate = _vps_to_mV_per_s(params.scan_rate)

    pretreat_potential = _v_to_mV(params.pretreat_potential_v)
    pretreat_interval = _seconds_to_ms(params.pretreat_interval_s)
    pretreat_duration = int(round(params.pretreat_duration_s))  # whole seconds

    nscans_clause = f" nscans({int(params.n_scans)})" if params.n_scans > 1 else ""

    autorange_lines = []
    if params.enable_autoranging:
        autorange_lines.append(
            f"set_autoranging ba {params.auto_range_low} {params.auto_range_high}"
        )

    # NOTE: set_pgstat_chan + set_range_minmax da are required for the CV
    # to actually run on EmStat4T.  Without them the chip selects no
    # channel (no cell engagement) and/or the DAC clips before reaching
    # the vertex potentials, so the script stalls silently.  These were
    # missing in earlier versions and caused a regression when the
    # combined drop-detect+CV script was split into two pieces in
    # echem_app/measurement.py.
    lines = [
        "e",
        "var c",
        "var p",
        "set_pgstat_chan 0",
        f"set_pgstat_mode {params.pgstat_mode}",
        *_bandwidth_lines(params),
        f"set_range_minmax da {e_vtx2} {e_vtx1}",
        f"set_range ba {params.current_range}",
        *autorange_lines,
        f"set_e {e_begin}",
        "cell_on",
    ]

    if params.cell_on_settle_s > 0:
        lines.append(f"wait {_seconds_to_ms(params.cell_on_settle_s)}")

    if params.pretreat_duration_s > 0:
        lines.extend(
            [
                "# Equilibration (chronoamperometry) before CV",
                (
                    f"meas_loop_ca p c {pretreat_potential} "
                    f"{pretreat_interval} {pretreat_duration}"
                ),
                "endloop",
            ]
        )

    lines.extend(
        [
            "# E_begin, E_vtx1, E_vtx2, E_step, scan_rate"
            + (f" (with nscans={params.n_scans})" if params.n_scans > 1 else ""),
            (
                f"meas_loop_cv p c {e_begin} {e_vtx1} {e_vtx2} {e_step} {scan_rate}"
                f"{nscans_clause}"
            ),
            "\tpck_start",
            "\tpck_add p",
            "\tpck_add c",
            "\tpck_end",
            "endloop",
            "on_finished:",
            "cell_off",
            "",
        ]
    )

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# SWV
# ---------------------------------------------------------------------------


def build_swv_script(params: SWVParameters) -> str:
    """Return a MethodSCRIPT program for a SWV experiment from `params`."""

    e_begin = _v_to_mV(params.e_begin)
    e_end = _v_to_mV(params.e_end)
    e_step = _v_to_mV(params.e_step)
    e_amp = _v_to_mV(params.e_amplitude)
    freq = int(params.frequency_hz)

    pretreat_pot_v = (
        params.pretreat_potential_v
        if params.pretreat_potential_v is not None
        else params.e_begin
    )
    pretreat_potential = _v_to_mV(pretreat_pot_v)
    pretreat_interval = _seconds_to_ms(params.pretreat_interval_s)
    pretreat_duration = int(round(params.pretreat_duration_s))

    e_range_min = _v_to_mV(params.e_range_min)
    e_range_max = _v_to_mV(params.e_range_max)

    autorange_lines = []
    if params.enable_autoranging:
        autorange_lines.append(
            f"set_autoranging ba {params.auto_range_low} {params.auto_range_high}"
        )

    # Deposition step (optional): held at e_dep for t_dep seconds, sampled
    # every pretreat_interval_s.  Skipped when t_dep <= 0.
    e_dep_mv = _v_to_mV(getattr(params, "e_dep", 0.0))
    t_dep_s = int(round(getattr(params, "t_dep", 0.0)))

    lines = [
        "e",
        "var c",
        "var p",
        "var f",
        "var r",
        f"set_pgstat_chan 0",
        f"set_pgstat_mode {params.pgstat_mode}",
        *_bandwidth_lines(params),
        f"set_range_minmax da {e_range_min} {e_range_max}",
        f"set_range ba {params.current_range}",
        *autorange_lines,
        # PSTrace holds the deposition / equilibration potential before
        # turning the cell on, so the cell engages already at e_dep
        # (or e_begin if no deposition).  Mirrors the CV builder's set_e.
        f"set_e {e_dep_mv if t_dep_s > 0 else _v_to_mV(pretreat_pot_v)}",
        "cell_on",
    ]

    if getattr(params, "cell_on_settle_s", 0) > 0:
        lines.append(f"wait {_seconds_to_ms(params.cell_on_settle_s)}")

    if t_dep_s > 0:
        lines.extend(
            [
                "# Deposition step (analyte accumulation at e_dep)",
                (
                    f"meas_loop_ca p c {e_dep_mv} "
                    f"{pretreat_interval} {t_dep_s}"
                ),
                "endloop",
            ]
        )

    if params.pretreat_duration_s > 0:
        lines.extend(
            [
                "# Equilibration (chronoamperometry) before SWV",
                (
                    f"meas_loop_ca p c {pretreat_potential} "
                    f"{pretreat_interval} {pretreat_duration}"
                ),
                "endloop",
            ]
        )

    # Forward SWV sweep
    lines.extend(
        [
            "# Forward SWV: E, I, I_fwd, I_rev",
            f"meas_loop_swv p c f r {e_begin} {e_end} {e_step} {e_amp} {freq}",
            "\tpck_start",
            "\tpck_add p",
            "\tpck_add c",
            "\tpck_add f",
            "\tpck_add r",
            "\tpck_end",
            "endloop",
        ]
    )

    if params.do_reverse_sweep:
        lines.extend(
            [
                "# Reverse SWV",
                f"meas_loop_swv p c f r {e_end} {e_begin} {e_step} {e_amp} {freq}",
                "\tpck_start",
                "\tpck_add p",
                "\tpck_add c",
                "\tpck_add f",
                "\tpck_add r",
                "\tpck_end",
                "endloop",
            ]
        )

    lines.extend(
        [
            "on_finished:",
            "cell_off",
            "",
        ]
    )

    return "\n".join(lines) + "\n"
