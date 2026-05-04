# cv_app — usage

Python API + live plot for the PalmSens **EmStat4 / EmStat4T** potentiostat.

> **The EmStat4T DOES have a real programmatic API.** It's the MethodSCRIPT
> protocol over USB serial — what PSTrace itself uses internally. The vendor
> Python helpers in
> `MethodSCRIPT_Examples-master/Example_Python/Example_Python/palmsens/` give us
> direct read/write access to the device.  This package wraps those helpers so
> you don't need PSTrace and don't need cursor-recording automation.

## Install

```bash
cd /Users/poom/cv_app_project
pip install -r cv_app/requirements.txt        # matplotlib, numpy, pyserial
```

The package auto-finds the vendored `palmsens` helpers; no install needed.

## Quick start (with hardware)

```bash
# CV with all default parameters (uses the vendor example sweep)
python -m cv_app cv

# Custom CV — every CVParameters field is exposed as a CLI flag
python -m cv_app cv \
    --e_begin 0 --e_vtx1 0.5 --e_vtx2 -0.5 \
    --e_step 0.01 --scan_rate 0.1 --n_scans 2 \
    --save-csv outputs/run1.csv --features

# SWV
python -m cv_app swv --e_begin -0.3 --e_end 0.3 --frequency_hz 25
```

## Quick start (no hardware — replay)

You don't need the EmStat4T plugged in to develop or to test the live plot
and analysis pipeline. Replay any `console_example.py`-style text dump:

```bash
python -m cv_app cv \
    --replay MethodSCRIPT_Examples-master/Example_Python/Example_Python/cv_result.txt \
    --features --predict
```

Or run the bundled smoke test:

```bash
python -m cv_app.demo
```

## Python API

```python
from cv_app import CVParameters, run_cv, basic_features
from cv_app.analysis import predict_marker

params = CVParameters(
    e_begin=0.0, e_vtx1=0.5, e_vtx2=-0.5,
    e_step=0.01, scan_rate=0.1, n_scans=1,
    current_range="100u", enable_autoranging=True,
)

result = run_cv(params, live_plot=True, save_csv="outputs/run1.csv")

samples = result["samples"]                 # list[Sample]
features = basic_features(samples)          # dict[str, float]
prediction = predict_marker(features)       # dict (placeholder for now)
```

For UI integrations, use the streaming generator instead:

```python
from cv_app import iter_samples_cv

for s in iter_samples_cv(params):
    my_ui.push_point(s.potential_v, s.current_a)
    my_buffer.append(s)
```

## Drop-detect (auto-trigger placeholder)

```python
from cv_app.triggers import DropDetector

trigger = DropDetector()        # use_hardware=False — manual gate for now
trigger.wait_for_drop()         # press ENTER once you've placed the sample
run_cv(params)
```

The hardware-based detector slot is already wired in — replace the body of
`DropDetector.wait_for_drop()` with a small MethodSCRIPT that streams WE
current at low bandwidth and trips when |dI| > threshold (start from
`MethodSCRIPT_Examples-master/MethodSCRIPTs/MSExample016-Drop_detect.mscr`).

## Where to plug in your ML model

`cv_app/analysis.py::predict_marker(features)` is a stub.  Once you've
trained your model:

```python
# analysis.py
import joblib
_MODEL = joblib.load("models/my_marker_model.pkl")

def predict_marker(features):
    X = [[features.get(k, 0.0) for k in _MODEL.feature_names_in_]]
    pred = _MODEL.predict(X)[0]
    proba = _MODEL.predict_proba(X)[0].max()
    return {"marker": pred, "score": float(proba)}
```

The feature dict produced by `basic_features` is intentionally flat and
JSON-serializable so you can save it alongside the run for training data.

## Layout

```
cv_app/
  __init__.py        public API (CVParameters, run_cv, ...)
  __main__.py        enables `python -m cv_app`
  params.py          CVParameters / SWVParameters / Sample
  script_builder.py  generates MethodSCRIPT from params (no .mscr files needed)
  device.py          DeviceConnection — wraps vendor palmsens.* helpers
  runner.py          streaming + replay + run_cv / run_swv
  liveplot.py        PSTrace-style live matplotlib plot
  analysis.py        peak finder + feature extraction + ML hook
  triggers.py        DropDetector placeholder
  cli.py / demo.py   CLI + smoke test
```

## Verified

- `python -m cv_app cv --replay …/cv_result.txt --features --predict` →
  201 samples, anodic peak at +0.49 V, cathodic peak at −0.48 V.
- Generated CV / SWV MethodSCRIPT matches the structure of the vendor
  `example_cv.mscr` and `example_advanced_swv.mscr`.
- Live plot renders forward + reverse sweep correctly (see
  `cv_app/outputs/replay_plot.png`).
