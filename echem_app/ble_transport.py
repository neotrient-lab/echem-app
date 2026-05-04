"""Bluetooth Low Energy (BLE) transport for PalmSens devices.

Why this module exists
----------------------
PalmSens BLE devices (Sensit Smart, EmStat4T-BLE, PS-539B etc.) do NOT
appear as `/dev/cu.*` serial ports — they're Bluetooth Low Energy, which
uses GATT services, not the SPP profile.  pyserial can't talk to them.

This module wraps a `bleak` BLE connection in a synchronous, pyserial-
compatible facade — `write(bytes)` and `readline() -> bytes` — so the
vendor's `palmsens.instrument.Instrument` class can use it without any
changes.

Protocol assumptions
--------------------
PalmSens BLE devices typically expose the **Nordic UART Service (NUS)**
which acts as a transparent BLE-to-serial bridge:

  - Service:   6E400001-B5A3-F393-E0A9-E50E24DCCA9E
  - TX (notify, device → host):  6E400003-B5A3-F393-E0A9-E50E24DCCA9E
  - RX (write, host → device):   6E400002-B5A3-F393-E0A9-E50E24DCCA9E

If the device uses a different (custom) service, this adapter falls back
to scanning the connected device's services and picking the first
characteristic with `notify` for receive and the first with `write` /
`write-without-response` for transmit.  That covers most custom GATT
profiles too.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from queue import Empty, Queue
from typing import Optional

LOG = logging.getLogger(__name__)

# Standard Nordic UART Service UUIDs.
NUS_SERVICE = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
NUS_TX_CHAR = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"   # notify  device → host
NUS_RX_CHAR = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"   # write   host  → device

# Default chunk size for writes — keep small to fit a 23-byte default MTU.
# bleak will auto-negotiate higher MTU when the device supports it; chunking
# at 20 bytes is the safe lowest-common-denominator.
WRITE_CHUNK = 20


# ---------------------------------------------------------------------------
# pyserial-compatible facade
# ---------------------------------------------------------------------------


class _FakeConnection:
    """Mimics pyserial's `connection` attribute so that
    `Instrument.readline()`'s `self.comm.connection.timeout = ...` line
    doesn't blow up."""

    def __init__(self, timeout_s: float):
        self.timeout = timeout_s


class BleSerialAdapter:
    """Synchronous, pyserial-compatible BLE adapter.

    Methods:
        open()              — connect to the device by name (synchronous)
        close()             — disconnect cleanly
        write(data: bytes)  — send bytes to the device's RX characteristic
        readline() -> bytes — block until a `\\n`-terminated line arrives
                              from the TX (notify) characteristic, or
                              return b'' on timeout
        connection.timeout  — set/get the readline timeout

    Internally it runs bleak's asyncio loop in a daemon thread; readline
    pulls from a thread-safe queue of incoming notification chunks and
    re-assembles them into newline-terminated lines.
    """

    def __init__(self, ble_name: str,
                 timeout_s: float = 5.0,
                 scan_timeout_s: float = 15.0,
                 # Generous so the operator has time to enter the pairing
                 # code shown on the EmStat4T screen if macOS prompts.
                 connect_timeout_s: float = 30.0,
                 max_candidates: int = 6):
        self.ble_name = ble_name
        self.connection = _FakeConnection(timeout_s)
        self.scan_timeout_s = scan_timeout_s
        self.connect_timeout_s = connect_timeout_s
        self.max_candidates = max_candidates

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._client = None
        self._thread: Optional[threading.Thread] = None
        self._tx_uuid: Optional[str] = None  # notify (device → host)
        self._rx_uuid: Optional[str] = None  # write  (host → device)

        self._rx_buffer = bytearray()
        self._rx_chunks: "Queue[bytes]" = Queue()
        self._open_error: Optional[Exception] = None
        self._ready_event = threading.Event()
        self._stop_flag = threading.Event()

    # ----- lifecycle -----------------------------------------------------

    def open(self) -> None:
        try:
            import bleak  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "bleak is not installed. Run: pip install bleak"
            ) from exc
        if self._thread is not None:
            return
        self._open_error = None
        self._ready_event.clear()
        self._stop_flag.clear()
        self._thread = threading.Thread(target=self._run_loop,
                                        name=f"BLE[{self.ble_name}]",
                                        daemon=True)
        self._thread.start()
        # Budget = scan + (connect + GAP + MS-validation) per candidate.
        # MethodSCRIPT validation is the slow path because it may wait
        # for the operator to type a pairing PIN.
        per_candidate = self.connect_timeout_s + 50  # 50s for GAP + validate
        total_timeout = (self.scan_timeout_s
                         + self.max_candidates * per_candidate
                         + 5)
        if not self._ready_event.wait(timeout=total_timeout):
            raise RuntimeError(
                f"BLE setup for '{self.ble_name}' timed out after "
                f"{total_timeout:.0f}s."
            )
        if self._open_error:
            raise self._open_error

    def close(self) -> None:
        if not self._loop:
            return
        self._stop_flag.set()
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._disconnect_async(), self._loop
            )
            future.result(timeout=5)
        except Exception as exc:
            LOG.debug("BLE disconnect error (ignored): %s", exc)
        try:
            self._loop.call_soon_threadsafe(self._loop.stop)
        except Exception:
            pass
        if self._thread:
            self._thread.join(timeout=3)
        self._thread = None
        self._loop = None
        self._client = None

    # ----- pyserial-compatible IO ----------------------------------------

    def write(self, data: bytes) -> None:
        if not self._client:
            raise RuntimeError("BLE not connected.")
        future = asyncio.run_coroutine_threadsafe(
            self._write_async(data), self._loop
        )
        future.result(timeout=max(2.0, self.connection.timeout))

    def readline(self) -> bytes:
        deadline = time.monotonic() + max(0.0, self.connection.timeout)
        while True:
            # Already-buffered line?
            idx = self._rx_buffer.find(b"\n")
            if idx >= 0:
                line = bytes(self._rx_buffer[:idx + 1])
                del self._rx_buffer[:idx + 1]
                return line
            # Otherwise wait for more
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return b""    # timeout — pyserial returns empty bytes
            try:
                chunk = self._rx_chunks.get(timeout=min(remaining, 0.2))
            except Empty:
                continue
            if chunk:
                self._rx_buffer.extend(chunk)

    # ----- async internals (run inside the worker thread) ----------------

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._connect_async())
            self._ready_event.set()
            self._loop.run_forever()
        except Exception as exc:
            self._open_error = exc
            self._ready_event.set()
        finally:
            try:
                self._loop.close()
            except Exception:
                pass

    async def _connect_async(self) -> None:
        from bleak import BleakClient, BleakScanner

        wanted = (self.ble_name or "").strip()
        if not wanted:
            raise RuntimeError("BLE name/MAC is empty.")
        wanted_lower = wanted.lower()

        # Detect MAC-shaped input (e.g. "6c:1d:eb:0b:53:9b").
        wanted_mac = wanted_lower.replace("-", ":").replace(" ", "")
        is_mac = (len(wanted_mac.replace(":", "")) == 12
                  and all(c in "0123456789abcdef:" for c in wanted_mac))
        # Last 4 hex chars from a MAC become a useful name-fragment
        # because PalmSens devices typically embed the MAC tail in the
        # advertised name (MAC ends 53:9b → name "PS-539B").
        mac_tail = wanted_mac.replace(":", "")[-4:] if is_mac else ""

        LOG.info("BLE: scanning for %s '%s' (scan timeout=%ds)…",
                 "MAC" if is_mac else "name", self.ble_name,
                 self.scan_timeout_s)

        discovered = await BleakScanner.discover(
            timeout=self.scan_timeout_s, return_adv=True
        )

        # Quick pre-check: scan ALL the discovered devices for ANY trace
        # of our target identifier (MAC tail, exact MAC, name fragment).
        # If nothing matches, the device isn't advertising — no point
        # connecting to a bunch of unrelated NUS peripherals.  Most
        # common cause: the device is currently bonded to another host
        # (typically a Windows PC) and stays silent until that host
        # releases it.
        target_seen = False
        seen_total = len(discovered)
        for addr, (device, adv) in discovered.items():
            haystacks = [(device.address or "").lower(),
                         (device.name or "").lower(),
                         (getattr(adv, "local_name", None) or "").lower()]
            if is_mac:
                if (wanted_mac in haystacks
                        or any(mac_tail in h for h in haystacks if h)):
                    target_seen = True
                    break
            else:
                if any(wanted_lower in h for h in haystacks if h):
                    target_seen = True
                    break

        if is_mac and not target_seen:
            raise RuntimeError(
                f"BLE: PS-539B (MAC {self.ble_name}, tail '{mac_tail}') "
                f"was NOT in the {seen_total} BLE devices that macOS saw "
                f"during the {self.scan_timeout_s}s scan.\n"
                "\n"
                "Most likely cause: the device is currently bonded to "
                "another host (e.g. Windows).  A BLE peripheral can only "
                "talk to one host at a time and often goes silent in the "
                "presence of its primary one.\n"
                "\n"
                "Fix:\n"
                "  1. On Windows: Settings → Bluetooth → click PS-539B → Remove device.\n"
                "  2. Power-cycle the EmStat4T (off, wait 5 s, back on).\n"
                "  3. Re-run this test within ~30 s of the device booting up.\n"
                "  4. When macOS pops up the pairing dialog, enter the\n"
                "     code shown on the EmStat4T's screen.\n"
            )

        candidates = []   # (priority, device, why)
        for addr, (device, adv) in discovered.items():
            addr_lower = (device.address or "").lower()
            names = list(filter(None, [
                (device.name or "").strip(),
                (getattr(adv, "local_name", None) or "").strip(),
            ]))
            names_lower = [n.lower() for n in names]
            adv_uuids = [u.lower() for u in (getattr(adv, "service_uuids", None) or [])]

            # 1. Exact MAC (Linux/Windows path — bleak returns real MAC there)
            if is_mac and addr_lower == wanted_mac:
                candidates.append((110, device, f"exact MAC {wanted_mac}"))
                continue
            # 2. Service UUID — Nordic UART Service is the most reliable
            #    PalmSens BLE marker because it's in the primary advertisement
            #    (and therefore visible to macOS even when local_name isn't).
            if NUS_SERVICE in adv_uuids:
                candidates.append((100, device,
                                   "advertises Nordic UART Service (NUS)"))
                continue
            # 3. MAC tail in name (when name IS visible — macOS workaround)
            if mac_tail:
                hit = next((n for n in names_lower if mac_tail in n), None)
                if hit:
                    candidates.append((90, device,
                                       f"name '{hit}' contains MAC tail '{mac_tail}'"))
                    continue
            # 4. Exact UUID (macOS device.address is a CoreBluetooth UUID)
            if not is_mac and addr_lower == wanted_lower:
                candidates.append((80, device, "exact UUID"))
                continue
            # 5. Substring name match (only when operator gave us a name)
            if not is_mac:
                hit = next((n for n in names_lower if wanted_lower in n), None)
                if hit:
                    candidates.append((70, device, f"name contains '{wanted_lower}'"))
                    continue

        if not candidates:
            # Verbose discovery log — show service UUIDs + manufacturer
            # data so we can identify the PS-539B even when its name is
            # hidden behind a scan-response we didn't capture.
            seen_lines = []
            for addr, (device, adv) in discovered.items():
                n = (device.name or getattr(adv, "local_name", None) or "(no name)")
                svcs = list(getattr(adv, "service_uuids", None) or [])
                mfg = dict(getattr(adv, "manufacturer_data", None) or {})
                rssi = getattr(adv, "rssi", "?")
                line = f"    {addr}  rssi={rssi}  {n}"
                if svcs:
                    line += f"\n        services: {', '.join(svcs[:3])}"
                if mfg:
                    line += "\n        mfg_data: " + ", ".join(
                        f"0x{cid:04X}={data.hex()[:24]}"
                        for cid, data in list(mfg.items())[:3])
                seen_lines.append(line)
            extra = ("\nVisible BLE devices:\n" + "\n".join(seen_lines[:50])
                     if seen_lines else
                     "\nNo BLE devices were visible at all — check that the "
                     "device is powered, not connected to another host, and that "
                     "macOS Bluetooth permission for this terminal is granted.")
            raise RuntimeError(
                f"BLE device matching '{self.ble_name}' not found within "
                f"{self.connect_timeout_s}s." + extra
            )

        # Sort candidates highest-priority first.
        candidates.sort(key=lambda c: -c[0])
        candidates = candidates[:self.max_candidates]
        LOG.info("BLE: %d candidate(s) to validate", len(candidates))

        # Build the set of name fragments that count as a "PalmSens" match.
        # If the operator gave us a MAC, the tail (e.g. "539b") is the most
        # specific signal.  Otherwise we accept any of the well-known
        # PalmSens name prefixes.
        name_patterns = ["palmsens", "ps-", "sensit", "emstat"]
        if mac_tail:
            name_patterns.append(mac_tail.lower())
        if not is_mac:
            name_patterns.append(wanted_lower)

        GAP_DEVICE_NAME = "00002a00-0000-1000-8000-00805f9b34fb"

        rejection_reasons = []   # human-readable per-candidate reason

        for idx, (priority, device, reason) in enumerate(candidates, 1):
            client = None
            label = f"{device.name or '(no name)'} ({device.address})"
            try:
                LOG.info("BLE: [%d/%d] trying %s — %s",
                         idx, len(candidates), label, reason)
                client = BleakClient(device)
                await asyncio.wait_for(client.connect(),
                                       timeout=self.connect_timeout_s)
            except asyncio.TimeoutError:
                rejection_reasons.append(
                    f"  [{idx}] {label}: connect timed out after "
                    f"{self.connect_timeout_s}s"
                )
                LOG.warning("BLE:        connect timed out — trying next")
                continue
            except Exception as exc:
                rejection_reasons.append(f"  [{idx}] {label}: connect failed: {exc}")
                LOG.warning("BLE:        connect failed: %s", exc)
                if client:
                    try: await client.disconnect()
                    except Exception: pass
                continue

            # If we got here on a strong advertisement signal (exact MAC,
            # NUS service, MAC-tail-in-name, or exact UUID) the
            # advertisement itself already identifies the device.  The
            # GAP Device Name read is then redundant AND counter-
            # productive: it triggers a slow pairing dance that often
            # times out before the operator can enter the PIN.  Skip it
            # for priority >= 80 and rely on the MethodSCRIPT validation
            # below to confirm we're really talking to a PalmSens.
            gap_name = ""
            gap_succeeded = False
            if priority >= 80:
                LOG.info("BLE:        skipping GAP read (advertisement "
                         "already matched at priority %d: %s)",
                         priority, reason)
            else:
                try:
                    name_bytes = await asyncio.wait_for(
                        client.read_gatt_char(GAP_DEVICE_NAME),
                        timeout=5.0,
                    )
                    gap_name = name_bytes.decode("utf-8",
                                                 errors="replace").strip()
                    gap_succeeded = True
                    LOG.info("BLE:        GAP Device Name = %r", gap_name)
                except Exception as exc:
                    LOG.info("BLE:        GAP read failed (%s) — falling "
                             "back to advertisement-based acceptance", exc)

            # Locate notify + write characteristics.  Without these we
            # can't speak MethodSCRIPT — reject regardless of name match.
            notify_uuid = None
            write_uuid = None
            for service in client.services:
                if service.uuid.lower() == NUS_SERVICE:
                    for ch in service.characteristics:
                        u = ch.uuid.lower()
                        if u == NUS_TX_CHAR and "notify" in ch.properties:
                            notify_uuid = ch.uuid
                        if u == NUS_RX_CHAR and (
                            "write" in ch.properties
                            or "write-without-response" in ch.properties
                        ):
                            write_uuid = ch.uuid
            if not (notify_uuid and write_uuid):
                for service in client.services:
                    for ch in service.characteristics:
                        if not notify_uuid and "notify" in ch.properties:
                            notify_uuid = ch.uuid
                        if not write_uuid and (
                            "write" in ch.properties
                            or "write-without-response" in ch.properties
                        ):
                            write_uuid = ch.uuid

            if not (notify_uuid and write_uuid):
                rejection_reasons.append(
                    f"  [{idx}] {label}: connected but no notify+write "
                    f"characteristics (not a serial-over-BLE device)"
                )
                LOG.warning("BLE:        no notify+write characteristics — "
                            "trying next")
                try: await client.disconnect()
                except Exception: pass
                continue

            # Decide whether to accept.
            #   priority >= 80 → strong advertisement match → accept
            #                    (final confirmation done by MethodSCRIPT)
            #   priority < 80  → weak match, require GAP name to confirm
            accepted = False
            why_accept = ""
            if priority >= 80:
                accepted = True
                why_accept = reason     # the advertisement-match reason
            elif gap_succeeded:
                gap_lower = gap_name.lower()
                if any(p and p in gap_lower for p in name_patterns):
                    accepted = True
                    why_accept = (f"GAP name {gap_name!r} matches PalmSens "
                                  f"patterns")
                else:
                    rejection_reasons.append(
                        f"  [{idx}] {label}: GAP name {gap_name!r} doesn't "
                        f"match any of {name_patterns}"
                    )
                    LOG.info("BLE:        %r doesn't match patterns %s — "
                             "trying next", gap_name, name_patterns)
            else:
                rejection_reasons.append(
                    f"  [{idx}] {label}: weak advertisement signal AND "
                    f"GAP Device Name unreadable"
                )

            if not accepted:
                try: await client.disconnect()
                except Exception: pass
                continue

            # Tentatively accept — but verify by sending the MethodSCRIPT
            # 't' (get firmware version) command and waiting for a real
            # PalmSens response.  This catches false positives like phone
            # camera-shutters that happen to advertise NUS but don't
            # actually speak MethodSCRIPT.
            self._client = client
            self._tx_uuid = notify_uuid
            self._rx_uuid = write_uuid
            LOG.info("BLE:        characteristics OK; verifying with "
                     "MethodSCRIPT 't' command…")
            await self._client.start_notify(self._tx_uuid, self._on_notify)
            ms_ok = await self._validate_methodscript()
            if not ms_ok:
                rejection_reasons.append(
                    f"  [{idx}] {label}: connected, but did not respond "
                    f"to MethodSCRIPT 't' (not a PalmSens device)"
                )
                LOG.warning("BLE:        no MethodSCRIPT response — "
                            "disconnecting and trying next")
                try:
                    await self._client.stop_notify(self._tx_uuid)
                except Exception: pass
                try: await client.disconnect()
                except Exception: pass
                self._client = None
                self._tx_uuid = None
                self._rx_uuid = None
                continue

            LOG.info("BLE: ✓ accepted %r (%s) — %s + MethodSCRIPT verified",
                     gap_name or device.name or "(no name)",
                     device.address, why_accept)
            LOG.info("BLE:   TX (notify) = %s", notify_uuid)
            LOG.info("BLE:   RX (write)  = %s", write_uuid)
            return

        # Exhausted all candidates without finding a valid PalmSens device.
        reason_block = ("\n" + "\n".join(rejection_reasons)
                        if rejection_reasons else "")
        raise RuntimeError(
            f"BLE: tried {len(candidates)} candidate(s), none was a valid "
            f"PalmSens device.  Per-candidate reasons:" + reason_block + "\n"
            "Tip: if the EmStat4T showed a pairing code on its screen, the "
            "first connect probably triggered the macOS pairing dialog — "
            "make sure to enter the code BEFORE the connect timeout. "
            "Once paired the bond persists and re-connects are instant."
        )

    async def _validate_methodscript(self, timeout_s: float = 45.0) -> bool:
        """Send the MethodSCRIPT 't' command and wait for a PalmSens
        firmware version reply.

        Long timeout (45 s by default) because the first 't' on a
        bonding-required device triggers macOS's pairing prompt — the
        operator needs time to read the code shown on the EmStat4T's
        screen and type it into the macOS dialog.  We also print a
        stdout hint so the operator knows to look for the dialog.
        """
        import sys as _sys
        print("    BLE: verifying device is PalmSens (sending 't' command)…\n"
              "         If a macOS Bluetooth pairing dialog pops up, enter\n"
              "         the code shown on the EmStat4T's screen.\n"
              "         Waiting up to {} s for response.".format(int(timeout_s)),
              file=_sys.stderr, flush=True)
        # Drain any stale notifications
        while not self._rx_chunks.empty():
            try: self._rx_chunks.get_nowait()
            except Empty: break
        self._rx_buffer.clear()
        try:
            await self._client.write_gatt_char(self._rx_uuid, b"t\n",
                                               response=False)
        except Exception as exc:
            LOG.debug("BLE: validation write failed: %s", exc)
            return False

        # Collect bytes for up to `timeout_s`
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout_s
        buf = bytearray()
        while loop.time() < deadline:
            try:
                chunk = self._rx_chunks.get_nowait()
                buf.extend(chunk)
            except Empty:
                await asyncio.sleep(0.05)
            if buf:
                # Check the first non-empty line
                line = buf.split(b"\n", 1)[0].decode("ascii", errors="replace").strip()
                if line:
                    if line.startswith("t"):
                        LOG.info("BLE: MethodSCRIPT response: %r", line[:60])
                        return True
                    else:
                        LOG.info("BLE: response %r doesn't look like MethodSCRIPT",
                                 line[:60])
                        return False
        LOG.info("BLE: no response to MethodSCRIPT 't' within %ss", timeout_s)
        return False

    async def _disconnect_async(self) -> None:
        if self._client and self._client.is_connected:
            try:
                if self._tx_uuid:
                    await self._client.stop_notify(self._tx_uuid)
            except Exception:
                pass
            try:
                await self._client.disconnect()
            except Exception:
                pass

    async def _write_async(self, data: bytes) -> None:
        for i in range(0, len(data), WRITE_CHUNK):
            chunk = data[i:i + WRITE_CHUNK]
            await self._client.write_gatt_char(self._rx_uuid, chunk,
                                               response=False)

    def _on_notify(self, _sender, data: bytearray) -> None:
        if data:
            self._rx_chunks.put(bytes(data))


# ---------------------------------------------------------------------------
# DeviceConnection-equivalent for BLE
# ---------------------------------------------------------------------------


class BleDeviceConnection:
    """Mirrors `cv_app.device.DeviceConnection`'s public interface so the
    measurement code can use either USB or BLE without branching."""

    def __init__(self, ble_name: str, read_timeout_s: float = 5.0):
        self.ble_name = ble_name
        self.read_timeout_s = read_timeout_s
        self._comm: Optional[BleSerialAdapter] = None
        self._device = None
        self.info = None

    # context manager
    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
        return False

    def open(self) -> None:
        from .connection import _ensure_palmsens_path
        _ensure_palmsens_path()
        # Vendor-imported names
        from palmsens.instrument import Instrument                # type: ignore

        self._comm = BleSerialAdapter(self.ble_name,
                                      timeout_s=self.read_timeout_s)
        self._comm.open()
        self._device = Instrument(self._comm)
        try:
            self._device.abort_and_sync()
        except Exception as exc:
            LOG.debug("BLE abort_and_sync failed (ignored): %s", exc)

        # Lazy import DeviceInfo from echem_app.device wrapper
        from cv_app.device import DeviceInfo
        self.info = DeviceInfo(
            device_type=self._device.get_device_type(),
            firmware_version=self._device.get_firmware_version(),
            mscript_version=self._device.get_mscript_version(),
            serial_number=self._device.get_serial_number(),
        )
        LOG.info("BLE connected: %s (FW %s)",
                 self.info.device_type, self.info.firmware_version)

    def close(self) -> None:
        try:
            if self._device is not None:
                try:
                    self._device.abort_and_sync()
                except Exception:
                    pass
        finally:
            if self._comm is not None:
                try:
                    self._comm.close()
                except Exception:
                    pass
            self._comm = None
            self._device = None

    # ---- helpers expected by iter_measurement -----------------------------

    @property
    def device(self):
        if self._device is None:
            raise RuntimeError("BLE device not open.")
        return self._device

    @property
    def device_type_str(self) -> str:
        return self.info.device_type if self.info else "unknown device"

    def send_script_text(self, script_text: str) -> None:
        for line in script_text.splitlines():
            self._device.write(line + "\n")

    def iter_data_packages(self, line_timeout: Optional[float] = None):
        from palmsens import mscript                         # type: ignore
        if line_timeout is not None:
            self._comm.connection.timeout = line_timeout
        while True:
            try:
                line = self._device.readline(line_timeout=line_timeout)
            except Exception:
                continue
            if line == "\n":
                return
            if not line:
                continue
            first = line[0]
            if first == "P":
                yield mscript.parse_mscript_data_package(line)
            elif first == "!":
                LOG.error("BLE device error: %s", line.strip())
                return
            else:
                continue
