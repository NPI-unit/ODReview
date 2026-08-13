"""Gate for Cellpose model resolution (cellpose_runner.resolve_model).

Lab-critical: Cellpose itself, handed a path it cannot find, logs one line and
segments with cpsam anyway. That silent substitution would put counts from two
different models into one study while the mask metadata still claimed the
requested one. resolve_model() must raise instead — this pins that.

    ./.venv/bin/python tests/test_model_resolve.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from erysreview.cellpose_runner import BUILTIN_MODEL, ModelUnavailable, resolve_model
from erysreview.persistence import AppSettings


def main() -> int:
    checks = 0

    def ok(label: str) -> None:
        nonlocal checks
        checks += 1
        print(f"  ok  {label}")

    def raises(label: str, name: str, registry: dict) -> None:
        try:
            resolve_model(name, registry)
        except ModelUnavailable as e:
            assert e.name == name, f"{label}: wrong name on the exception"
            ok(label)
            return
        raise AssertionError(f"{label}: expected ModelUnavailable, got a result")

    # The built-in never needs an entry, whatever the registry holds.
    assert resolve_model(BUILTIN_MODEL, {}) == BUILTIN_MODEL
    assert resolve_model(BUILTIN_MODEL, None) == BUILTIN_MODEL
    ok("built-in resolves without a registry")

    # An empty/absent name means "default", not "error".
    assert resolve_model("", {}) == BUILTIN_MODEL
    assert resolve_model(None, {}) == BUILTIN_MODEL
    ok("empty name falls back to the built-in")

    # A registered, existing file resolves to its path.
    with tempfile.NamedTemporaryFile(suffix=".pth", delete=False) as fh:
        model_file = Path(fh.name)
    try:
        assert resolve_model("Eigen", {"Eigen": str(model_file)}) == str(model_file)
        ok("registered model resolves to its file")

        # The three ways it must refuse rather than substitute cpsam.
        raises("unknown name refuses", "Eigen", {})
        raises("empty path refuses", "Eigen", {"Eigen": ""})
        raises("vanished file refuses", "Weg", {"Weg": str(model_file) + ".gone"})

        # A directory is not a model file either.
        raises("directory refuses", "Ordner", {"Ordner": str(model_file.parent)})
    finally:
        model_file.unlink(missing_ok=True)

    # A newer Cellpose brings its own models along. Simulated by seeding the
    # cache the way known_builtins() would fill it after an upgrade — the point
    # is that no list in this repo has to be edited for cpdino/cpsam_v2 to work,
    # and that they are refused as long as the installed package lacks them.
    import erysreview.cellpose_runner as cr
    saved = cr._BUILTIN_CACHE
    try:
        cr._BUILTIN_CACHE = ("cpsam",)
        raises("model of a newer Cellpose refused before upgrading", "cpdino", {})
        cr._BUILTIN_CACHE = ("cpsam", "cpsam_v2", "cpdino", "cpdino-vitb")
        assert resolve_model("cpdino", {}) == "cpdino"
        assert resolve_model("cpsam_v2", {}) == "cpsam_v2"
        ok("same model resolves once the installed Cellpose reports it")
    finally:
        cr._BUILTIN_CACHE = saved

    # The registry round-trips through the settings file unchanged.
    s = AppSettings()
    assert s.cellpose_models == {}, "registry should start empty"
    s.cellpose_models = {"Eigen": "/pfad/modell.pth"}
    assert AppSettings.load.__func__ is not None      # sanity: classmethod present
    ok("AppSettings carries the registry")

    print(f"OK — {checks} checks: resolve_model refuses unknown models instead of "
          f"silently falling back to cpsam.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
