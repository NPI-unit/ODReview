"""Self-test after installing: does everything ErysReview needs work here?

    ./.venv/bin/python tools/check_install.py

One line per check, made to be read by people and by the installer window:

    CHECK <ok|warn|fail> <name> <detail>
    RESULT <ok|warn|fail>

'warn' does not stop the app (e.g. no GPU — Cellpose then runs on the CPU,
slower); 'fail' does. Exit code 0 unless something failed. Nothing is
written anywhere: the Qt check opens no window (offscreen), Cellpose loads
no model, the app's settings are not touched.
"""

from __future__ import annotations

import importlib
import os
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

RESULTS: list[str] = []


def report(state: str, name: str, detail: str = "") -> None:
    RESULTS.append(state)
    print(f"CHECK {state} {name} {detail}".rstrip(), flush=True)


def check_python() -> None:
    v = sys.version_info
    ok = (v.major, v.minor) == (3, 12)
    report("ok" if ok else "fail", "python", f"{platform.python_version()} ({sys.executable})"
           + ("" if ok else " — Python 3.12 needed"))


PACKAGES = [("numpy", "numpy"), ("scipy", "scipy"), ("pandas", "pandas"), ("skimage", "scikit-image"),
            ("PIL", "pillow"), ("tifffile", "tifffile"), ("matplotlib", "matplotlib"), ("openpyxl", "openpyxl"),
            ("torch", "torch"), ("cellpose", "cellpose"), ("PySide6", "PySide6-Essentials")]


def check_packages() -> bool:
    from importlib import metadata
    all_ok = True
    for mod, dist in PACKAGES:
        try:
            importlib.import_module(mod)
            try:
                ver = metadata.version(dist)
            except metadata.PackageNotFoundError:
                ver = getattr(sys.modules[mod], "__version__", "?")
            report("ok", f"package:{dist}", ver)
        except Exception as e:  # noqa: BLE001
            report("fail", f"package:{dist}", f"{type(e).__name__}: {e}")
            all_ok = False
    return all_ok


def check_qt() -> None:
    """Can Qt start (platform plugin found)? Runs offscreen; also clears the
    macOS 'hidden' flag on the plugins first, as the app does at start."""
    try:
        from erysreview.qt.app import unhide_qt_plugins
        n = unhide_qt_plugins()
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication, QWidget
        app = QApplication.instance() or QApplication(["check"])
        w = QWidget(); w.resize(10, 10); w.show(); app.processEvents(); w.close()
        from PySide6 import __version__ as qtv
        report("ok", "qt", f"PySide6 {qtv}" + (f" (plugins unhidden: {n})" if n else ""))
    except Exception as e:  # noqa: BLE001
        report("fail", "qt", f"{type(e).__name__}: {e}")


def check_gpu() -> None:
    try:
        from erysreview.cellpose_runner import detect_gpu_backend
        b = detect_gpu_backend()
        if b == "cpu":
            report("warn", "gpu", "none — Cellpose runs on the CPU (slower)")
        else:
            report("ok", "gpu", b)
    except Exception as e:  # noqa: BLE001
        report("warn", "gpu", f"{type(e).__name__}: {e}")


def check_model() -> None:
    p = Path.home() / ".cellpose" / "models" / "cpsam"
    if p.is_file() and p.stat().st_size > 100_000_000:
        report("ok", "model:cpsam", f"{p.stat().st_size / 1e9:.1f} GB")
    else:
        report("warn", "model:cpsam", "missing — downloaded on the first Cellpose run (~1.2 GB, needs internet)")


def check_app() -> None:
    try:
        import erysreview
        importlib.import_module("erysreview.qt.app")
        report("ok", "app", f"ErysReview {erysreview.__version__}")
    except Exception as e:  # noqa: BLE001
        report("fail", "app", f"{type(e).__name__}: {e}")


def main() -> int:
    check_python()
    if check_packages():
        check_qt()
        check_gpu()
        check_app()
    check_model()
    result = "fail" if "fail" in RESULTS else "warn" if "warn" in RESULTS else "ok"
    print(f"RESULT {result}", flush=True)
    return 1 if result == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
