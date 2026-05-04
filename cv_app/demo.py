"""End-to-end smoke test you can run today.

This script exercises the full pipeline (script generation, streaming, live
plot, feature extraction, ML hook) WITHOUT requiring the EmStat4 to be
attached.  It re-streams the data from `cv_result.txt` that PSTrace /
console_example.py already produced.

Run it with the project venv:

    python -m cv_app.demo --replay PATH_TO/cv_result.txt

When you have the device attached, run a real CV instead:

    python -m cv_app cv --e_begin 0 --e_vtx1 0.5 --e_vtx2 -0.5 \
        --scan_rate 0.1 --save-csv outputs/run1.csv
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .analysis import basic_features, predict_marker
from .params import CVParameters
from .runner import run_cv
from .script_builder import build_cv_script


def main(argv=None):
    logging.basicConfig(level=logging.INFO,
                        format="[%(name)s] %(message)s")
    p = argparse.ArgumentParser(description="cv_app demo / smoke test")
    p.add_argument("--replay",
                   default=str(
                       Path(__file__).resolve().parent.parent
                       / "MethodSCRIPT_Examples-master"
                       / "Example_Python"
                       / "Example_Python"
                       / "cv_result.txt"
                   ),
                   help="Path to a console_example-style text dump.")
    p.add_argument("--no-plot", dest="live_plot", action="store_false")
    p.add_argument("--save-csv", default=None)
    args = p.parse_args(argv)

    params = CVParameters(
        e_begin=0.0,
        e_vtx1=0.5,
        e_vtx2=-0.5,
        e_step=0.01,
        scan_rate=0.1,
        n_scans=1,
    )

    print("Generated MethodSCRIPT for these parameters:\n")
    print(build_cv_script(params))

    print("=" * 70)
    print("DEMO == REPLAY MODE.  This re-streams an OLD text dump from disk.")
    print("It does NOT measure your sensor — drop a different sample, you")
    print("will still see the same curve.  For a REAL measurement run:")
    print("    python -m cv_app cv  [--port /dev/cu.usbmodemXXX]")
    print("=" * 70)
    print(f"\nReplay source: {args.replay}\n")
    result = run_cv(
        params,
        replay=args.replay,
        replay_pace_s=0.03,          # ~30 Hz so you actually SEE the streaming
        live_plot=args.live_plot,
        save_csv=args.save_csv,
    )

    print(f"\nCaptured {result['n_samples']} samples.")
    feats = basic_features(result["samples"])
    print("\nExtracted features:")
    print(json.dumps(feats, indent=2, default=str))

    pred = predict_marker(feats)
    print("\nPlaceholder ML prediction:")
    print(json.dumps(pred, indent=2, default=str))

    return 0


if __name__ == "__main__":
    sys.exit(main())
