"""Entry point: ``python -m erysreview`` starts the app (Qt front end)."""

from .qt.app import main


if __name__ == "__main__":
    raise SystemExit(main())
