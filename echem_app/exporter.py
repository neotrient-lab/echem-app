"""Build the export `.zip` bundle described in the spec."""

from __future__ import annotations

import io
import json
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def _safe_id(s: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in s)[:64] or "sample"


def build_export_bundle(*,
                        sample_id: str,
                        session_meta: Dict[str, Any],
                        sample_meta: Dict[str, Any],
                        operator: Dict[str, Any],
                        raw_csv: Path,
                        predictions: Dict[str, Any],
                        linear: Dict[str, Any],
                        notes: str,
                        plots: Optional[Dict[str, Path]] = None,
                        audit_lines: Optional[List[Dict[str, Any]]] = None,
                        exports_dir: Path) -> Path:
    """Assemble the bundle and return the path to the generated zip."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    zip_name = f"sample_{_safe_id(sample_id)}_{ts}.zip"
    out_path = Path(exports_dir) / zip_name
    out_path.parent.mkdir(parents=True, exist_ok=True)

    metadata = {
        "sample_id": sample_id,
        "session": session_meta,
        "sample": sample_meta,
        "operator": operator,
        "exported_at": datetime.now(timezone.utc).isoformat(),
    }

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("metadata.json",
                    json.dumps(metadata, indent=2, default=str, ensure_ascii=False))
        zf.writestr("predictions.json",
                    json.dumps({
                        "primary": predictions,
                        "linear_baseline": linear,
                        "notes": notes,
                    }, indent=2, default=str, ensure_ascii=False))

        if raw_csv.exists():
            zf.write(raw_csv, "raw_voltammograms.csv")

        if plots:
            for name, p in plots.items():
                if p and Path(p).exists():
                    zf.write(p, f"plots/{Path(p).name}")

        # audit_log.txt
        audit_buf = io.StringIO()
        if audit_lines:
            for e in audit_lines:
                audit_buf.write(json.dumps(e, default=str, ensure_ascii=False) + "\n")
        zf.writestr("audit_log.txt", audit_buf.getvalue())

    return out_path
