"""Append-only audit log for the Echem app.

Every meaningful action gets a timestamped line in `exports/audit.log`,
plus a per-session JSON copy that ships inside the export bundle.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


_LOCK = threading.Lock()


class AuditLog:
    """Per-session audit trail.  Lines are appended atomically.

    Use :meth:`AuditLog.global_logger()` for app-startup events that aren't
    tied to a specific sample yet.
    """

    def __init__(self, log_path: Path, session_id: Optional[str] = None):
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.session_id = session_id
        self._entries: List[Dict[str, Any]] = []

    @classmethod
    def global_logger(cls, exports_dir: Path) -> "AuditLog":
        return cls(Path(exports_dir) / "audit.log", session_id=None)

    def event(self, action: str, **fields):
        """Log an event with arbitrary keyword fields."""
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "session": self.session_id,
            "action": action,
            **fields,
        }
        line = json.dumps(entry, ensure_ascii=False, default=str)
        with _LOCK:
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        self._entries.append(entry)
        return entry

    def entries(self) -> List[Dict[str, Any]]:
        """Return a copy of the entries logged in this session."""
        return list(self._entries)

    def write_text_dump(self, path: Path):
        """Write the human-readable audit_log.txt for the export bundle."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            for e in self._entries:
                ts = e.get("ts", "")
                action = e.get("action", "")
                detail = {k: v for k, v in e.items() if k not in ("ts", "action", "session")}
                fh.write(f"[{ts}] {action}")
                if detail:
                    fh.write("  " + json.dumps(detail, ensure_ascii=False, default=str))
                fh.write("\n")
