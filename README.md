# Neotrient Electrochemical Analysis — v0.1.15

**In-team alpha test release.**  Cyclic-voltammetry app for the
PalmSens EmStat4T (USB or Bluetooth Low Energy), with a 6-phase wizard,
drop-detect, live charts, AI inference, auto-saved CSVs, and a mobile
UI optimised for iPhone/iPad.  Validated against PSTrace 5.13 on both
dummy cells and Fe(NO₃)₃ samples — between-system spread is within the
5% acceptance threshold under controlled conditions.

This is an internal test build.  Please report bugs and UX feedback to
the team before sharing externally.

---

## For team members (no terminal needed)

If you just want to **install and use** the app, follow
[**INSTALL.md**](./INSTALL.md). It walks you through everything with
screenshots-style instructions — no Python or terminal experience
required. The short version:

1. Install Python from <https://python.org> (one-time).
2. Double-click `setup_mac.command` (macOS) or `setup_windows.bat`
   (Windows).
3. Double-click `start_app.command` / `start_app.bat` to run.

Your browser opens at `http://127.0.0.1:8080`.

---

## For developers (Quick start with terminal)

### macOS / Linux

```bash
cd alpha_1
python3 -m venv .venv
source .venv/bin/activate
pip install -r echem_app/requirements.txt
ECHEM_HOST=0.0.0.0 ECHEM_PORT=8080 ECHEM_MDNS=1 python -m echem_app.app
```

The app starts on **<http://127.0.0.1:8080>** and your default browser
opens automatically.  Phones/tablets on the same WiFi can also reach
the LAN URL printed in the terminal.

Stop the app with **Ctrl-C**.

### Windows

```cmd
cd alpha_1
python -m venv .venv
.venv\Scripts\activate
pip install -r echem_app\requirements.txt
set ECHEM_HOST=0.0.0.0
set ECHEM_PORT=8080
set ECHEM_MDNS=1
python -m echem_app.app
```

---

## What's in this folder

```
alpha_1/
├── INSTALL.md              Beginner-friendly install & use guide for the team
├── README.md               (this file — overview)
├── setup_mac.command       Double-click to install on macOS (one-time)
├── start_app.command       Double-click to start the app on macOS
├── setup_windows.bat       Double-click to install on Windows (one-time)
├── start_app.bat           Double-click to start the app on Windows
├── requirements.txt        Pinned Python deps (mirrored from echem_app/)
├── echem_app/              Flask web app (UI + API + measurement engine)
│   ├── app.py              Entry point
│   ├── ble_transport.py    BLE adapter (NUS via bleak)
│   ├── connection.py       USB / BLE selection
│   ├── measurement.py      MethodSCRIPT generation + SSE streaming
│   ├── ai_model.py         Inference registry
│   ├── auto_save.py        Auto-save CSV per measurement
│   ├── exporter.py         Bundle export
│   ├── audit.py            Append-only audit log
│   ├── config_store.py     Presets / devices / models JSON
│   ├── lims_client.py      LIMS POST stub
│   ├── data/               Default presets, devices, models JSON
│   ├── models/             AI model registry / weights
│   ├── templates/          index.html (single-page UI)
│   ├── static/             app.js, style.css, splash assets
│   └── requirements.txt    Pinned Python deps
├── cv_app/                 Stand-alone CV analysis library
└── vendor/                 Bundled PalmSens MethodSCRIPT runtime
```

> **Note:** `PASSWORDS.txt`, `.venv/`, and `echem_app/exports/` are
> intentionally **not** in the repository (they are excluded by
> `.gitignore`). Each user generates their own venv during setup and
> keeps their own measurement CSVs locally.

Auto-saved measurement CSVs land under
`echem_app/exports/<YYYY-MM-DD>/sample_<id>_*.csv`.

---

## Mobile / tablet (same WiFi)

After launching with `ECHEM_HOST=0.0.0.0`, the terminal prints a line:

```
LAN mode enabled — phones / tablets on the same WiFi can reach the app at:
http://192.168.1.42:8080
```

Open that URL in any browser on your phone or tablet.  The mobile UI
collapses the workflow sidebar into a bottom **Prev / Next** bar and
inlines the manual sample-entry form on Phase 1 (no QR camera — Safari
blocks it on plain HTTP).

---

## What changed from the 0.7.x line

This alpha is a major UX pass over the working 0.7.3 build.  Key fixes:

### Mobile UI
- Workflow sidebar fully hidden on phones; bottom **Prev / Next** nav
  takes over.  Step label updates per phase.
- Phase 1 manual sample entry is inline (no extra "Manual entry" tap).
- QR scanner card and `confirm-qr` preview hidden on mobile (camera
  doesn't work on http://).
- Each phase fits one viewport — `.working` owns its own scroll.
- Weather-app typography rhythm: bigger title, lighter subtitle,
  uppercase muted section labels.
- Help button is a clean 36px circle (was being squashed into an oval).
- Buttons centered, side-by-side instead of stretched full-width.
- Anomaly / review banners trimmed to one line.

### Save page
- Manual export controls removed — measurements are auto-saved as they
  finish.
- Phase 5 now shows: Session summary (counters), Activity log
  (chronological save / re-measure / delete events with timestamps),
  Sample details, optional Notes, and a **Done** button (mobile + desktop).

### Analysis page
- Per-cycle filter is honored server-side — Box / Swarm distributions
  finally update when you tick / un-tick cycles.
- Re-running analysis with a subset of samples narrows the comparison
  chart to that subset.
- Un-ticks in the comparison table now persist across re-renders.
- Extracted features render as a clean two-column table (was a JSON
  dump).
- All checkboxes in #screen-4 unified at 16×16 with indigo accent.

### Workflow correctness
- "Run next sample" fully resets Phase 1 state (header pill, inline
  form, scanned image, `state.sample`).
- "Re-measurement" on Phase 5 batch list now correctly shows the
  sample being re-measured on Phase 1 (was showing the most recently
  confirmed one).
- Deleting a sample on the analysis page **also unlinks the CSV on
  disk** (server-side glob unlink) and logs a 'deleted' event.
- Preset-change on Phase 0 re-POSTs `/api/session/start` so the server
  uses the new preset (was running stale preset from initial Connect).

### Layout
- Chart and QR-frame heights are viewport-aware (`clamp()`), so a
  1366×768 monitor doesn't have to scroll to see Phase 1/3.
- New short-viewport CSS tier `(max-height: 820px)` trims paddings and
  the load-illustration on smaller desktops.

### Server
- `DELETE /api/session/samples/<sid>` actually removes files now (CSV
  + log + zip glob) and reports `files_removed` / `files_failed`.
- Per-cycle predictions honor `requested_cycles`.

### Drop-detect (this release)
- The v0.4.0 voltage-OCP drop-detect script is restored exactly
  (`baseline_seconds=1`).  An earlier in-development bump to 3 s was
  reverted — the original 1-second baseline is the version that worked
  on test cells.
- `method-2` preset's `voltage_threshold_mv` is 30 mV (matches the
  Standard preset and method-1).

---

## Known issues for alpha test

- iPhone 15 Pro Max and iPad Pro have been the primary mobile test
  targets.  Older / smaller screens may need additional polish.
- Drop detect still depends on a stable OCP baseline; a fully dry SPE
  with no liquid in the well can sometimes ride the noise floor.  In
  that case, switch to a manual-trigger preset (e.g. `method-1`).
- `_STATE.preset` is a singleton — if two operators run the app from
  different browsers against the same backend, they'll trample each
  other's preset selection.

---

## License

Proprietary — Neotrient internal use only.  Alpha-test build.
