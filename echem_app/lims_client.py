"""Stub LIMS client.

If `LIMS_URL` is configured in the env, POSTs the predictions JSON there.
Otherwise it just records that no LIMS endpoint was configured and returns
success — so the operator still gets a valid local export.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional


def post_to_lims(payload: Dict[str, Any],
                 url: Optional[str] = None,
                 timeout_s: float = 10.0) -> Dict[str, Any]:
    """POST `payload` as JSON to the configured LIMS endpoint.

    Returns ``{"ok": True/False, "status": http_status_or_None,
    "message": "..."}``.  Never raises — Flask layer should display the
    message verbatim if `ok` is False.
    """
    url = url or os.environ.get("LIMS_URL")
    if not url:
        return {
            "ok": True,
            "status": None,
            "message": "No LIMS_URL configured; skipped (local export still saved).",
        }

    try:
        body = json.dumps(payload, default=str).encode("utf-8")
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            status = resp.status
            text = resp.read().decode("utf-8", errors="replace")
        return {
            "ok": 200 <= status < 300,
            "status": status,
            "message": text[:500],
        }
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code, "message": str(e)}
    except Exception as e:
        return {"ok": False, "status": None, "message": str(e)}
