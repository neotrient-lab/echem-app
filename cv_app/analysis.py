"""Lightweight analysis helpers for CV / SWV data.

The goal of this module is twofold:

  1. Give you a useful starter set of features for "marker detection" — peak
     potentials, peak currents, charge under the curve, etc.
  2. Provide a stable feature dict that your future ML model can consume
     directly.  When you swap in your trained model, you'll just import it
     here and add a `predict_marker(features)` call.

The functions accept either a list of `Sample` objects or plain numpy arrays.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np

from .params import Sample

# NumPy 2.0 renamed np.trapz to np.trapezoid; keep working on both.
_trapz = getattr(np, "trapezoid", getattr(np, "trapz", None))


# ---------------------------------------------------------------------------
# Conversion helpers
# ---------------------------------------------------------------------------


def samples_to_arrays(samples: Iterable[Sample]) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return potential, current, scan arrays from a stream of Samples."""
    pot, cur, scn = [], [], []
    for s in samples:
        pot.append(s.potential_v)
        cur.append(s.current_a)
        scn.append(s.scan)
    return np.asarray(pot), np.asarray(cur), np.asarray(scn, dtype=int)


# ---------------------------------------------------------------------------
# Smoothing + derivative
# ---------------------------------------------------------------------------


def moving_average(y: np.ndarray, window: int = 5) -> np.ndarray:
    """Simple centered moving-average smoother (edges padded with same value)."""
    if window <= 1:
        return y.copy()
    window = int(window)
    if window % 2 == 0:
        window += 1
    pad = window // 2
    padded = np.pad(y, pad, mode="edge")
    kernel = np.ones(window) / window
    return np.convolve(padded, kernel, mode="valid")


# ---------------------------------------------------------------------------
# Peak detection
# ---------------------------------------------------------------------------


@dataclass
class Peak:
    index: int
    potential_v: float
    current_a: float
    polarity: str  # "anodic" or "cathodic"


def find_peaks_simple(potential_v: np.ndarray,
                      current_a: np.ndarray,
                      smooth_window: int = 9,
                      min_prominence_a: Optional[float] = None) -> List[Peak]:
    """Return anodic (max) and cathodic (min) peaks of a voltammogram.

    Uses a basic local-extremum check on the smoothed signal; no scipy
    dependency.  If `scipy` is installed the caller can switch to
    `scipy.signal.find_peaks` for more sophisticated detection — this is just
    a sane default so the pipeline works out of the box.
    """
    if len(current_a) < 5:
        return []

    y_smooth = moving_average(current_a, smooth_window)

    if min_prominence_a is None:
        # Default prominence threshold: 5% of the signal range.
        signal_range = float(np.nanmax(y_smooth) - np.nanmin(y_smooth))
        min_prominence_a = 0.05 * signal_range if signal_range > 0 else 0.0

    peaks: List[Peak] = []
    for i in range(2, len(y_smooth) - 2):
        # local max
        if (y_smooth[i] > y_smooth[i - 1] > y_smooth[i - 2]
                and y_smooth[i] > y_smooth[i + 1] > y_smooth[i + 2]):
            prominence = y_smooth[i] - min(y_smooth.min(), y_smooth[i - 2])
            if prominence >= min_prominence_a:
                peaks.append(Peak(i, float(potential_v[i]), float(current_a[i]),
                                  "anodic"))
        # local min
        elif (y_smooth[i] < y_smooth[i - 1] < y_smooth[i - 2]
                and y_smooth[i] < y_smooth[i + 1] < y_smooth[i + 2]):
            prominence = max(y_smooth.max(), y_smooth[i - 2]) - y_smooth[i]
            if prominence >= min_prominence_a:
                peaks.append(Peak(i, float(potential_v[i]), float(current_a[i]),
                                  "cathodic"))
    return peaks


# ---------------------------------------------------------------------------
# Feature extraction (the dict your ML model will consume)
# ---------------------------------------------------------------------------


def basic_features(samples: List[Sample]) -> Dict[str, Any]:
    """Compute a baseline feature dict from a CV / SWV run.

    The keys are deliberately stable so a downstream ML model can consume them
    without renaming.  Add domain-specific features (e.g. peak separation for
    quasi-reversible markers, charge integration for capacitive markers) as
    your sensor characterization matures.
    """
    if not samples:
        return {"n_samples": 0}

    pot, cur, scn = samples_to_arrays(samples)
    peaks = find_peaks_simple(pot, cur)

    anodic = [p for p in peaks if p.polarity == "anodic"]
    cathodic = [p for p in peaks if p.polarity == "cathodic"]

    features: Dict[str, Any] = {
        "n_samples": int(len(samples)),
        "n_scans": int(scn.max()) + 1 if len(scn) else 0,
        "potential_min_v": float(np.nanmin(pot)),
        "potential_max_v": float(np.nanmax(pot)),
        "current_min_a": float(np.nanmin(cur)),
        "current_max_a": float(np.nanmax(cur)),
        "current_mean_a": float(np.nanmean(cur)),
        "current_std_a": float(np.nanstd(cur)),
        # area under |I| approximates total charge (very rough — Simpson's
        # rule would be better for proper coulometry).
        "auc_abs_a_v": float(_trapz(np.abs(cur), pot)) if _trapz else 0.0,
        "n_peaks_anodic": len(anodic),
        "n_peaks_cathodic": len(cathodic),
    }

    if anodic:
        top = max(anodic, key=lambda p: p.current_a)
        features["anodic_peak_potential_v"] = top.potential_v
        features["anodic_peak_current_a"] = top.current_a
    if cathodic:
        top = min(cathodic, key=lambda p: p.current_a)
        features["cathodic_peak_potential_v"] = top.potential_v
        features["cathodic_peak_current_a"] = top.current_a

    if anodic and cathodic:
        features["peak_separation_v"] = abs(
            features["anodic_peak_potential_v"]
            - features["cathodic_peak_potential_v"]
        )

    return features


# ---------------------------------------------------------------------------
# ML hook (placeholder)
# ---------------------------------------------------------------------------


def predict_marker(features: Dict[str, Any]) -> Dict[str, Any]:
    """Placeholder for your trained model.

    Replace the body with `joblib.load(...)` (or torch / TF / sklearn
    equivalent) and return a dict like {"marker": "...", "score": 0.97}.
    Today this just returns a dummy structure so the rest of the pipeline can
    be wired up immediately.
    """
    score = 0.0
    if "anodic_peak_current_a" in features and "cathodic_peak_current_a" in features:
        score = min(1.0, abs(features["anodic_peak_current_a"]) * 1e6)
    return {
        "model": "placeholder-v0",
        "marker": "unknown",
        "score": score,
        "features_used": sorted(features.keys()),
    }
