"""auto_save.py — automatic measurement persistence.

Every completed CV measurement is written to disk immediately, with no
"Save" button needed.  Files land under:

    ~/Documents/Neotrient/results/YYYY-MM-DD/

A new dated folder is created on the first measurement of the day.  If
nobody runs anything on a given day, no folder exists for that day.

Each measurement produces three files (timestamped + sample-id'd):

    HHMMSS_<sample_id>.csv       full per-point CV data
    HHMMSS_<sample_id>.json      params + metadata (operator, preset, etc.)
    log.txt                      append-only daily log of every run

The log.txt format is one line per event:

    [ISO-8601 timestamp]  operator=<name>  sample=<id>  preset=<name>
                          status=<ok|error|cancelled>  cycles=<n>  notes=<text>

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


def save_measurement(
    *,
    sample_id: str,
    operator: str,
    preset_name: str = "",
    cv_points: Optional[Iterable[Sequence[float]]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    status: str = "ok",
) -> Dict[str, str]:
    """Write a CV measurement to today's folder + log it.

    Returns a dict with paths the caller can echo to the UI:
        { "csv": "...", "json": "...", "folder": "...", "log": "..." }

    Failures never raise — the measurement should not be lost just
    because the disk is full.  Errors are logged to the application
    logger and the returned paths may be empty.
    """
    metadata = dict(metadata or {})
    metadata.setdefault("sample_id",   sample_id)
    metadata.setdefault("operator",    operator)
    metadata.setdefault("preset_name", preset_name)
    metadata.setdefault("status",      status)
    metadata["saved_at"] = datetime.now().isoformat(timespec="seconds")

    paths = {"csv": "", "json": "", "folder": "", "log": ""}
    try:
        folder = get_today_dir()
        paths["folder"] = str(folder)
        paths["log"]    = str(folder / "log.txt")

        ts = datetime.now().strftime("%H%M%S")
        slug = _safe_filename(sample_id)
        base = folder / f"{ts}_{slug}"

        # CSV — only if we actually have data
        if cv_points is not None:
            csv_path = base.with_suffix(".csv")
            with csv_path.open("w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["index", "potential_V", "current_A", "cycle"])
                for i, pt in enumerate(cv_points):
                    # accept (V, I) or (V, I, cycle)
                    cycle = pt[2] if len(pt) > 2 else ""
                    w.writerow([i, pt[0], pt[1], cycle])
            paths["csv"] = str(csv_path)

        # JSON metadata
        json_path = base.with_suffix(".json")
        with json_path.open("w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, default=str)
        paths["json"] = str(json_path)

        # Append to daily log
        log_meta = {**metadata, "csv": paths["csv"], "json": paths["json"]}
        append_log(log_meta)

        LOG.info("Auto-saved measurement to %s", folder)
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
