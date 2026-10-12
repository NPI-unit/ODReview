"""Where global app files live. Imports nothing heavy: the installer window
uses it before numpy & co. are installed."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def app_support_dir() -> Path:
    if sys.platform == "darwin":
        d = Path.home() / "Library" / "Application Support" / "NKReview"
    elif sys.platform == "win32":
        d = Path(os.environ.get("APPDATA", Path.home())) / "NKReview"
    else:
        d = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "nkreview"
    d.mkdir(parents=True, exist_ok=True)
    return d
