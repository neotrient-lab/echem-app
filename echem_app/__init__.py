"""echem_app — Neotrient Electrochemical Analysis App.

Versioning convention
---------------------
We're pre-1.0, so the version string is `0.<folder>.<patch>`:
    folder alpha_1  →  0.1.0, 0.1.1, 0.1.2, …
    folder alpha_2  →  0.2.0, 0.2.1, …
    folder alpha_N  →  0.N.0, 0.N.1, …
The middle digit always matches the alpha_<N> folder it lives in.
1.0.0 is reserved for the first stable public release.

Version log
-----------
0.1.0   First in-team alpha test release.  Branched from 0.7.x with a full
        mobile UI pass (single-form sample identification, bottom Prev/Next
        navigation, Weather-app rhythm), responsive desktop layout for
        smaller monitors, status-log save page (auto-save, no manual
        export), per-cycle filter honored server-side, deletion that
        actually removes the CSV on disk, preset-change refresh on the
        server, unified 16x16 tick boxes across the analysis page, and the
        original v0.4.0 drop-detect script restored (1-second OCP baseline).
0.1.1   Added "Dummy cell test" preset (PSTrace reference method for
        hardware sanity checks on the PalmSens dummy cell — manual trigger,
        10 cycles, ±1 V, 50 mV/s, 100 nA – 1 mA autorange, 1 s
        equilibration).  Also restored method-2 voltage threshold to 30 mV
        (was 3 mV in 0.1.0 due to a stale preset file).  Switched version
        scheme to 0.<folder>.<patch>.
0.1.2   Fixed "No module named 'palmsens'" error on Connect.  The slim
        alpha_1 package dropped the bulky MethodSCRIPT_Examples-master
        folder, but `connection.py` and `cv_app/device.py` still pointed
        their `_VENDOR_DIR` there.  Both now look in `<project>/vendor/`
        first (where the alpha_1 vendored copy lives) and fall back to
        the legacy MethodSCRIPT_Examples path for backwards compat.
0.1.3   Fixed "unsupported operand type(s) for |: 'type' and 'NoneType'"
        error on Connect when the venv is Python 3.9.  The vendored
        palmsens library uses PEP 604 union syntax (`float | None`) which
        only works on Python 3.10+ at runtime.  Patched
        `vendor/palmsens/instrument.py` and `vendor/palmsens/mscript.py`
        with `from __future__ import annotations` to defer evaluation,
        making them work on 3.9 too.
0.1.4   Fixed "invalid syntax (mscript.py, line 465)" on Connect.  The
        vendored palmsens library uses PEP 634 `match` / `case` pattern
        matching (Python 3.10+) at three places in mscript.py.  Unlike
        annotations, match/case is a real syntax addition that no
        __future__ import can polyfill — rewrote each block as an
        equivalent `if` / `elif` chain so the file parses on Python 3.9.
        All three palmsens .py files now ast-parse cleanly under 3.9
        grammar.
0.1.5   Fixed "USB selected but Connect tries Bluetooth" bug.  Three
        defensive changes:  (a) the EmStat4T USB record is now the
        registered default in devices.json (was PS-539B / BLE), so a
        first-time user who doesn't touch the dropdown gets USB;
        (b) `loadDevices()` no longer clobbers the operator's chosen
        `state.selectedDeviceId` when the device list is reloaded — the
        previous selection is preserved if it still exists; (c) the
        device dropdown now prefixes each option with its transport
        (`[USB]` / `[BLE]` / `[BT (SPP)]`) so the operator can see at a
        glance which connection type they're picking.
0.1.6   Fixed two follow-up Connect bugs.  (a) The Connect modal hard-
        coded "First Bluetooth connection may take up to a minute…"
        regardless of which transport the operator picked — now the
        text is transport-aware (USB shows "Opening the USB serial
        port and running a beep test.  Should take a couple of
        seconds.").  (b) The Connect modal could appear stuck on
        "Connecting to device…" forever when the device firmware
        didn't emit a clean end-of-script marker after the beep test —
        the post-script `iter_data_packages` drain loop is now capped
        at a 5-second wall-clock deadline (with 1-second per-line
        timeout) so the request always returns and the modal closes.
0.1.7   Connect UX cleanup.  (a) USB Connect no longer shows the
        progress modal at all — the operator picked USB precisely
        because they expect it to "just work" instantly, and the
        spinner was overkill (and worse, occasionally stuck).  Inline
        button state ("Connecting…" → "✓ Connected") plus a single
        success toast is enough.  BLE keeps the modal because the
        first-pair really can take 60 seconds and the operator needs
        to watch for the macOS pairing dialog.  (b) Hard client-side
        AbortController timeouts on `/api/connection/test`: 15 s for
        USB, 90 s for BLE.  No matter what the server does, the
        Connect button always re-enables and the operator can retry
        — the UI cannot be permanently stuck.  Timeout errors now
        give actionable advice ("check USB cable and power", "BLE
        device may be paired with another host").
0.1.8   Fixed "phantom Connect" bug.  After a Connect FAILURE (timeout
        or error response) the previous successful connect's
        `state.connectionOpen = true` flag was leaking through, so the
        operator could still click Begin session, the sidebar still
        showed "● Connected" green, and the wizard advanced against an
        unplugged / unreachable device.  Every Connect-fail path now
        runs an `invalidateConnection()` helper that: (a) flips
        `state.connectionOpen` back to false, (b) disables Begin
        session, (c) flips the sidebar pill to "● Disconnected"
        immediately, and (d) POSTs `/api/connection/release` so the
        server drops any held connection too.  refreshStatus() is then
        re-run to reconcile with authoritative server state.  No more
        ghost connections.
0.1.9   Fixed "stuck Connect button + un-clickable Begin session" bug.
        On a Python-3.9 venv the Connect succeeded server-side (sidebar
        went green) but the JS fetch sometimes never resolved its
        promise — leaving `state.connectionOpen=false`, the Connect
        button frozen on "Connecting…", and Begin session refusing to
        advance because of the stale local flag.  refreshStatus() now
        reconciles the JS state with the authoritative server status
        on every 5-second poll: if the server holds a connection but
        local state thinks it doesn't, it flips state.connectionOpen
        true, marks Connect as "✓ Connected", enables Begin session,
        and hides any leftover error/modal.  The reverse direction is
        also handled (server drops the link → JS state goes back to
        disconnected).  This means the UI self-heals within 5 seconds
        no matter what state the connect-fetch ended up in.
0.1.15  Hot-fix for v0.1.14: measurement was getting stuck on
        "STARTING…" with 0 samples for any preset using a fractional
        max_bandwidth_hz (e.g. 29.253) when the trigger mode was
        MANUAL.  Root cause: 0.1.14 added the milli-Hz formatting
        helper to `echem_app/measurement.py` (used by drop-detect
        scripts) but missed the second copy of the
        `set_max_bandwidth` line in `cv_app/script_builder.py`,
        which is the path used by manual-trigger CV.  That copy was
        still emitting `set_max_bandwidth 29.253` — invalid
        MethodSCRIPT syntax — and the chip silently rejected the
        whole script, never starting the measurement.  Replaced
        both `set_max_bandwidth` lines in script_builder.py
        (CV and SWV builders) with the same `_bandwidth_lines()`
        helper so manual and drop-detect paths emit identical,
        chip-acceptable bandwidth syntax.

0.1.14  Made the "Dummy cell test" preset match PSTrace's auto-mode
        MethodSCRIPT byte-for-byte, after diffing the two scripts:
          - pgstat_mode      2  → 3   (HiZ/OCP — PSTrace's auto-pick)
          - max_bandwidth_hz 40 → 29.253 Hz (PSTrace's auto-pick)
          - acquisition_frac_autoadjust  → 50 (was missing entirely;
                                                PSTrace emits this line
                                                right after set_max_bandwidth)
          - pretreat_interval_s  0.1 → 0.2 s (PSTrace samples the 1-s
                                              equilibration every 200 ms)
        Implementation:
          - Added optional `acquisition_frac_autoadjust` field to
            CVParameters; defaults to None so other presets don't get
            the new line emitted unless they opt in.
          - Added `_format_bandwidth_hz` helper that emits sub-Hz
            values via the milli-Hz suffix (e.g. 29.253 → "29253m"),
            matching PSTrace's own formatting for line-by-line script
            diffing.
          - Replaced all three `set_max_bandwidth` callsites in
            measurement.py with `_bandwidth_lines()` so the
            bandwidth + acquisition_frac_autoadjust pair is always
            emitted together, properly indented.
          - Changed CVParameters.max_bandwidth_hz default from `40`
            (int) to `40.0` (float) so cv_params_from_preset's type
            casting preserves fractional Hz values from the JSON.
        Confirmed: parsing the Dummy cell test preset now emits
        exactly `set_max_bandwidth 29253m` followed by
        `set_acquisition_frac_autoadjust 50`.

0.1.13  Added a PSTrace-style "Select current range(s)" picker to the
        preset edit form (Manage presets modal).  Eight buttons span
        the EmStat4T's hardware ranges (1 nA → 10 mA).  Click an empty
        range to add it to the autorange set, click a selected range
        to mark it as the start range (▼), click the start range to
        remove it.  Replaces the old single text input.  Translates
        on save into the existing `current_range` / `auto_range_low` /
        `auto_range_high` / `enable_autoranging` fields, so the
        underlying preset JSON schema is unchanged.  Mobile-friendly
        sizing (48 px tiles) under 820 px.

0.1.12  Fixed "Device not detected" modal popping up on BLE sessions.
        `/api/status` was reporting the status of the registered DEFAULT
        device (USB EmStat4T) instead of the device the operator chose
        for the active session — so even with a successful BLE connect
        the status check would spam "USB auto-detect failed" every 5 s
        and the JS would pop the USB-flavored "Device not detected"
        modal.  Now `_palmsens_status()` reads `_STATE.device_record_id`
        first (the session's active device, BLE or USB) and only falls
        back to the registered default when no session has started.
        Side benefit: the log no longer fills with pointless USB scans
        when the operator is on BLE.

0.1.11  Removed the post-connect beep test — the beep's drain loop
        (`iter_data_packages`) was the source of every lock-hang we
        saw.  On some EmStat4T firmware revs the chip doesn't emit a
        clean end-of-script marker after the beep script, so the
        drain would loop forever while still holding _CONN_LOCK,
        blocking out every subsequent measurement with the
        "device busy" timeout error.  The connection is already fully
        validated by the open() handshake (abort_and_sync +
        get_device_type + get_firmware_version), and the operator has
        visual confirmation via the green "● Connected" pill in the
        sidebar.  Skipping the beep is a strict reliability win — no
        more stuck connection-test, no more "device busy" cascades.
0.1.10  Fixed "SerialException: multiple access on port" during
        measurement.  Root cause: `iter_measurement()` (the SSE
        generator that streams measurement events) was directly using
        the held serial connection without acquiring `_CONN_LOCK`.  If
        the operator clicked Connect (which holds _CONN_LOCK during
        its beep-drain), then advanced through Phases 1-2 and clicked
        Start measurement before the connect-test had finished, two
        threads would call read/write on the same pyserial Serial
        object at once and pyserial would raise the "multiple access"
        SerialException ~10 ms into the measurement.  /api/measure/
        start's `gen()` SSE generator now acquires _CONN_LOCK
        (10-second timeout) before calling iter_measurement and
        releases it in a finally block, so all serial I/O across all
        endpoints is properly serialized.  If a measurement is
        attempted while another operation holds the lock for >10 s,
        the operator gets a clear error event ("device busy with
        another operation, wait a few seconds and try again") instead
        of an opaque pyserial crash.
"""

__version__ = "0.1.15"
