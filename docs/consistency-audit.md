# ErysReview — Consistency & Robustness Audit

**Date:** 2026-06-14
**Scope:** Whole-repo review across four axes — **cross-platform parity**, **error handling & crashes**, **state-refresh consistency**, **naming & structure** — agreed up front. The six user-reported issues are treated as *anchor findings*; this report also lists their *siblings* (the same pattern elsewhere).
**Status:** Read-only audit. **No source files were modified.** Fixes happen only after review.

## How to read this

Severity legend:

| | Meaning |
|---|---|
| 🔴 **Critical** | Breaks the app for a whole platform / data-integrity risk. |
| 🟠 **High** | Frequent or user-visible breakage; should fix this round. |
| 🟡 **Medium** | Real inconsistency or latent bug; fix soon. |
| ⚪ **Low** | Polish / hygiene / documentation. |

`★ anchor` marks one of your six reported items. Every finding has a `file:line` and a recommended fix that matches a decision already made in our review where applicable.

---

## Anchor → finding map

| Your item | Finding(s) | Decision reached |
|---|---|---|
| Windows Cellpose "crash" at high diameter | **CP-1** | Diagnosed as OpenMP duplicate-runtime hang (not diameter). Fix via conda-forge stack. |
| Install conda-forge via `.install` | **CP-1, CP-2** | Windows installer → conda-forge channel + consented Miniforge bootstrap. Mac unchanged. |
| Package: choose which picture | **NS-3** | Hierarchical per-image checklist, default all-checked. |
| Windows: sidebar not updating on import | **CP-6 / SR-1** | Harden `list_images` + log; one Windows run confirms. |
| Icon + name in the bottom bar | **CP-4** (Win), **CP-5** (Mac) | AppUserModelID + minimal `.app` bundle. |
| Own "U-Segmentation" | — | **Deferred this round.** |
| *(surfaced in review)* UI freeze during segmentation | **EH-1** | Move inference to a background thread now. |

---

## Axis 1 — Cross-platform parity

### 🔴 CP-1 ★ Windows hangs forever on first Cellpose call — duplicate OpenMP runtime
**Where:** [install.bat:73-87](../install.bat), [requirements.txt](../requirements.txt), [cellpose_runner.py:201](../erysreview/cellpose_runner.py)
**What:** Windows install does `conda create -n erys` then **pip-installs** torch/cellpose into it. The conda/MKL-linked numpy and pip's torch each ship their own `libiomp5md.dll`; on Windows two copies in one process deadlock on the first `model.eval()` (**OMP Error #15**, confirmed in your console). macOS is immune — its Homebrew/MPS stack has a single `libomp`. This — **not** the diameter value (it was 200, well within the legal 5–500) — is why Windows never completes a segmentation while Mac is fine.
**Fix (agreed):** Rewrite **`install.bat` only** to install `pytorch`, `numpy`, `cellpose` (4.1.1 is on conda-forge) **from the conda-forge channel** so a single OpenMP runtime is used. **Recreate the `erys` env cleanly** rather than reuse an existing pip-built one, or the conflict survives. No `KMP_DUPLICATE_LIB_OK` flag (it risks silently wrong numerics — unacceptable for a counting tool).

### 🟠 CP-2 ★ Miniforge is not bootstrapped — installer dead-ends
**Where:** [install.bat:41-50](../install.bat)
**What:** If conda isn't found, the installer prints "go download Miniforge" and exits. Non-technical lab users are stranded.
**Fix (agreed):** Detect missing conda → prompt **"download and install Miniforge now? [Y/N]"** → on yes, fetch the official installer and run it silently, then continue. Consented and visible.

### 🟡 CP-3 conda-forge `pytorch` is CPU-only — GPU silently unused on Windows/Linux
**Where:** [cellpose_runner.py:188-209](../erysreview/cellpose_runner.py) (`detect_gpu_backend`, `_get_model`), [requirements.txt:22-36](../requirements.txt)
**What:** The default conda-forge `pytorch` is the CPU build, so on an NVIDIA Windows box `detect_gpu_backend()` returns `cpu` and `use_gpu` is silently ignored. Combined with CP-1's fix, Windows runs **CPU inference** — correct but slow (see EH-1). The current `requirements.txt` comment about a separate CUDA wheel won't apply once we move off pip.
**Fix:** Document the CPU default; offer an opt-in conda-forge `pytorch-gpu`/CUDA path in `install.bat` for NVIDIA users. Make the Settings GPU label honest about "CPU build — no GPU."

### 🟠 CP-4 ★ Windows taskbar shows the generic Python icon/name
**Where:** [app.py:132-158](../erysreview/app.py) (`_apply_window_icon`)
**What:** `iconbitmap(default=icon.ico)` sets the *title-bar* icon, but the taskbar groups under `python.exe` and shows the Python icon because no **AppUserModelID** is set.
**Fix (agreed):** Call `ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Erys.Review")` **before** the root window is created. (Same `ctypes.windll` idiom already used at [dialogs.py:976](../erysreview/dialogs.py).) Assets are fine — `icon.ico` is multi-res.

### 🟠 CP-5 ★ macOS Dock shows "Python", not "ErysReview"
**Where:** [install.command:84-99](../install.command), [app.py:132-158](../erysreview/app.py), `assets/` (no `.icns`)
**What:** Launched as `python -m erysreview`, the Dock shows the Python rocket and name. Tk's `iconphoto` changes neither the Dock icon nor the menu-bar/Dock *name* for an unbundled process.
**Fix (agreed):** Build a **minimal `.app` bundle** in `install.command`: generate `icon.icns` from the 512px PNG, write `Info.plist` (`CFBundleName=ErysReview`, `CFBundleIconFile`), launcher invokes **the `.venv` python** (the Homebrew-Tk one — must not fall back to system python, or the Tahoe Tk segfault returns). Locally built ⇒ no Gatekeeper quarantine.

### 🟡 CP-6 ★ `list_images()` returns empty on Windows although the files exist
**Where:** [cellpose_runner.py:58-88](../erysreview/cellpose_runner.py), [app.py:485-500](../erysreview/app.py)
**What:** After import the files are physically in the target folder and `_open_folder` runs, yet `list_images()` returns `[]` and the "no images" popup fires — Windows-only. A top-level `folder/img.tif` should pass every filter, so the failure is in the runtime layer (most likely the folder path handed to `rglob` doesn't match where files landed — a slash/trailing-separator artifact from `filedialog.askdirectory` joined with the subfolder path — or an encoding/normalization quirk). Not reproducible from macOS.
**Fix (agreed):** **Harden + log together** — normalize the folder path, add a non-recursive fallback scan, and log `folder`, raw `rglob` count, and per-file drop reason, so one Windows run either fixes it outright or prints the cause.

### ⚪ CP-7 Intentional installer divergence is undocumented
**Where:** [install.command:8-11](../install.command) (venv, *because* of Tk 8.6.13 Tahoe segfault) vs [install.bat](../install.bat) (conda).
**What:** The two platforms install completely differently *on purpose*, but only `install.command`'s header explains why. A future maintainer "unifying" them would reintroduce the Tahoe crash.
**Fix:** Document the divergence + rationale in `README.md` and at the top of `install.bat`.

### ✅ CP-8 Good pattern (keep) — platform-correct config dir
[persistence.py:23-32](../erysreview/persistence.py) `app_support_dir()` correctly branches `darwin`/`win32`/XDG. No change.

---

## Axis 2 — Error handling & crashes

### 🟠 EH-1 ★ Cellpose runs on the Tk main thread → UI freeze (worst on Windows-CPU)
**Where:** [app.py:636-640](../erysreview/app.py) (`_on_resegment`), [dialogs.py:1024-1027](../erysreview/dialogs.py) (`_compute_one`), [cellpose_runner.py:1-11](../erysreview/cellpose_runner.py) (docstring)
**What:** `run_for_image()` is called synchronously on the UI thread inside `CellposeSpinner`. The spinner can't animate and the window goes "Not Responding" for the whole inference. Once CP-1 unblocks Windows, every CPU segmentation will visibly freeze the app. The runner docstring ("the modal spinner above keeps the UI alive") is **inaccurate** — nothing pumps the loop during `eval`.
**Fix (agreed):** Run inference in a **worker thread**; hand the result back via a queue polled with `after`; spinner animates and is cancelable. **No Tk calls from the worker.** Apply to both single-image and batch paths. Also fix the docstring (ties to NS-4).

### 🟡 EH-2 Batch loop pumps with `update()` — re-entrancy risk
**Where:** [dialogs.py:1043,1051,1074](../erysreview/dialogs.py)
**What:** `container.update()` processes *all* pending events (including user clicks), so a stray click during the pump can re-enter handlers. `update_idletasks()` would be safer; the threaded model (EH-1) removes the need entirely.
**Fix:** Resolve as part of EH-1; otherwise downgrade to `update_idletasks()`.

### 🟡 EH-3 Numeric Spinboxes accept out-of-range typed input
**Where:** [dialogs.py:407-413](../erysreview/dialogs.py), saved at [dialogs.py:623-626](../erysreview/dialogs.py)
**What:** `from_=5.0, to=500.0` bound only the **arrows**; a user can *type* any value (this is how "diameter 2000/200" got in). The same applies to flow (0–3) and cellprob (−6–6). The UI lies about its own limits; pathological values reach Cellpose unvalidated.
**Fix:** Validate/clamp on save (and ideally on focus-out) with a friendly message. Note: this is a *correctness/UX* fix — it would **not** have prevented CP-1 (200 is legal), so it's independent of the Windows hang.

### ⚪ EH-4 Swallowed exceptions need case-by-case review
**Where:** e.g. [app.py:555](../erysreview/app.py), [app.py:592](../erysreview/app.py), [app.py:890](../erysreview/app.py), [detail_panel.py:90](../erysreview/detail_panel.py), [sidebar.py:272](../erysreview/sidebar.py)
**What:** Several `except Exception: pass` blocks. Some are legitimately cosmetic (icon load, overlay redraw). Others sit around data computations (eval markers, group summaries) where a silent failure could mask wrong counts.
**Fix:** Triage each: keep+comment the cosmetic ones; add at least a debug log to the data-path ones so failures aren't invisible.

---

## Axis 3 — State-refresh consistency

### 🟠 SR-1 ★ Blank-sidebar import is a silent dead-end
**Where:** [app.py:485-490](../erysreview/app.py)
**What:** When `list_images()` returns empty (CP-6), the user gets only a generic "no images" popup after a "successful" import — confusing. Couples to CP-6.
**Fix:** With CP-6's logging, also make the post-import empty case state *why* (e.g. "imported N files but none were listable — see log").

### 🟡 SR-2 Inconsistent refresh contract after mutations
**Where:** e.g. [app.py:607-613](../erysreview/app.py) (`_on_toggle_finished` refreshes statuses but **not** the group counter) vs [app.py:669-687](../erysreview/app.py) (`_on_toggle_eval` refreshes statuses **and** counter **and** invalidates cache).
**What:** Each mutation handler hand-picks which sidebar refreshes to call, so it's easy to miss one and leave a stale counter/icon.
**Fix:** Define one "after-mutation refresh" helper and call it everywhere, or document the contract explicitly.

### 🟡 SR-3 Counter-cache invalidation is scattered
**Where:** [sidebar.py:340-347](../erysreview/sidebar.py) plus many `invalidate_group_cache()` call sites in `app.py`.
**What:** The `_group_active_cache` / `_group_eval_cache` correctness depends on every mutating path remembering to invalidate. That's fragile.
**Fix:** Centralize invalidation (e.g. invalidate from the same after-mutation helper in SR-2), or key the cache on a cheap content signature.

### ⚪ SR-4 Mask read precedence is a quiet footgun
**Where:** [cellpose_runner.py:108-117](../erysreview/cellpose_runner.py)
**What:** Reads prefer `.erys_cache` over the sibling export. A stale cache next to a newer exported mask wins silently. Documented, but surprising.
**Fix:** Note in the export/import docs; consider a mtime tie-break if it ever bites.

---

## Axis 4 — Naming & structure

### 🟡 NS-1 The "2000" is hardcoded in names but the value is editable
**Where:** [evaluation.py:92-100](../erysreview/evaluation.py), [detail_panel.py:193](../erysreview/detail_panel.py), [overview.py:59-70](../erysreview/overview.py), [summary.py:110-119](../erysreview/summary.py); actual cap is `profile.target_cells` ([profile.py:66](../erysreview/profile.py)).
**What:** Keys/columns like `pct_changed_2000`, `%verändert_2000`, `pct_veraendert_per_2000` bake in "2000", but the cap is user-editable. When a user sets target ≠ 2000, the labels mislead.
**Fix:** Rename to `…_per_target` (or interpolate the live target into the displayed label). Internal-only churn, no behavior change.

### ⚪ NS-2 German/English mixing in identifiers & comments
**Where:** domain terms (`Proband`, `Zeitpunkt`, `verändert`) vs English code throughout.
**What:** Acceptable for a German research tool, but currently ad-hoc.
**Fix:** Pick and document a convention — e.g. *domain nouns stay German, all code/comments English* — and apply opportunistically.

### ⚪ NS-3 ★ `create_package` has no image selection
**Where:** [package.py:56](../erysreview/package.py)
**What:** Hard-includes every cached image; no picker.
**Fix (agreed):** Add a `selected_stems: set[str] | None` param (None = all, back-compat) fed by a **hierarchical timepoint→subject→image checklist** in the create dialog, reusing the `eval_select_dialog` ✓-toggle idiom; group rows toggle children; default all-checked.

### ⚪ NS-4 ★ Inaccurate runner docstring
**Where:** [cellpose_runner.py:9-10](../erysreview/cellpose_runner.py)
**What:** Claims the modal spinner keeps the UI alive; it can't (EH-1). Fix the comment when EH-1 lands.

### ✅ NS-5 Good patterns (keep)
Atomic JSON writes ([persistence.py:44-56](../erysreview/persistence.py)), the `caffeinate -i -w <pid>` self-cleaning keep-awake ([dialogs.py:968-994](../erysreview/dialogs.py)), and the int16+zlib mask compression ([cellpose_runner.py:148-158](../erysreview/cellpose_runner.py)) are all solid — no change.

---

## Recommended sequencing (respects dependencies)

1. **CP-1 + CP-2** — conda-forge install + Miniforge bootstrap. *Unblocks Windows entirely; everything else on Windows is untestable until this lands.*
2. **EH-1** (+ EH-2, NS-4) — background-thread inference. *Makes Windows-CPU actually usable, not just functional.*
3. **CP-6 + SR-1** — harden+log `list_images`; one Windows run to confirm.
4. **CP-4 + CP-5** — taskbar AppUserModelID + macOS `.app` bundle.
5. **NS-3** — package image picker.
6. **EH-3, NS-1, SR-2/SR-3, CP-3/CP-7, EH-4** — hygiene & consistency.

**Deferred:** own "U-Segmentation" (custom/trained model or freehand drawing) — not in this round.

---

## Implementation log

- **2026-06-15 — #1 (CP-1, CP-2) shipped, pending Windows verification.** `install.bat` rebuilt to install the full stack from conda-forge (one OpenMP runtime) + consented Miniforge auto-install + clean env recreate + import sanity check; `run.bat` conda path hardened; `requirements.txt` marked macOS/pip-only with a Windows warning; `README.md` quick-start + Windows manual install corrected to conda-forge (the old pip steps would have re-triggered the hang). *Not yet run on Windows.*
- **2026-06-16 — #2 (EH-1, EH-2, NS-4) shipped.** Added `dialogs.run_in_thread_pumping()` (worker thread + responsive event-loop pump; unit-smoke-tested on macOS: off-thread exec, loop stays live, result + exception propagation verified). Wired `_on_resegment` and the batch `_compute_one` to it; `CellposeSpinner._tick` now uses `update_idletasks` (no re-entrant pump); runner docstring corrected. Mac-verifiable now; Windows benefit lands once #1 is confirmed.
- **2026-06-17 — #3 (CP-6/SR-1) shipped.** `list_images` hardened: `resolve()` the folder + excludes (fixes slash/case/trailing-separator path shapes), per-reason drop tally + diagnostics logged when empty, non-recursive flat fallback, per-path `OSError` guards. Tested on macOS (keeps real images; skips overlay/non-image/hidden/excluded; trailing-slash form works). One Windows run will now either work outright or print exactly where images are lost.
- **2026-06-17 — #4 (CP-4/CP-5) shipped.** Windows: `EryApp._set_app_user_model_id()` called before the root window (taskbar identity). macOS: generated `assets/icon.icns`; `install.command` now builds a Desktop `ErysReview.app` bundle (Info.plist `CFBundleName`, `.icns`, launcher → repo `.venv` python). Bundle structure + plist validated on macOS. Visual Dock/taskbar confirmation pending on each OS.
- **2026-06-17 — #5 (NS-3) shipped.** `create_package` gained `selected_stems` (None = all, back-compat); `package_create_dialog` now shows a hierarchical timepoint→subject→image checklist (group rows toggle children, All/None buttons, live count, default all-checked). End-to-end tested: only selected stems land in the ZIP/manifest. 3 i18n keys added (DE+EN).
- **2026-06-17 — #6 partial (EH-3) shipped.** `_clampf()` clamps the three Cellpose params to their spinbox bounds (diameter 5–500, flow 0–3, cellprob −6–6) on save, robust to non-numeric input. Unit-tested.

### Deliberately deferred from #6 (regression / outward-facing risk)
- **NS-1 (rename the `2000` keys/columns)** — the `%verändert_2000` etc. are **user-facing Excel column names**; renaming them silently could break downstream analysis the user has built on those names. Needs explicit sign-off on the new names before changing.
- **SR-2 / SR-3 (centralize the after-mutation refresh & cache invalidation)** — a behavioral refactor of the sidebar/canvas refresh contract; meaningful regression surface that can't be fully validated without the live GUI. Worth doing deliberately, not bundled in.
- **EH-4 (narrow swallowed exceptions)** — broad change across many cosmetic vs data-path sites; low value-to-risk without per-site GUI testing.
- **NS-2 (German/English convention)** — opportunistic cleanup, no functional impact.

---

*This document is the agreed reference from the design review. Tick items off as they land; the Implementation log above tracks what has changed.*
