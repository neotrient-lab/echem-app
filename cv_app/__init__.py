"""cv_app — high-level Python API for the PalmSens EmStat4 / EmStat4T.

This package wraps the low-level MethodSCRIPT serial protocol (provided in the
vendor `palmsens` package shipped in MethodSCRIPT_Examples-master) and exposes:

  - Parameter dataclasses (`CVParameters`, `SWVParameters`)
  - On-the-fly MethodSCRIPT generation (no hand-edited .mscr files)
  - Streaming runner that yields (potential, current) samples as they arrive
  - Live matplotlib plotting (PSTrace-style voltammogram window)
  - An offline replay mode so the whole pipeline can be tested without hardware
  - Hooks for analysis / ML feature extraction and for a future drop-detect
    auto-trigger
"""

from .params import CVParameters, SWVParameters, Sample
from .runner import (
    run_cv,
    run_swv,
    iter_samples_cv,
    iter_samples_swv,
    replay_samples_from_file,
)
from .liveplot import LivePlot
from .device import find_device, DeviceConnection
from .analysis import basic_features, find_peaks_simple

__all__ = [
    "CVParameters",
    "SWVParameters",
    "Sample",
    "run_cv",
    "run_swv",
    "iter_samples_cv",
    "iter_samples_swv",
    "replay_samples_from_file",
    "LivePlot",
    "find_device",
    "DeviceConnection",
    "basic_features",
    "find_peaks_simple",
]

__version__ = "0.1.0"
