"""Thin wrapper around the vendor `palmsens` package.

The vendor library lives in
`MethodSCRIPT_Examples-master/Example_Python/Example_Python/palmsens/` and is
imported lazily so that the rest of `cv_app` (parameters, script builder,
analysis, replay) keeps working in environments where pyserial / the vendor
package isn't available — useful for offline tests and CI.
"""

from __future__ import annotations

import contextlib
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

LOG = logging.getLogger(__name__)


# Make the vendored palmsens helpers importable without forcing the user to
# install them as a package.  Two layouts supported:
#   1. <project>/vendor/        — slim alpha-1+ layout.
#   2. <project>/MethodSCRIPT_Examples-master/Example_Python/Example_Python/
#      — legacy layout (kept for backwards compat).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_VENDOR_CANDIDATES = [
    _PROJECT_ROOT / "vendor",
    _PROJECT_ROOT / "MethodSCRIPT_Examples-master" / "Example_Python" / "Example_Python",
]
_VENDOR_DIR = next(
    (d for d in _VENDOR_CANDIDATES if (d / "palmsens").is_dir()),
    None,
)
if _VENDOR_DIR is not None and str(_VENDOR_DIR) not in sys.path:
    sys.path.insert(0, str(_VENDOR_DIR))


def _import_palmsens():
    """Import the vendor palmsens modules, raising a helpful error on failure."""
    try:
        import palmsens.instrument as instrument  # type: ignore
        import palmsens.serialport as serialport  # type: ignore
        import palmsens.mscript as mscript        # type: ignore
    except ImportError as exc:  # pragma: no cover - exercised only without hw
        raise ImportError(
            "Could not import the vendor `palmsens` package.  Make sure the "
            "MethodSCRIPT_Examples-master folder is alongside the `cv_app` "
            "package, and `pip install pyserial matplotlib numpy`.  "
            f"Original error: {exc}"
        ) from exc
    return instrument, serialport, mscript


def find_device(port: Optional[str] = None,
                baudrate: Optional[int] = None) -> Tuple[str, int]:
    """Return (port, baudrate) for the connected MethodSCRIPT device.

    If `port` is None, the vendor auto-detect is used.  If `baudrate` is None,
    a sensible default is chosen based on the device description (921600 for
    EmStat4 / EmStat4T, 230400 for EmStat Pico).
    """
    _, serialport, _ = _import_palmsens()
    if port and baudrate:
        return port, baudrate
    if port and not baudrate:
        # User specified the port but not the baudrate. EmStat4T defaults to
        # 921600.
        return port, 921600
    detected_port, detected_baud = serialport.auto_detect_port()
    return detected_port, baudrate or detected_baud


@dataclass
class DeviceInfo:
    """Information about the connected device."""

    device_type: str
    firmware_version: str
    mscript_version: str
    serial_number: str


class DeviceConnection(contextlib.AbstractContextManager):
    """High-level managed connection to a PalmSens MethodSCRIPT instrument.

    Usage:

        with DeviceConnection() as dev:
            print(dev.info)
            for sample in dev.run_script_streaming(script_text):
                ...
    """

    def __init__(self,
                 port: Optional[str] = None,
                 baudrate: Optional[int] = None,
                 read_timeout_s: float = 5.0):
        self._instrument_mod, self._serialport_mod, self._mscript_mod = _import_palmsens()
        self.port, self.baudrate = find_device(port, baudrate)
        self.read_timeout_s = read_timeout_s
        self._comm = None
        self._device = None
        self.info: Optional[DeviceInfo] = None

    # context manager ------------------------------------------------------
    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
        return False  # don't swallow exceptions

    # connection management ------------------------------------------------
    def open(self):
        if self._comm is not None:
            return
        LOG.info("Connecting to %s @ %d baud", self.port, self.baudrate)
        self._comm = self._serialport_mod.Serial(
            self.port, self.baudrate, self.read_timeout_s
        )
        self._comm.open()
        self._device = self._instrument_mod.Instrument(self._comm)
        # Recover from a possibly-running script
        self._device.abort_and_sync()
        self.info = DeviceInfo(
            device_type=self._device.get_device_type(),
            firmware_version=self._device.get_firmware_version(),
            mscript_version=self._device.get_mscript_version(),
            serial_number=self._device.get_serial_number(),
        )
        LOG.info("Connected to %s (FW %s)", self.info.device_type,
                 self.info.firmware_version)

    def close(self):
        try:
            if self._device is not None:
                with contextlib.suppress(Exception):
                    self._device.abort_and_sync()
        finally:
            if self._comm is not None:
                with contextlib.suppress(Exception):
                    self._comm.close()
            self._comm = None
            self._device = None

    # script execution -----------------------------------------------------
    @property
    def device(self):
        """Underlying `palmsens.instrument.Instrument`."""
        if self._device is None:
            raise RuntimeError("Device not open. Call open() first.")
        return self._device

    @property
    def device_type_str(self) -> str:
        return self.info.device_type if self.info else "unknown device"

    def send_script_text(self, script_text: str):
        """Write an in-memory MethodSCRIPT program to the device."""
        # MethodSCRIPT expects ASCII, line-terminated.  Sending it as one chunk
        # is fine for short scripts; for long ones we split per-line so it
        # behaves the same as the vendor `send_script` helper.
        for line in script_text.splitlines():
            self.device.write(line + "\n")

    def iter_data_packages(self, line_timeout: Optional[float] = None):
        """Yield parsed data Packages until the script finishes.

        Empty line marks end-of-script.  Error lines (starting with '!') are
        logged and the iteration stops.
        """
        mscript = self._mscript_mod
        while True:
            try:
                line = self.device.readline(line_timeout=line_timeout)
            except self._instrument_mod.CommunicationTimeout:
                # No data yet — keep waiting.  Caller can break on a global
                # timeout if they want.
                continue
            if line == "\n":
                return
            if not line:
                continue
            first = line[0]
            if first == "P":
                yield mscript.parse_mscript_data_package(line)
            elif first == "!":
                LOG.error("Device reported error: %s", line.strip())
                return
            else:
                # Loop / scan markers and other meta lines — ignore for the
                # streaming case (the runner reconstructs scan boundaries
                # from the data itself).
                continue
