"""The default study 'TRIBAL (Default)' becomes 'Standard' once, keeping its
folder and types; the last-opened setting follows; an existing 'Standard'
is never overwritten."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CHECKS = 0


def check(cond: bool, label: str) -> None:
    global CHECKS
    CHECKS += 1
    if not cond:
        raise SystemExit(f"FAIL: {label}")
    print(f"  ok  {label}")


def main() -> int:
    tmp = tempfile.TemporaryDirectory()
    support = Path(tmp.name)
    from erysreview import persistence, profile as pm
    persistence.app_support_dir = lambda: support
    pm.app_support_dir = lambda: support
    from erysreview.persistence import AppSettings

    old = pm.ProfileData(name="TRIBAL (Default)")
    old.import_folder = "/Volumes/STICK/Bilder"
    old.target_cells = 1234
    pm.save_profile(old)
    st = AppSettings(last_profile_name="TRIBAL (Default)"); st.save()
    check(pm.migrate_default_name(st), "the old default study is renamed")
    new = pm.load_profile("Standard")
    check(new is not None and new.import_folder == "/Volumes/STICK/Bilder" and new.target_cells == 1234,
          "…with its folder and values")
    check("TRIBAL (Default)" not in pm.list_profiles(), "…and the old file is gone")
    check(AppSettings.load().last_profile_name == "Standard", "the last-opened study follows")
    check(not pm.migrate_default_name(st), "a second start changes nothing")
    pm.save_profile(pm.ProfileData(name="TRIBAL (Default)"))
    check(not pm.migrate_default_name(st) and "TRIBAL (Default)" in pm.list_profiles()
          and pm.load_profile("Standard").target_cells == 1234, "an existing 'Standard' is never overwritten")
    check(pm.ensure_default_profile().name == "Standard", "a fresh default is called 'Standard'")
    tmp.cleanup()
    print(f"OK — {CHECKS} checks: the default study is called 'Standard'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
