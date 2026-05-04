"""Parameter dataclasses for electrochemistry experiments.

All values are expressed in SI base units (Volts, Volts/second, Amps, Hertz,
seconds) so that callers can think in physics, not in PalmSens's "milli"
text format.  The script builder takes care of converting to the device units.

Every parameter has a sane default, so a user who is happy with the defaults
can just call:

    run_cv(CVParameters())

and immediately get a measurement.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


# ---------------------------------------------------------------------------
# Data sample produced by the runner
# ---------------------------------------------------------------------------


@dataclass
class Sample:
    """A single (potential, current) reading streamed from the device.

    Extra fields capture optional information the device sends along with
    each data package (status flags, current range, scan index for multi-
    scan CV, time, etc.).  They are all `None` if the device did not send
    them.
    """

    potential_v: float
    current_a: float
    index: int = 0
    scan: int = 0
    time_s: Optional[float] = None
    status: Optional[str] = None
    current_range: Optional[str] = None
    # SWV-specific (forward / reverse currents)
    current_fwd_a: Optional[float] = None
    current_rev_a: Optional[float] = None


# ---------------------------------------------------------------------------
# Cyclic Voltammetry
# ---------------------------------------------------------------------------


@dataclass
class CVParameters:
    """Parameters for a Cyclic Voltammetry (CV) experiment.

    All voltages are in V; scan_rate in V/s; currents in A.

    The vendor MethodSCRIPT command signature for reference:
        meas_loop_cv p c E_begin E_vtx1 E_vtx2 E_step scan_rate [nscans(N)]
    """

    # Sweep definition (in Volts)
    e_begin: float = 0.0          # starting potential
    e_vtx1: float = 0.5           # first vertex
    e_vtx2: float = -0.5          # second vertex
    e_step: float = 0.01          # step size (V)
    scan_rate: float = 0.1        # V / s
    n_scans: int = 1              # number of scans

    # Equilibration (chronoamperometry hold) before the CV starts
    pretreat_potential_v: float = 0.0
    pretreat_duration_s: float = 3.0
    pretreat_interval_s: float = 0.1

    # Hardware configuration
    pgstat_mode: int = 2          # 2 = Low-current / standard 3-electrode
                                  # 3 = High impedance (matches PSTrace's auto-pick)
    max_bandwidth_hz: float = 40.0  # analog low-pass filter cutoff (Hz)
                                    # (PSTrace uses 29.253 Hz in auto-mode)
    # PSTrace emits `set_acquisition_frac_autoadjust 50` after the
    # bandwidth command — this tells the firmware to auto-shift WHEN
    # within each potential step the sample is taken.  We add an
    # optional knob with the same default value so we can match
    # PSTrace's behaviour byte-for-byte.  Set to None to omit the
    # command entirely (matches the pre-0.1.14 behaviour).
    acquisition_frac_autoadjust: Optional[int] = None
    current_range: str = "100u"   # default range as a MethodSCRIPT string
    auto_range_low: str = "1n"    # autoranging low limit
    auto_range_high: str = "100u" # autoranging high limit
    enable_autoranging: bool = True

    # Optional: hold the cell on at e_begin briefly before pretreatment
    cell_on_settle_s: float = 0.0

    # Free-form notes the caller can attach (saved with the run)
    notes: str = ""

    # Estimate how many samples we expect — handy for live plotting buffers
    @property
    def estimated_samples(self) -> int:
        # CV goes e_begin -> e_vtx1 -> e_vtx2 -> e_begin (one cycle)
        path = (
            abs(self.e_vtx1 - self.e_begin)
            + abs(self.e_vtx2 - self.e_vtx1)
            + abs(self.e_begin - self.e_vtx2)
        )
        per_scan = max(1, int(round(path / max(self.e_step, 1e-9))))
        return per_scan * max(1, self.n_scans)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Square Wave Voltammetry
# ---------------------------------------------------------------------------


@dataclass
class SWVParameters:
    """Parameters for a Square Wave Voltammetry (SWV) experiment.

    Vendor MethodSCRIPT command signature for reference:
        meas_loop_swv p c f r E_begin E_end E_step E_amp freq
    """

    # Sweep definition (in Volts)
    e_begin: float = -0.3
    e_end: float = 0.3
    e_step: float = 0.005          # 5 mV
    e_amplitude: float = 0.05      # 50 mV peak amplitude
    frequency_hz: int = 5          # square-wave frequency

    # Optional reverse sweep (set False for a single forward SWV)
    do_reverse_sweep: bool = False

    # Equilibration / pretreatment
    pretreat_potential_v: Optional[float] = None  # default = e_begin
    pretreat_duration_s: float = 2.0
    pretreat_interval_s: float = 0.1

    # Hardware configuration
    pgstat_mode: int = 2
    max_bandwidth_hz: int = 40
    current_range: str = "100u"
    auto_range_low: str = "1n"
    auto_range_high: str = "100u"
    enable_autoranging: bool = True

    # Output potential range hint
    e_range_min: float = -0.3
    e_range_max: float = 0.4

    notes: str = ""

    @property
    def estimated_samples(self) -> int:
        per_sweep = max(1, int(round(abs(self.e_end - self.e_begin) / max(self.e_step, 1e-9))))
        return per_sweep * (2 if self.do_reverse_sweep else 1)

    def to_dict(self) -> dict:
        return asdict(self)
