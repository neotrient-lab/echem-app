"""Allow `python -m cv_app ...` to invoke the CLI."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
