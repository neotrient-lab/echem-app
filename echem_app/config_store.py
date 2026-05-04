"""JSON-backed registries for presets, measurement devices, and AI models.

Persisted to `echem_app/data/`:

  data/presets.json   — Echem program presets (CV parameters + trigger mode)
  data/devices.json   — Measurement device registry (name, id, optional port hint)
  data/models.json    — AI model registry (name, file path, ion metadata)

Each registry uses the same shape: a list of records, each with an `id`, plus
a single `default_id` field at the top level pointing at the default record.
The registry classes hide that and expose `list_all()`, `get_default()`,
`add()`, `update()`, `delete()`, `set_default()`.

Why JSON files and not a DB?  This is a single-operator local app; SQLite
would be overkill, and the operator can hand-edit the JSON when needed.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


LOG = logging.getLogger(__name__)
_LOCK = threading.RLock()


HERE = Path(__file__).resolve().parent

# When the env var ECHEM_DATA_DIR is set (e.g. by the PyInstaller
# launcher), persist registries to that user-writable folder so they
# survive across app launches.  Otherwise fall back to the in-tree
# data/ folder, which is what the dev workflow uses.
_env_data = os.environ.get("ECHEM_DATA_DIR")
DATA_DIR = Path(_env_data) if _env_data else HERE / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _load_json(path: Path, fallback: Dict[str, Any]) -> Dict[str, Any]:
    if not path.exists():
        path.write_text(json.dumps(fallback, indent=2))
        return dict(fallback)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        LOG.warning("Could not parse %s (%s) — recreating with fallback.",
                    path, exc)
        backup = path.with_suffix(path.suffix + ".broken")
        try:
            path.rename(backup)
        except Exception:
            pass
        path.write_text(json.dumps(fallback, indent=2))
        return dict(fallback)


def _save_json(path: Path, data: Dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Generic registry
# ---------------------------------------------------------------------------


class Registry:
    """Generic JSON-backed registry of records.

    A record is a dict that must contain an "id" key; the registry adds
    "created_at" and an "is_default" flag derived from the top-level
    `default_id` field.
    """

    def __init__(self, path: Path, *, id_prefix: str, seed: List[Dict[str, Any]]):
        self.path = path
        self.id_prefix = id_prefix
        self._seed = seed
        self._fallback = {
            "items": list(seed),
            "default_id": seed[0]["id"] if seed else None,
        }
        with _LOCK:
            self._data = _load_json(self.path, self._fallback)
            self._migrate()

    def _migrate(self):
        """Backfill missing fields on legacy records.

        When new top-level fields are added to a seeded record (e.g. when we
        add a `start_countdown_s` to the preset schema), existing records on
        disk won't have them. We backfill any missing keys from the seed so
        user-added records keep their values but pick up the new defaults.
        """
        if "items" not in self._data:
            self._data["items"] = []
        if "default_id" not in self._data:
            self._data["default_id"] = (
                self._data["items"][0]["id"] if self._data["items"] else None
            )
        # Backfill missing top-level keys on each record using the first
        # seed entry as the schema reference.
        if self._seed:
            schema = self._seed[0]
            changed = False
            for rec in self._data["items"]:
                for k, v in schema.items():
                    if k not in rec:
                        rec[k] = v
                        changed = True
            if changed:
                _save_json(self.path, self._data)

    def _decorate(self, rec: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(rec)
        out["is_default"] = (rec.get("id") == self._data.get("default_id"))
        return out

    def list_all(self) -> List[Dict[str, Any]]:
        with _LOCK:
            return [self._decorate(r) for r in self._data["items"]]

    def get(self, item_id: str) -> Optional[Dict[str, Any]]:
        with _LOCK:
            for r in self._data["items"]:
                if r.get("id") == item_id:
                    return self._decorate(r)
        return None

    def get_default(self) -> Optional[Dict[str, Any]]:
        with _LOCK:
            did = self._data.get("default_id")
            if not did:
                return None
            return self.get(did)

    def add(self, record: Dict[str, Any], *, set_default: bool = False) -> Dict[str, Any]:
        with _LOCK:
            record = dict(record)
            record.setdefault("id", _new_id(self.id_prefix))
            record.setdefault("created_at",
                              time.strftime("%Y-%m-%dT%H:%M:%S%z"))
            self._data["items"].append(record)
            if set_default or not self._data.get("default_id"):
                self._data["default_id"] = record["id"]
            _save_json(self.path, self._data)
            return self._decorate(record)

    def update(self, item_id: str, fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        with _LOCK:
            for r in self._data["items"]:
                if r.get("id") == item_id:
                    r.update({k: v for k, v in fields.items()
                              if k not in ("id", "created_at")})
                    _save_json(self.path, self._data)
                    return self._decorate(r)
        return None

    def delete(self, item_id: str) -> bool:
        with _LOCK:
            before = len(self._data["items"])
            self._data["items"] = [r for r in self._data["items"]
                                   if r.get("id") != item_id]
            if self._data.get("default_id") == item_id:
                self._data["default_id"] = (
                    self._data["items"][0]["id"]
                    if self._data["items"] else None
                )
            _save_json(self.path, self._data)
            return len(self._data["items"]) != before

    def set_default(self, item_id: str) -> bool:
        with _LOCK:
            if any(r.get("id") == item_id for r in self._data["items"]):
                self._data["default_id"] = item_id
                _save_json(self.path, self._data)
                return True
        return False


# ---------------------------------------------------------------------------
# Seeded registries
# ---------------------------------------------------------------------------


_DEFAULT_PRESET = {
    "id": "preset_default_cv",
    "name": "Standard CV — 3 cycles @ 0.1 V/s",
    "technique": "CV",
    # Vendor-mappable CV parameters (matches cv_app.params.CVParameters)
    "cv": {
        "e_begin": 0.0,
        "e_vtx1": 0.5,
        "e_vtx2": -0.5,
        "e_step": 0.01,
        "scan_rate": 0.1,
        "n_scans": 3,
        "pretreat_potential_v": 0.0,
        "pretreat_duration_s": 3.0,
        "pretreat_interval_s": 0.1,
        "pgstat_mode": 2,
        "max_bandwidth_hz": 40,
        "current_range": "100u",
        "auto_range_low": "1n",
        "auto_range_high": "100u",
        "enable_autoranging": True,
    },
    "trigger_mode": "drop_detect",   # "drop_detect" | "manual"
    # Drop-detect method (only used when trigger_mode == "drop_detect"):
    #   "voltage" — software OCP threshold; works on any cell incl. PalmSens
    #               dummy cell.  Triggers when |OCP - baseline| exceeds
    #               `voltage_threshold_mv`.
    #   "gpio"    — hardware GPIO_D0/D1 drop-detect.  Requires a drop-detect
    #               compatible SPE (not the PalmSens dummy cell).
    "drop_detect_method": "voltage",
    "voltage_threshold_mv": 30,      # mV change in OCP that triggers the run
    # Timing
    "start_countdown_s": 0,
    "post_drop_settle_s": 0,
    "notes": "Default preset. 3 cycles of CV from 0 V → +0.5 V → -0.5 V → 0 V.",
}


_DEFAULT_DEVICE = {
    "id": "device_default",
    "name": "Primary EmStat4T",
    "device_id": "ES4T-01",
    "model": "EmStat4T",
    # Connection wiring:
    #   "usb_auto"  — let the vendor library auto-detect a USB serial port.
    #                 port_hint is ignored.
    #   "bluetooth" — find a paired Bluetooth-SPP serial port whose name
    #                 contains `port_hint` (e.g. "PS-539B" → matches a
    #                 macOS path like /dev/cu.PS-539B-SerialPort).  The
    #                 device must already be paired in System Settings.
    #   "manual"    — use the literal `port_hint` as the serial port path
    #                 (e.g. "/dev/cu.usbmodem101", "COM6").
    "connection_type": "usb_auto",
    "port_hint": "",
    "baudrate": 0,            # 0 = use sensible default per connection type
    "notes": "",
}


_DEFAULT_MODELS = [
    {
        "id": "model_linear_v0",
        "name": "Linear regression (built-in baseline)",
        "kind": "linear",                     # "linear" | "torch" | "sklearn"
        "path": "",                           # built-in, no file
        "version": "linear-v0",
        "ions": [
            {"symbol": "Cu", "name": "Copper",   "lod_mg_l": 0.030},
            {"symbol": "Pb", "name": "Lead",     "lod_mg_l": 0.0001},
            {"symbol": "Cd", "name": "Cadmium",  "lod_mg_l": 5e-5},
            {"symbol": "Zn", "name": "Zinc",     "lod_mg_l": 0.150},
            {"symbol": "Fe", "name": "Iron",     "lod_mg_l": 0.040},
            {"symbol": "Hg", "name": "Mercury",  "lod_mg_l": 1e-5},
        ],
        "linear_coefficients": {
            "Cu": [0.42, 1.0e6],
            "Pb": [0.005, 8.0e6],
            "Cd": [0.002, 6.0e6],
            "Zn": [2.5,   3.0e6],
            "Fe": [0.7,   2.0e6],
            "Hg": [3e-4,  1.0e7],
        },
        "notes": "Built-in linear baseline using anodic peak current as predictor.",
    },
]


_PRESETS = Registry(DATA_DIR / "presets.json",
                    id_prefix="preset",
                    seed=[_DEFAULT_PRESET])
_DEVICES = Registry(DATA_DIR / "devices.json",
                    id_prefix="device",
                    seed=[_DEFAULT_DEVICE])
_MODELS = Registry(DATA_DIR / "models.json",
                   id_prefix="model",
                   seed=_DEFAULT_MODELS)


def presets() -> Registry:  return _PRESETS
def devices() -> Registry:  return _DEVICES
def models() -> Registry:   return _MODELS
