"""Command-line entry point.

    python -m cv_app cv  --e_begin 0 --e_vtx1 0.5 --e_vtx2 -0.5 --scan_rate 0.1
    python -m cv_app swv --e_begin -0.3 --e_end 0.3 --frequency_hz 25
    python -m cv_app cv  --replay path/to/cv_result.txt   # offline test
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import fields
from pathlib import Path

from .analysis import basic_features, predict_marker
from .params import CVParameters, SWVParameters
from .runner import run_cv, run_swv


def _add_dataclass_args(parser: argparse.ArgumentParser, dc_cls):
    """Auto-expose every field of a dataclass as a CLI flag."""
    for f in fields(dc_cls):
        if f.type in (bool,) or f.type == "bool":
            parser.add_argument(f"--{f.name}", action="store_true",
                                default=f.default,
                                help=f"(default: {f.default})")
        else:
            kw = {}
            if f.default is not None:
                kw["default"] = f.default
            kw["type"] = (
                int if f.type in (int, "int") else
                float if f.type in (float, "float") else
                str
            )
            parser.add_argument(f"--{f.name}", help=f"(default: {f.default})", **kw)


def _build_dataclass(args, dc_cls):
    """Materialize a dataclass from parsed CLI args."""
    kwargs = {}
    for f in fields(dc_cls):
        if hasattr(args, f.name):
            v = getattr(args, f.name)
            if v is not None:
                kwargs[f.name] = v
    return dc_cls(**kwargs)


def main(argv=None):
    logging.basicConfig(level=logging.INFO,
                        format="[%(name)s] %(message)s")

    # Common flags shared by all subcommands.  Using a parent parser so the
    # flags work *after* the subcommand name as well as before, e.g.
    #     python -m cv_app cv --replay PATH --no-plot
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--port", help="Serial port (auto-detect if omitted)")
    common.add_argument("--baudrate", type=int,
                        help="Serial baudrate (default: device-specific)")
    common.add_argument("--no-plot", dest="live_plot", action="store_false",
                        default=True,
                        help="Disable the live plot window")
    common.add_argument("--save-csv", help="Save the run to this CSV path")
    common.add_argument("--replay", help="Replay a console_example.py text "
                        "dump instead of talking to hardware")
    common.add_argument("--features", action="store_true",
                        help="Print extracted features after the run")
    common.add_argument("--predict", action="store_true",
                        help="Run the (placeholder) ML predictor on the features")

    parser = argparse.ArgumentParser(prog="cv_app",
                                     description="EmStat4 CV / SWV runner",
                                     parents=[common])

    sub = parser.add_subparsers(dest="kind", required=True)
    p_cv = sub.add_parser("cv", help="Run cyclic voltammetry",
                          parents=[common])
    _add_dataclass_args(p_cv, CVParameters)

    p_swv = sub.add_parser("swv", help="Run square-wave voltammetry",
                           parents=[common])
    _add_dataclass_args(p_swv, SWVParameters)

    args = parser.parse_args(argv)

    if args.kind == "cv":
        params = _build_dataclass(args, CVParameters)
        result = run_cv(
            params,
            port=args.port,
            baudrate=args.baudrate,
            live_plot=args.live_plot,
            save_csv=args.save_csv,
            replay=args.replay,
        )
    else:
        params = _build_dataclass(args, SWVParameters)
        result = run_swv(
            params,
            port=args.port,
            baudrate=args.baudrate,
            live_plot=args.live_plot,
            save_csv=args.save_csv,
            replay=args.replay,
        )

    print(f"Captured {result['n_samples']} samples ({result['kind']}).")

    if args.features or args.predict:
        feats = basic_features(result["samples"])
        if args.features:
            print("Features:")
            print(json.dumps(feats, indent=2, default=str))
        if args.predict:
            pred = predict_marker(feats)
            print("Prediction:")
            print(json.dumps(pred, indent=2, default=str))

    return 0


if __name__ == "__main__":
    sys.exit(main())
