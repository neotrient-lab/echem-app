# Neotrient Electrochemical Analysis — v0.2.0

**In-team alpha test release.**  Electrochemical analysis app for the
PalmSens EmStat4T (USB or Bluetooth Low Energy).  Supports **cyclic
voltammetry (CV)** and **square-wave voltammetry (SWV)**, with a
multi-phase wizard, drop-detect, live charts, AI inference, auto-saved
self-describing CSVs, and a mobile UI optimised for iPhone/iPad.
Validated against PSTrace 5.13.

This is an internal test build.  Please report bugs and UX feedback to
the team before sharing externally.

---

## What's new in v0.2.0

- **SWV technique support** — measure square-wave voltammetry alongside
  CV.  Three-trace live plot (forward / reverse / difference).  New
  `preset-swv-1` matches PSTrace's auto-pick chip configuration
  byte-for-byte.
- **Sequence / batch mode** — plan multiple samples and run them in
  one go.  Build batches up front (with per-batch method preset),
  then walk through measurement for each sample with the sequence
  overview always visible on Phase 1.
- **Two sample-identification forms** — *Clinical sample* (donor
  pouch, collection date, clinical site) for IVD workflows, or
  *Standard solution* (sample type, concentration, prep date, lot,
  solvent) for calibration / QC workflows.  Picked once in
  Configuration; the form layout swaps everywhere it appears.
- **Self-describing CSV output** — every measurement CSV now starts
  with a `# key=value` metadata header (experiment, operator, device,
  sample, every preset parameter) so the file is meaningful without a
  sidecar.
- **Method preset editor** — technique-aware (CV vs SWV) so SWV
  presets can no longer accidentally carry `n_scans` / `e_vtx1`
  fields.  Protected presets (the team-supplied originals) require an
  admin password to delete.

See [`CHANGELOG.md`](./CHANGELOG.md) for the full version log.

---

## For team members (no terminal needed)

If you just want to **install and use** the app, follow
[**INSTALL.md**](./INSTALL.md).  It walks you through everything with
screenshots-style instructions — no Python or terminal experience
required.  The short version:

1. Install Python from <https://python.org> (one-time).
2. Double-click `setup_mac.command` (macOS) or `setup_windows.bat`
   (Windows).
3. Double-click `start_app.command` / `start_app.bat` to run.

Your browser opens at `http://127.0.0.1:8080`.

For day-to-day operation see [**USAGE.md**](./USAGE.md) — workflow
recipes for both Single-sample and Sequence/batch modes, plus CV and
SWV runs.

---

## For developers (Quick start with terminal)

### macOS / Linux

```bash
cd alpha_2
python3 -m venv .venv
source .venv/bin/activate
pip install -r echem_app/requirements.txt
ECHEM_HOST=0.0.0.0 ECHEM_PORT=8080 ECHEM_MDNS=1 python -m echem_app.app
```

The app starts on **<http://127.0.0.1:8080>** and your default browser
opens automatically.  Phones / tablets on the same WiFi can also reach
the LAN URL printed in the terminal.

Stop the app with **Ctrl-C**.

### Windows

```cmd
cd alpha_2
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
alpha_2/
├── INSTALL.md              Beginner-friendly install & use guide
├── USAGE.md                Operator workflow recipes (CV/SWV, Single/Sequence)
├── CHANGELOG.md            Version log
├── README.md               (this file — overview)
├── setup_mac.command       Double-click to install on macOS (one-time)
├── start_app.command       Double-click to start the app on macOS
├── setup_windows.bat       Double-click to install on Windows (one-time)
├── start_app.bat           Double-click to start the app on Windows
├── update_github.command   For maintainers — push updates to the team repo
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
├── cv_app/                 Stand-alone CV / SWV analysis library
└── vendor/                 Bundled PalmSens MethodSCRIPT runtime
```

> **Note:** `PASSWORDS.txt`, `.venv/`, and `echem_app/exports/` are
> intentionally **not** in the repository (they are excluded by
> `.gitignore`).  Each user generates their own venv during setup and
> keeps their own measurement CSVs locally.

Auto-saved measurement files land under
`~/Documents/Neotrient/results/<YYYY-MM-DD>/HHMMSS_<sample_id>.csv`
(self-describing — the `# key=value` header carries every parameter).

---

## Mobile / tablet (same WiFi)

After launching with `ECHEM_HOST=0.0.0.0`, the terminal prints a line:

```
LAN mode enabled — phones / tablets on the same WiFi can reach the app at:
http://192.168.1.42:8080
```

Open that URL in any browser on your phone or tablet.  The mobile UI
collapses the workflow sidebar into a bottom **Back / Next** bar and
inlines the manual sample-entry form on Phase 1 (no QR camera — Safari
blocks it on plain HTTP).

---

## Hardware

- **Potentiostat:** PalmSens EmStat4T (USB or BLE / PS-539B).
- **Cell:** dummy cell for sanity checks, screen-printed electrode
  (SPE) or 3-electrode setup for real samples.
- **Communication:** MethodSCRIPT over USB serial or BLE Nordic UART
  service.

---

## License

Proprietary — Neotrient internal use only.  Alpha-test build.
