# Neotrient · Echem Analysis App

A locally-run lab workstation app that drives a PalmSens EmStat4T, runs a
CV measurement, scores it with an AI model, and exports a signed `.zip`
bundle per sample.

It is the **in-lab analysis half** of the two-system pipeline; the other
half is *Lab Reception v9* (browser app, not in this repo).

---

## Run

```bash
cd /Users/poom/cv_app_project
source .venv/bin/activate
pip install -r echem_app/requirements.txt
python -m echem_app.app
```

The browser opens at <http://127.0.0.1:5050>.  No installer, no Electron,
no cloud — Flask exists only because a browser can't talk to USB.

> **Why port 5050, not 5000?** macOS Monterey+ uses port 5000 for AirPlay
> Receiver, which intercepts requests with HTTP 403 before Flask sees them.
> Override with `ECHEM_PORT=8080 python -m echem_app.app` if you need a
> different port.

## What it does

| Phase | Screen | Backend |
|-------|--------|---------|
| 0 | Setup form (operator, sensor, AI model, ions) | `POST /api/session/start` |
| 1 | QR scan via webcam (jsQR) → LIMS lookup | `GET  /api/sample/lookup?sample_id=…` |
| 2 | "Drop on sensor" + arm hardware drop-detect | (entry only) |
| 3 | Live Plotly chart, point-by-point | `POST /api/measure/start` (SSE) |
| 4 | AI predictions per ion + linear sanity check | `POST /api/predict` |
| 5 | Notes, sign-off, export `.zip` (+ optional LIMS POST) | `POST /api/save` |

## Architecture

```
echem_app/
├── app.py            ← Flask + SSE endpoints (this is the main entry point)
├── measurement.py    ← wraps cv_app + builds drop-detect MethodSCRIPT
├── ai_model.py       ← loads .pt / .pkl from models/, falls back to stub
├── audit.py          ← append-only audit log
├── lims_client.py    ← POST stub
├── exporter.py       ← .zip bundle builder
├── templates/index.html
├── static/{style.css, app.js}
├── models/           ← drop your trained model here (resattn.pt or *.pkl)
└── exports/          ← all output bundles + audit log land here
```

The whole measurement layer is reused from the sibling `cv_app/` package
— this app is a chassis around it.

## Drop-detect

Phase 2 uses the EmStat4T's **hardware drop-detect** (GPIO_D0 enable +
GPIO_D1 sense) as documented in
`MethodSCRIPT_Examples-master/MethodSCRIPTs/MSExample016-Drop_detect.mscr`.
The chip waits for the drop on its own; the app just sends one combined
"arm-then-CV" MethodSCRIPT and streams the resulting samples back.  No
software polling, no false triggers from cable noise.

## AI model hookup

By default the app boots in **stub** mode — `ai_model.predict()` returns
deterministic plausible numbers per ion, seeded by sample ID.  Drop a
real model in:

* PyTorch → `echem_app/models/resattn.pt`
* sklearn / xgboost → `echem_app/models/resattn.pkl`

…or set `ECHEM_MODEL_PATH=/abs/path/to/your_model.pt` in the env.

The app calls `model.predict(X)` (or `model(tensor)` for torch) where `X`
is a feature vector built from `cv_app.analysis.basic_features()` keys in
sorted order.  Adjust `_real_predict()` in `ai_model.py` once your model
expects a specific input shape.

## LIMS POST

Optional.  Set `LIMS_URL=https://your-lims.example.com/intake` before
launching; otherwise the app simply skips LIMS and you still get the
local `.zip`.

## Replay mode (for development)

If you don't have the EmStat4T plugged in, click **"Run with replay
(no hardware)"** in Phase 2.  The app re-streams the existing
`MethodSCRIPT_Examples-master/.../cv_result.txt` so you can demo the
whole UI / SSE / Plotly / AI / export pipeline without hardware.

## What's NOT here

No login, no DB, no Electron, no PWA, no animated number counters.  This
is a wired-up lab PC, not a mobile app.
