"""Resolve a device record to a serial port + baudrate.

Three connection types are supported:

  - "usb_auto"  — delegate to the vendor `palmsens.serialport.auto_detect_port`
  - "bluetooth" — scan `serial.tools.list_ports` for any port whose
                  device path / description / hwid contains the BT device
                  name from `port_hint`.  The device must already be
                  paired in System Settings → Bluetooth.
  - "manual"    — use `port_hint` as the literal port path.

Default baudrates:
  - USB EmStat4 / EmStat4T / Nexus → 921600
  - All BT (SPP) and other serial → 230400
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


LOG = logging.getLogger(__name__)

# Make the vendored palmsens.serialport importable on this path.
# We look in two places, in order:
#   1. <project>/vendor/        — the slim alpha-1+ package layout.
#   2. <project>/MethodSCRIPT_Examples-master/Example_Python/Example_Python/
#      — the legacy layout where the palmsens library lived alongside the
#      MethodSCRIPT examples.  Kept for backwards compatibility.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_VENDOR_CANDIDATES = [
    _PROJECT_ROOT / "vendor",
    _PROJECT_ROOT / "MethodSCRIPT_Examples-master" / "Example_Python" / "Example_Python",
]
_VENDOR_DIR = next(
    (d for d in _VENDOR_CANDIDATES if (d / "palmsens").is_dir()),
    _VENDOR_CANDIDATES[0],
)
if str(_VENDOR_DIR) not in sys.path:
    sys.path.insert(0, str(_VENDOR_DIR))


def _ensure_palmsens_path() -> None:
    """Public helper for sibling modules (e.g. ble_transport) that also
    need the vendored palmsens.* on sys.path."""
    if str(_VENDOR_DIR) not in sys.path:
        sys.path.insert(0, str(_VENDOR_DIR))


# Sentinel returned by `resolve_device_connection` when the device is BLE.
# The port string `ble://<name>` carries the BLE name through the existing
# (port, baud) tuple plumbing without expanding everyone's signature.
BLE_PORT_PREFIX = "ble://"


def _list_serial_ports():
    import serial.tools.list_ports
    return list(serial.tools.list_ports.comports(include_links=False))


def find_bluetooth_port(bt_name: str) -> Tuple[str, int]:
    """Find a paired Bluetooth-SPP serial port matching `bt_name`.

    Matches case-insensitively against the port's device path,
    description, and hwid — so 'PS-539B' will pick up
    `/dev/cu.PS-539B-SerialPort` on macOS or
    `COM5` (description "Standard Serial over Bluetooth link (PS-539B)")
    on Windows.

    Returns (port_path, default_baudrate).  Raises RuntimeError if
    nothing matches.
    """
    needle = (bt_name or "").strip().lower()
    if not needle:
        raise RuntimeError("Bluetooth device name is empty.")
    ports = _list_serial_ports()
    matches: List[str] = []
    for p in ports:
        for hay in (p.device, p.description or "", p.hwid or ""):
            if needle in (hay or "").lower():
                matches.append(p.device)
                break
    if not matches:
        all_seen = ", ".join(p.device for p in ports) or "(none)"
        raise RuntimeError(
            f"Bluetooth device '{bt_name}' not found. "
            "Pair it via System Settings → Bluetooth first. "
            f"Currently visible serial ports: {all_seen}"
        )
    # On macOS prefer 'cu.' over 'tty.' (cu. doesn't block on DCD).
    matches.sort(key=lambda p: ("/tty." in p, p))
    return matches[0], 230400


def resolve_device_connection(record: Dict[str, Any]) -> Tuple[str, int]:
    """Return (port, baudrate) for the given device record.

    Falls back to USB auto-detect if the record is missing or empty.
    """
    if not record:
        return _resolve_usb_auto()

    conn = (record.get("connection_type") or "usb_auto").lower()
    port_hint = (record.get("port_hint") or "").strip()
    baud_override = int(record.get("baudrate") or 0)

    if conn == "manual":
        if not port_hint:
            raise RuntimeError(
                f"Device '{record.get('name')}' is set to manual port "
                "but no port path is configured."
            )
        baud = baud_override or 921600
        return port_hint, baud

    if conn == "bluetooth":
        if not port_hint:
            raise RuntimeError(
                f"Device '{record.get('name')}' is set to Bluetooth "
                "but no Bluetooth name is configured."
            )
        port, default_baud = find_bluetooth_port(port_hint)
        return port, baud_override or default_baud

    if conn == "ble":
        if not port_hint:
            raise RuntimeError(
                f"Device '{record.get('name')}' is set to BLE "
                "but no BLE name is configured."
            )
        # Encode the BLE name into a 'ble://' pseudo-port so it flows
        # through the same (port, baudrate) plumbing.  Baudrate is
        # meaningless for BLE — return 0 and let the BLE adapter handle
        # everything.
        return f"{BLE_PORT_PREFIX}{port_hint}", 0

    # usb_auto (default)
    return _resolve_usb_auto(baud_override)


def _resolve_usb_auto(baud_override: int = 0) -> Tuple[str, int]:
    import palmsens.serialport as sp  # type: ignore
    port, baud = sp.auto_detect_port()
    return port, baud_override or baud


def status_for_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """Lightweight status check for a device record — never raises.

    For BLE devices we deliberately don't scan on every poll (a BLE scan
    takes seconds and can prompt the OS for permissions).  Instead we
    just report it as 'configured for BLE' and trust the actual measure
    call to surface any error.
    """
    conn_type = (record or {}).get("connection_type", "usb_auto")
    if conn_type == "ble":
        name = (record or {}).get("port_hint", "")
        return {
            "connected": True,             # status is "ready", not actively scanning
            "port": f"{BLE_PORT_PREFIX}{name}",
            "baudrate": 0,
            "connection_type": "ble",
            "ble_name": name,
            "note": "BLE — connection verified at measurement time.",
        }
    try:
        port, baud = resolve_device_connection(record)
        return {
            "connected": True,
            "port": port,
            "baudrate": baud,
            "connection_type": conn_type,
        }
    except Exception as exc:
        return {
            "connected": False,
            "error": str(exc),
            "connection_type": conn_type,
        }


def status_default_device() -> Dict[str, Any]:
    """Used by /api/status — resolves whichever device is currently the default."""
    from . import config_store
    rec = config_store.devices().get_default() or {}
    return status_for_record(rec)
