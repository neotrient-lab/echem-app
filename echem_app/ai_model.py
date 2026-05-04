"""AI inference layer driven by the model registry (`config_store.models()`).

A "model" record in the registry has shape:

    {
      "id":      "model_<...>",
      "name":    "ResAttn v3.2",
      "kind":    "torch" | "sklearn" | "linear",
      "path":    "/abs/path/to/file.pt"      # ignored for "linear"
      "version": "v3.2",
      "ions":    [{"symbol": "Cu", "name": "Copper", "lod_mg_l": 0.030}, ...],
      "linear_coefficients": {"Cu": [a, b], ...}      # only for kind="linear"
      "notes":   "..."
    }

Important: **each model declares the ions it predicts**.  The UI builds the
result grid from `model["ions"]`, so adding a new model that predicts a
different ion set just works.

For PyTorch / sklearn models, your model file should be packaged as a dict
or a tuple containing the model object and a metadata block, e.g.:

    torch.save({"model": net.state_dict(), "metadata": {...}}, "resattn.pt")

If your existing pickle / .pt is just the bare model with no metadata, the
loader will accept it but the registry record's `ions` list is the source of
truth either way — so the operator can register a model + describe its
ions through the UI.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from . import config_store

LOG = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Output dataclasses
# ---------------------------------------------------------------------------


@dataclass
class IonPrediction:
    ion: str
    name: str
    value_mg_l: float
    ci_low_mg_l: float
    ci_high_mg_l: float
    below_lod: bool = False
    lod_mg_l: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ion": self.ion,
            "name": self.name,
            "value_mg_l": round(self.value_mg_l, 5),
            "ci_low_mg_l": round(self.ci_low_mg_l, 5),
            "ci_high_mg_l": round(self.ci_high_mg_l, 5),
            "below_lod": self.below_lod,
            "lod_mg_l": self.lod_mg_l,
        }


@dataclass
class ModelPrediction:
    model_id: str
    model_name: str
    model_version: str
    kind: str
    using_stub: bool
    predictions: List[IonPrediction] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model_id": self.model_id,
            "model": self.model_name,
            "version": self.model_version,
            "kind": self.kind,
            "using_stub": self.using_stub,
            "predictions": [p.to_dict() for p in self.predictions],
        }


# ---------------------------------------------------------------------------
# Loader cache
# ---------------------------------------------------------------------------


_LOADED: Dict[str, Dict[str, Any]] = {}  # id -> {"model": obj, "metadata": {...}}


def _load_file(path: Path, kind: str) -> Dict[str, Any]:
    """Load a model file, returning ``{"model": obj, "metadata": {...}}``.

    Supports two packaging styles:
      - bare model (just torch.save(model_obj) or joblib.dump(model_obj))
      - dict { "model": ..., "metadata": ... } (recommended)
    """
    if kind == "torch":
        import torch  # type: ignore
        obj = torch.load(path, map_location="cpu")
        if isinstance(obj, dict) and "model" in obj:
            return {"model": obj["model"], "metadata": obj.get("metadata", {})}
        return {"model": obj, "metadata": {}}
    if kind == "sklearn":
        import joblib  # type: ignore
        obj = joblib.load(path)
        if isinstance(obj, dict) and "model" in obj:
            return {"model": obj["model"], "metadata": obj.get("metadata", {})}
        return {"model": obj, "metadata": {}}
    raise ValueError(f"Unknown model kind: {kind}")


def _ensure_loaded(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Lazy-load a registry record's underlying model file (if any)."""
    rid = record["id"]
    if rid in _LOADED:
        return _LOADED[rid]
    if record.get("kind") == "linear":
        _LOADED[rid] = {"model": None, "metadata": record}
        return _LOADED[rid]
    path = Path(record.get("path") or "")
    if not path.exists():
        LOG.warning("Model file %s not found for %s — falling back to stub.",
                    path, record.get("name"))
        _LOADED[rid] = None
        return None
    try:
        loaded = _load_file(path, record["kind"])
        _LOADED[rid] = loaded
        return loaded
    except Exception as exc:
        LOG.exception("Failed to load model %s: %s", path, exc)
        _LOADED[rid] = None
        return None


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def list_models() -> List[Dict[str, Any]]:
    """Models for the picker — adds an `available` flag."""
    out = []
    for rec in config_store.models().list_all():
        rec = dict(rec)
        if rec.get("kind") == "linear":
            rec["available"] = True
        else:
            rec["available"] = bool(rec.get("path") and Path(rec["path"]).exists())
        out.append(rec)
    return out


def model_by_id(model_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not model_id:
        return config_store.models().get_default()
    return config_store.models().get(model_id)


def detect_kind(path: Path) -> str:
    """Guess `kind` from a file extension."""
    s = path.suffix.lower()
    if s in (".pt", ".pth"):
        return "torch"
    if s in (".pkl", ".joblib"):
        return "sklearn"
    return "linear"


def register_model_from_file(*,
                             name: str,
                             path: str,
                             ions: Optional[List[Dict[str, Any]]] = None,
                             notes: str = "",
                             set_default: bool = False) -> Dict[str, Any]:
    """Register a new model. If `ions` is None, try to read metadata from
    the file; otherwise use the seed (Cu, Pb, Cd, Zn, Fe, Hg)."""
    p = Path(path).expanduser()
    kind = detect_kind(p)
    metadata_ions = None
    if kind in ("torch", "sklearn") and p.exists():
        try:
            loaded = _load_file(p, kind)
            metadata_ions = (loaded.get("metadata") or {}).get("ions")
        except Exception as exc:
            LOG.warning("Could not introspect %s for ion metadata: %s", p, exc)

    final_ions = ions or metadata_ions or [
        {"symbol": "Cu", "name": "Copper",  "lod_mg_l": 0.030},
        {"symbol": "Pb", "name": "Lead",    "lod_mg_l": 0.0001},
        {"symbol": "Cd", "name": "Cadmium", "lod_mg_l": 5e-5},
        {"symbol": "Zn", "name": "Zinc",    "lod_mg_l": 0.150},
        {"symbol": "Fe", "name": "Iron",    "lod_mg_l": 0.040},
        {"symbol": "Hg", "name": "Mercury", "lod_mg_l": 1e-5},
    ]
    rec = {
        "name": name or p.stem,
        "kind": kind,
        "path": str(p) if p.exists() or kind != "linear" else "",
        "version": p.name if p.exists() else "v0",
        "ions": final_ions,
        "notes": notes,
    }
    return config_store.models().add(rec, set_default=set_default)


# ---------------------------------------------------------------------------
# Prediction
# ---------------------------------------------------------------------------


def _features_to_signal(features: Dict[str, Any]) -> float:
    """Reduce the feature dict to a single scalar a linear model can use.

    Today: max-amplitude peak current.  Replace once you ship a real linear
    calibration (per-ion slope/intercept already captured in
    `record["linear_coefficients"]`).
    """
    a = abs(features.get("anodic_peak_current_a", 0.0))
    c = abs(features.get("cathodic_peak_current_a", 0.0))
    return a + c


def _linear_predict(record: Dict[str, Any],
                    features: Dict[str, Any]) -> ModelPrediction:
    coeffs = record.get("linear_coefficients") or {}
    signal = _features_to_signal(features)
    preds: List[IonPrediction] = []
    for ion in record.get("ions", []):
        sym = ion["symbol"]
        # y = intercept + slope * signal_in_amps_per_ion_baseline
        c = coeffs.get(sym, [0.0, 0.0])
        intercept, slope = (c + [0.0, 0.0])[:2]
        v = max(0.0, intercept + slope * signal)
        preds.append(IonPrediction(
            ion=sym,
            name=ion.get("name", sym),
            value_mg_l=v,
            ci_low_mg_l=v * 0.7,
            ci_high_mg_l=v * 1.3,
            lod_mg_l=ion.get("lod_mg_l", 0.0),
            below_lod=v < ion.get("lod_mg_l", 0.0),
        ))
    return ModelPrediction(
        model_id=record["id"],
        model_name=record["name"],
        model_version=record.get("version", "linear-v0"),
        kind="linear",
        using_stub=False,
        predictions=preds,
    )


def _stub_predict(record: Dict[str, Any],
                  features: Dict[str, Any],
                  sample_id: str = "") -> ModelPrediction:
    """Deterministic plausible-numbers stub keyed on sample_id + features."""
    seed_src = (record["id"] + sample_id +
                json.dumps(features, sort_keys=True, default=str)).encode()
    seed = int.from_bytes(hashlib.sha256(seed_src).digest()[:8], "big")
    rng = np.random.default_rng(seed)

    preds: List[IonPrediction] = []
    for ion in record.get("ions", []):
        sym = ion["symbol"]
        lod = ion.get("lod_mg_l", 0.001)
        # plausible window — 1..50× LOD
        v = float(rng.uniform(lod, max(lod * 50, lod * 5)))
        unc = v * float(rng.uniform(0.05, 0.15))
        preds.append(IonPrediction(
            ion=sym,
            name=ion.get("name", sym),
            value_mg_l=v,
            ci_low_mg_l=max(0.0, v - unc),
            ci_high_mg_l=v + unc,
            lod_mg_l=lod,
            below_lod=v < lod,
        ))
    return ModelPrediction(
        model_id=record["id"],
        model_name=record["name"] + " (stub: file missing)",
        model_version=record.get("version", "stub"),
        kind=record.get("kind", "stub"),
        using_stub=True,
        predictions=preds,
    )


def _real_predict(record: Dict[str, Any],
                  loaded: Dict[str, Any],
                  features: Dict[str, Any]) -> ModelPrediction:
    """Run a torch / sklearn model from the registry.

    The model's input shape is treated generically: a flat feature vector
    in sorted-key order from `basic_features`.  Adjust this once your real
    training pipeline pins down the exact input layout.
    """
    feature_keys = sorted(features.keys())
    X = np.array([[features.get(k, 0.0) for k in feature_keys]],
                 dtype=np.float32)
    model = loaded["model"]

    if record["kind"] == "torch":
        import torch  # type: ignore
        with torch.no_grad():
            tensor = torch.from_numpy(X)
            out = model(tensor) if callable(model) else model.predict(tensor)
            arr = out.detach().cpu().numpy().reshape(-1)
    else:
        out = model.predict(X)
        arr = np.asarray(out).reshape(-1)

    preds: List[IonPrediction] = []
    for i, ion in enumerate(record.get("ions", [])):
        sym = ion["symbol"]
        v = float(arr[i]) if i < len(arr) else 0.0
        preds.append(IonPrediction(
            ion=sym,
            name=ion.get("name", sym),
            value_mg_l=v,
            ci_low_mg_l=v * 0.9,
            ci_high_mg_l=v * 1.1,
            lod_mg_l=ion.get("lod_mg_l", 0.0),
            below_lod=v < ion.get("lod_mg_l", 0.0),
        ))
    return ModelPrediction(
        model_id=record["id"],
        model_name=record["name"],
        model_version=record.get("version", "v0"),
        kind=record["kind"],
        using_stub=False,
        predictions=preds,
    )


def predict(model_id: Optional[str],
            features: Dict[str, Any],
            sample_id: str = "") -> ModelPrediction:
    rec = model_by_id(model_id)
    if rec is None:
        # No models registered at all — degrade gracefully
        return ModelPrediction(
            model_id="",
            model_name="(no model registered)",
            model_version="",
            kind="none",
            using_stub=True,
            predictions=[],
        )

    if rec["kind"] == "linear":
        return _linear_predict(rec, features)

    loaded = _ensure_loaded(rec)
    if loaded is None or loaded.get("model") is None:
        return _stub_predict(rec, features, sample_id=sample_id)

    try:
        return _real_predict(rec, loaded, features)
    except Exception as exc:
        LOG.exception("Real model %s failed (%s) — using stub.", rec["id"], exc)
        return _stub_predict(rec, features, sample_id=sample_id)


# ---------------------------------------------------------------------------
# Disagreement check (unchanged contract)
# ---------------------------------------------------------------------------


def disagreement_flags(main: ModelPrediction,
                       linear: ModelPrediction,
                       threshold: float = 0.30) -> List[str]:
    out = []
    by_ion = {p.ion: p.value_mg_l for p in linear.predictions}
    for p in main.predictions:
        l = by_ion.get(p.ion)
        if l is None or l == 0:
            continue
        rel = abs(p.value_mg_l - l) / max(abs(l), 1e-12)
        if rel > threshold:
            out.append(p.ion)
    return out


def status_summary() -> Dict[str, Any]:
    rec = config_store.models().get_default()
    if not rec:
        return {"name": "(none)", "version": "—", "using_stub": True,
                "kind": "none"}
    available = (rec.get("kind") == "linear" or
                 (rec.get("path") and Path(rec["path"]).exists()))
    return {
        "id": rec["id"],
        "name": rec["name"],
        "version": rec.get("version", ""),
        "kind": rec.get("kind", ""),
        "using_stub": not available,
    }
