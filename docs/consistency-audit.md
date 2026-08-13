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
**Update 2026-08-12 — first site resolved (worst case of the pattern).** The duplicate-detection block in `_show_image_at` turned out to be exactly the feared kind: it wrapped the render calls too, `canvas.duplicate_ids` was never reset between images, and `_update_status()` derives `n_active` from that set — so a swallowed failure showed the *previous* image's duplicates as the current image's count. Resolved structurally (PF-2) rather than by logging: duplicate detection moved into `EryCanvas.set_image`, next to `border_ids`, with no `except` — the same shape `compute_image_state` already uses, so the interactive and the export path can no longer disagree. The remaining EH-4 sites are still open.

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
**Update 2026-08-12 — the scattering had a measurable cost, one call site removed.** `_show_image_at` was clearing the cache for *every* group, so the first click after each arrow-key press recomputed `group_eval_summary` for the whole folder. Navigation changes no group's numbers, and all five paths that *do* (re-segment, batch, eval toggle, eval select, export) already invalidate themselves before landing there — so that call was pure loss. Removed; `refresh_current_group_counter()` still invalidates the group actually entered. The underlying fragility (correctness resting on "everyone remembers") is unchanged and still wants the SR-2 helper.

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

## Axis 5 — Performance & GUI

*Added 2026-08-12 from a code review focused on efficiency and the interface. Measurements come from `bench_click.py` (counts real `stat`/`exists`/`mkdir`/`open` syscalls) against a 20-image folder on a warm Mac SSD; per-image figures extrapolate linearly, which is a **floor** for a USB stick (exFAT has no directory index), not an estimate.*

### Landed this round

| ID | Sev | Finding | Result |
|---|---|---|---|
| **PF-1** | 🔴 | `_refresh_overlay()` rebuilt the whole `H*W*4` overlay on every click — 36.9 MB float32 per click at 1920×1200, though exactly one cell changed. | Single-cell `patch_overlay_cell()`; **15.49 ms → 0.045 ms** per click (×345). |
| **PF-2** | 🔴 | Overlay **and** all number artists were built twice per image switch: once by `set_image`, once by the duplicate-detection block that followed it in `app.py`. Also the EH-4 instance above. | Duplicates resolved inside `set_image`; one build per switch, **30.98 → 16.46 ms**. |
| **PF-3** | 🔴 | `sidebar._group_counters` ran a full `has_cache` + `ReviewState.load` pass per group to compute `n_done` — which no label format ever interpolated. | Deleted. **−220 ops/click** at 20 images. |
| **PF-4** | 🔴 | `group_eval_summary` read every `_counts.json` and `_review.json` in the group **twice** (once via `effective_eval_stems`, once in its own aggregation loop). | One shared `_group_rows()` pass. **965 → 280 ops**. |
| **PF-5** | 🟠 | Cache-wide invalidation on plain navigation — see SR-3 update. | Removed. |
| **PF-6** | 🟠 | `has_cache` went through `mask_read_paths`, which also stats the *meta* path the caller discards. | Direct mask probe. **6 → 2 ops** per call. |
| **PF-7** | 🟠 | `cache_dir_for` called `mkdir(parents=True, exist_ok=True)` (2 syscalls) on every `review_path` / `_counts_path` / `cache_paths`. | Memoized per folder. |
| **UI-1** | 🟡 | `_act_remove_toggle` never refreshed the numbers, so a cell switched inactive went gray but **kept its number** until the next image switch. | Patching now flips that one number. |
| **UI-2** | 🟡 | `_refresh_numbers` decided "active" with a rule that ignored `duplicate_ids`, contradicting both its own docstring and the status bar. | One `OverlayColors.is_active` for fill color, numbers and counts. *Visible change: duplicate-flagged cells no longer show a number.* |
| **UI-3** | 🟡 | `set_profile` recomputed `border_ids` but not `duplicate_ids`, so editing the duplicate config left stale flags until the next image switch. | `_recompute_duplicates()` added. |

Together: **2470 → 722 syscalls per click** (−71 %), and the per-click overlay cost is now independent of image size.

### 🔴 PF-13 The duplicate detection was inert — replaced by a size filter *(landed)*
**Where:** former `duplicates.py`, now [sizefilter.py](../erysreview/sizefilter.py); config in [profile.py](../erysreview/profile.py)
**What:** `detect_duplicates` flagged two cells whose centroids were closer than `threshold_factor × mean cell radius` (default **0.7**). Verified against the TRIBAL folder — 20 images, 3164 cells: **0 flagged, 0.00 %**, and not by accident. The threshold sat at 27 px while the *closest* centroid pair in the entire set was 44.8 px; the minimum nearest-neighbour distance is **1.16 radii** against a 0.70-radius threshold. The premise cannot hold: a Cellpose mask is a **pixel partition**, so two labels never overlap, and two touching cells sit ~2 radii apart (measured median 2.22). A sweep confirmed there is no usable setting — nothing fires below 1.5, and at 2.0 it flags 11.5 % of cells, i.e. ordinary neighbours. The unit test proves the code itself was correct (it does fire on a synthetic concentric fragment); the *signal* was wrong for the data.
**Fix (shipped):** area-based. Cells below `min_factor` (0.35) or above `max_factor` (1.70) times the **median** cell area of that image are excluded — fragment and merged-cell respectively. Median, not mean, so a fragment-heavy image doesn't lower its own threshold. Bounds are per-profile and clamped on save like the Cellpose params (EH-3).
**Measured impact on real data:** 90 fragments exist, but **77 of them were already excluded as border cells** — the filter newly removes 13 fragments + 4 merged cells = **17 of 3164 (0.54 %)**. On the reported group: `n_active` 2119 → 2105 (−0.66 %), `%verändert` 0.189 → 0.190, i.e. **+0.001 percentage points**. The evaluation set stayed at 15 images.
**Residual naming debt:** the exported `is_duplicate` column (stats CSV + `erys.db`) and the internal `duplicate_ids` that feeds it keep their names deliberately — they are a documented data contract (see [ML_DATASET.md](ML_DATASET.md)) and NS-1 is the standing decision not to rename user-facing columns without sign-off. The column now means "auto-excluded by the size filter".

### 🟠 UI-13 The overview workbook was only reachable through a full export *(landed)*
**Where:** [app.py](../erysreview/app.py) `_on_overview_xlsx`, [dialogs.py](../erysreview/dialogs.py) `ask_overview_destination` / `save_overview_copy`
**What:** `Uebersicht.xlsx` was written only as a side effect of `export_folder_structured`, into a fixed path under `outputs/`. Getting a current copy meant running a full export.
**Fix (shipped):** `Auswertung → Übersicht als Excel speichern …` rebuilds the workbook from the current review state and saves it wherever the user picks; the post-export preview window gained the same *Speichern unter …* button (which copies the file it just previewed, rather than rebuilding it). The rebuild walks every group — 577 ms for 20 images, so ~22 s at 780 — and therefore runs on a worker thread behind the new reusable `BusyDialog` instead of freezing the window. That dialog is the piece UI-4 needs; when the export and folder-open get their progress treatment, `CellposeSpinner` should fold into it.

### Accessibility round (2026-08-12)

*A WCAG 2.1 AA pass, adapted to a desktop app: contrast, keyboard, focus and labelling transfer directly; the ARIA/screen-reader criteria (4.1.2) do not — Tk exposes nothing useful to VoiceOver or Narrator, which is a property of the toolkit, not of this code. Measurements in `a11y.py`.*

| ID | Sev | Finding | Result |
|---|---|---|---|
| **UI-13a** | 🔴 | **The cell type was conveyed by fill colour alone** — WCAG 1.4.1, Level A. Measured on the colours *as rendered* (fill blended over a 245/255 bright field): the default palette drops to ΔE 1.2 under tritanopia, and **no** palette at **any** opacity clears ΔE 20 under deuteranopia — even at 100 % the "colorblind-safe" Wong set only reaches 18.6. Colour alone cannot carry this. | Per-type contour (`TypeDef.outline`), drawn at every zoom level. |
| **UI-6** | 🟠 | `fg_muted` at 2.58–2.62:1, warn colour at 3.22:1, sash at 1.42–1.57:1. | Palette fixed: **6/17 → 1/17** failing pairs (dark), **7/17 → 1/17** (light). |
| **UI-13b** | 🟠 | White on `accent_primary` was **2.75:1** in the dark theme — and that pair labels the *active tool*. | New `fg_on_accent` token, per theme; 5.75:1. |
| **UI-13c** | 🟠 | 15 font sizes ≤ 9 pt, hard-coded in 39 places, no way for the user to enlarge (1.4.4). | All 39 routed through `theme.font()`; a 100/125/150 % setting scales them. |
| **UI-13d** | 🟠 | **Measured:** Left arrow on a focused sidebar tree collapsed the node *and* stepped to the previous image, in one keystroke (2.1.1/3.2.1). | Extra bindtag spliced between the Treeview class and the toplevel — verified 1 → 0 spurious firings. |
| **UI-10** | 🟠 | Toolbar needs 1659 px, `minsize` allows 900 — Export and the param banner fell off the right edge. | Icon-only below 1700 px with tooltips and hysteresis: **1659 → 803 px**. |
| **UI-13e** | 🟡 | Focus ring on one of four button styles (2.4.7). | `focuscolor` on all four. |
| **UI-13f** | 🟡 | Click targets: buttons 37 px, tree rows 22 px (2.5.5 wants 44). | Buttons **45 px**, rows 28 px. |
| **UI-13g** | 🟡 | Escape missing in 5 of 15 windows; the two busy modals had **no** way out at all — no key, and the close button bound to a no-op (2.1.2). | Escape everywhere; the busy modals now register a cancel request and say so. They still cannot interrupt the worker — neither Cellpose nor a workbook build can be stopped mid-call — but the dead end is gone. |
| **PF-14** | 🟡 | `ProfileData.from_dict` did `TypeDef(**t)`, so a profile carrying a field this build doesn't know (e.g. inside a package from a newer version) raised `TypeError`. | Unknown keys are dropped. |

Fill opacity went from 0.42 to a per-profile **0.55** (min ΔE between types 21.6 → 27.6 normal, 14.1 → 17.9 deuteranopia). `CellposeSpinner` folded into `BusyDialog`, which had been a near-copy of it.

### Cellpose models + training data (2026-08-12)

| ID | Sev | Finding | Result |
|---|---|---|---|
| **PF-15** | 🔴 | Cellpose, given a model path it cannot find, logs one line and **segments with cpsam anyway**. A profile referring to a missing model would have mixed two models' counts inside one study while the mask metadata still named the requested one. | `resolve_model()` raises `ModelUnavailable`; the app blocks before the destructive part of re-segment (which deletes mask + review *first*) and offers the mapping dialog. Gate: `tests/test_model_resolve.py`. |
| **UI-16** | 🟠 | The Cellpose model was not editable at all — the profile editor passed `working.cellpose.model` straight through. Cellpose 4 ships exactly one model (`MODEL_NAMES == ["cpsam"]`; the v3 zoo is gone), so "another model" always means a file. | Model picker in the profile; the profile stores a **name**, and `AppSettings.cellpose_models` maps names to files per machine — a path inside a shared profile would be meaningless on the receiving computer. |
| **PF-16** | 🟡 | Nothing in the export said whether a human had reviewed an image; `is_finished` never left the review JSON. | `reviewed` added to the stats CSV and to `images` in erys.db (schema 2, with a rebuild-on-stale migration since the DDL is all `IF NOT EXISTS`). |
| **PF-17** | 🟡 | The export is not usable as segmentation training data (discs for manual cells, non-counted cells still labelled, originals not copied). | `export_training_data()` writes `<stem>.tif` + `<stem>_masks.tif` for finished, disc-free images only, verified loadable by `cellpose.io.load_train_test_data`. Rationale and limits in [ML_DATASET.md](ML_DATASET.md). |

**CP-9 (2026-08-12, open): Cellpose 4.2 exists but only on PyPI.** Measured: PyPI carries 4.2.1.1 (adding `cpdino`, `cpdino-vitb` and an updated `cpsam_v2` that "predicts fewer spurious masks"), **conda-forge is still on 4.1.1**. `requirements.txt` (macOS/pip) and `install.bat` (Windows/conda-forge) share the pin `cellpose>=4.1.1,<5`, so a `pip install --upgrade` on the Mac would jump to 4.2 while every lab machine stays on 4.1.1 — masks from two segmentation regimes in one study. Also note `params_match()` compares diameter/flow/cellprob/model but **not** `cellpose_version`, so an upgrade does not invalidate existing masks and nothing in the UI would flag the mix. Do not upgrade before conda-forge follows, or pin both installers to the same version explicitly. `cpdino` additionally needs `pip install git+https://github.com/facebookresearch/dinov3`, i.e. git on every lab machine plus that model's own licence terms.

**Not attempted, on purpose:** mask editing (delete a label, redraw a boundary, split merged cells). Without it the exported masks can only support *domain adaptation* — fine-tuning on this material — not learning from the reviewer's corrections. That is a deliberate scope boundary, not an oversight.

### Still open

### 🟠 PF-8 `list_images` walks the cache and the export folder — **blocked by CP-6**
**Where:** [cellpose_runner.py:94-121](../erysreview/cellpose_runner.py)
**What:** `sorted(folder.rglob("*"))` enumerates *everything* and filters afterwards, so every `.erys_cache` entry (4 files per image → ~3120 at 780 images, flat in one directory) and every copied TIF under `outputs/` is stat-ed on each folder open and each `F5`. Measured: 141 paths walked to return 20 images. `p.resolve()` per candidate adds more, and is expensive on Windows.
**Fix:** `os.walk` with in-place `dirnames` pruning, so skipped directories are never descended into.
**Why not now:** this is the exact function CP-6 hardened, and that fix is **still awaiting its one Windows run**. Rewriting it first would invalidate a verification that hasn't happened. Do CP-6's Windows run, *then* this.

### 🟠 PF-9 Export makes at least four passes over the same images, on the Tk thread
**Where:** [export.py:147-165](../erysreview/export.py), [summary.py:63](../erysreview/summary.py), [overview.py](../erysreview/overview.py), [db.py](../erysreview/db.py)
**What:** `effective_eval_stems` runs once in `export_folder_structured` and again inside `write_proband_zeitpunkt_summary`; `_export_image` calls `compute_image_state`, then summary/overview/db each re-read the counts. No progress indication, no worker thread — ≥23 s frozen at 780 images.
**Fix:** Compute the eval set and the per-image counts once per group and pass them down; wrap the run in the existing `run_in_thread_pumping` + a progress dialog (the batch dialog is the working model).
**Why not now:** rewrites lab-critical CSV/XLSX output that can't be validated without a real export; same reasoning that parked erys.db Step 2.

### 🟡 PF-10 `DetailPanel` destroys and rebuilds every widget per click
**Where:** [detail_panel.py:208-210](../erysreview/detail_panel.py)
**What:** `_clear_body()` `destroy()`s all children on every refresh, i.e. on every click. Besides the churn, the panel flickers.
**Fix:** Build the rows once, then `config(text=…)`.

### 🟡 PF-11 Time tracking invalidates the count cache on every image switch
**Where:** [app.py:382-388](../erysreview/app.py), [counts.py:64-72](../erysreview/counts.py)
**What:** `_flush_time()` rewrites the review JSON on each switch, which bumps its mtime — and `image_counts` keys its cache on that mtime. So the *next* read of that image forces a full `compute_image_state` (mask load + `regionprops`), although only a timer changed.
**Fix:** Exclude `time_spent_s` from the cache signature, or store the time outside the review file.

### ⚪ PF-12 `DetailPanel.show_image` re-parses the filename per click
**Where:** [detail_panel.py:117](../erysreview/detail_panel.py) — `grouped.flat` already holds the parsed `ImageGroupInfo`.

### 🟠 UI-4 The two long operations have no feedback at all
**Where:** [app.py:503-518](../erysreview/app.py) (folder open), [app.py:885-920](../erysreview/app.py) (export)
**What:** Cold folder open costs ~15 ms/image (≈4.5 s at 300 images) and export ≥23 s at 780 — both on the Tk thread, both painting "Not Responding" on Windows. Cellpose got a worker thread (EH-1) and the batch run got a progress bar; these two got neither.
**Fix:** Same pattern as `batch_dialog`: `run_in_thread_pumping` + determinate progress + cancel.

### 🟠 UI-5 The ⚙ hit-test measures text with the wrong font
**Where:** [sidebar.py:375-399](../erysreview/sidebar.py)
**What:** `_hit_settings_icon` measures the row string with `TkDefaultFont` and treats everything to its right as the icon — but the row renders in the `Erys.Treeview` style and the string mixes status icon, counter, `⊘` marker and padding. If the measurement is off, a click near the right edge either opens the eval dialog unintentionally or does nothing.
**Fix:** A real Treeview column (`batch_dialog` already does this correctly with `identify_column(evt.x) != "#1"`), or a right-click context menu — which would also be more discoverable.

### 🟠 UI-6 Hint text fails WCAG AA contrast in both themes
**Where:** [theme.py](../erysreview/theme.py) `fg_muted`
**What:** `#6B7384` on `#2A3447` (dark) and `#8B94A4` on `#E8ECF2` (light) both land at ≈**2.6:1**; AA wants 4.5:1 for body text. It is used at 9 pt italic — including `details_empty_hint`, the one string that explains the ⚙ feature.
**Fix:** Use `fg_secondary` for hints, or darken/lighten `fg_muted` until it clears 4.5:1.

### 🟡 UI-7 `q` quits and `s` exports without a modifier
**Where:** [app.py:339-340](../erysreview/app.py)
**What:** Both are bound bare on the root window, alongside the Cmd/Ctrl variants that already exist. A stray keypress closes the app or kicks off a full export (≈23 s frozen at 780 images).
**Fix:** Drop the bare bindings; keep `Cmd/Ctrl+Q` and `Cmd/Ctrl+S`.

### 🟡 UI-8 Theme and language only apply after a restart
**Where:** [app.py:866-884](../erysreview/app.py)
**What:** Both show a "restart required" dialog. For non-technical lab users this is a poor first impression. Language is the easier half — every string already goes through `self.S`.

### 🟡 UI-9 `Enter` triggers the destructive choice
**Where:** [dialogs.py:154](../erysreview/dialogs.py)
**What:** `confirm_dialog` binds `<Return>` to Yes, including for the re-segmentation prompt that discards the image's whole review.
**Fix:** Default to the safe option in destructive dialogs.

### ⚪ UI-10 Toolbar may drop its primary CTA at the minimum window size
**Where:** [toolbar.py:149-224](../erysreview/toolbar.py), `minsize(900, 600)` at [app.py:77](../erysreview/app.py)
**What:** Three tool buttons, a 22-char combobox and five action buttons pack left; Export and the param banner pack last and are therefore the first to be squeezed out. Unverified — needs one look at 900 px.

### ⚪ UI-11 The status line packs six values into one truncating label
**Where:** [app.py:980-988](../erysreview/app.py)

### 🟡 UI-15 Two toolbar glyphs may not render on Windows — **check on the first Windows run**
**Where:** [toolbar.py](../erysreview/toolbar.py) `GLYPHS`
**What:** The set was changed to a bolder look on 2026-08-12. Four of the six sit in blocks Windows covers reliably (Arrows U+21xx, Geometric Shapes U+25xx, Enclosed U+24xx). Two do not: **⛶** (U+26F6, Misc Symbols) and **⬇** (U+2B07, Misc Symbols and Arrows). Windows may substitute them from another font at a different weight, or show a box. Not checkable from macOS.
**Fix if wrong:** swap in `GLYPHS` — ▣ (U+25A3) and ⇩ (U+21E9) have the same weight and sit in well-covered blocks. One line each.
**Also note:** a Unicode glyph set can never be fully consistent — the six chosen are 12–17 px wide because they come from different blocks. Bundled PNG icons were considered and rejected in favour of zero new assets; if the toolbar ever needs to look truly uniform, that is the route (`ttk.Button` takes `image=`, verified).

### ✅ UI-12 Good patterns (keep)
Three-state status icons `○ ◐ ●` encode **shape as well as color**; the Wong colorblind-safe scheme is offered; number rendering has a zoom LOD; `batch_dialog` has progress, cancel, an ETA and self-cleaning keep-awake — it is the model the other long operations should follow.

---

## Recommended sequencing (respects dependencies)

1. **CP-1 + CP-2** — conda-forge install + Miniforge bootstrap. *Unblocks Windows entirely; everything else on Windows is untestable until this lands.*
2. **EH-1** (+ EH-2, NS-4) — background-thread inference. *Makes Windows-CPU actually usable, not just functional.*
3. **CP-6 + SR-1** — harden+log `list_images`; one Windows run to confirm.
4. **CP-4 + CP-5** — taskbar AppUserModelID + macOS `.app` bundle.
5. **NS-3** — package image picker.
6. **EH-3, NS-1, SR-2/SR-3, CP-3/CP-7, EH-4** — hygiene & consistency.

7. **Axis 5** — PF-8 must wait for step 3's Windows run; PF-9 and UI-4 belong together (both need `run_in_thread_pumping` + a progress dialog); UI-6 and UI-7 are one-line fixes that can ride along with anything.

**Deferred:** own "U-Segmentation" (custom/trained model or freehand drawing) — not in this round.

---

## Implementation log

- **2026-06-15 — #1 (CP-1, CP-2) shipped, pending Windows verification.** `install.bat` rebuilt to install the full stack from conda-forge (one OpenMP runtime) + consented Miniforge auto-install + clean env recreate + import sanity check; `run.bat` conda path hardened; `requirements.txt` marked macOS/pip-only with a Windows warning; `README.md` quick-start + Windows manual install corrected to conda-forge (the old pip steps would have re-triggered the hang). *Not yet run on Windows.*
- **2026-06-16 — #2 (EH-1, EH-2, NS-4) shipped.** Added `dialogs.run_in_thread_pumping()` (worker thread + responsive event-loop pump; unit-smoke-tested on macOS: off-thread exec, loop stays live, result + exception propagation verified). Wired `_on_resegment` and the batch `_compute_one` to it; `CellposeSpinner._tick` now uses `update_idletasks` (no re-entrant pump); runner docstring corrected. Mac-verifiable now; Windows benefit lands once #1 is confirmed.
- **2026-06-17 — #3 (CP-6/SR-1) shipped.** `list_images` hardened: `resolve()` the folder + excludes (fixes slash/case/trailing-separator path shapes), per-reason drop tally + diagnostics logged when empty, non-recursive flat fallback, per-path `OSError` guards. Tested on macOS (keeps real images; skips overlay/non-image/hidden/excluded; trailing-slash form works). One Windows run will now either work outright or print exactly where images are lost.
- **2026-06-17 — #4 (CP-4/CP-5) shipped.** Windows: `EryApp._set_app_user_model_id()` called before the root window (taskbar identity). macOS: generated `assets/icon.icns`; `install.command` now builds a Desktop `ErysReview.app` bundle (Info.plist `CFBundleName`, `.icns`, launcher → repo `.venv` python). Bundle structure + plist validated on macOS. Visual Dock/taskbar confirmation pending on each OS.
- **2026-06-17 — #5 (NS-3) shipped.** `create_package` gained `selected_stems` (None = all, back-compat); `package_create_dialog` now shows a hierarchical timepoint→subject→image checklist (group rows toggle children, All/None buttons, live count, default all-checked). End-to-end tested: only selected stems land in the ZIP/manifest. 3 i18n keys added (DE+EN).
- **2026-06-17 — #6 partial (EH-3) shipped.** `_clampf()` clamps the three Cellpose params to their spinbox bounds (diameter 5–500, flow 0–3, cellprob −6–6) on save, robust to non-numeric input. Unit-tested.

- **2026-08-12 — Axis 5 first batch shipped (PF-1…PF-7, UI-1…UI-3), incl. one EH-4 and one SR-3 site.** Two independent causes of the click latency, fixed separately. *(a) Size-independent:* the overlay is now `uint8` instead of `float32` (36.9 → 9.2 MB at 1920×1200 — memory only, the rebuild time is unchanged since it is gather-bound), a single-cell edit repaints just that cell's clamped bbox (15.49 → 0.045 ms), duplicates moved into `set_image` so overlay and numbers build once per switch instead of twice, and number artists now live for the image with visibility toggled per cell. *(b) Folder-size-dependent:* dead `n_done` loop deleted, `group_eval_summary` down to one disk pass, `has_cache` 6 → 2 ops, `cache_dir_for` memoized, cache-wide invalidation dropped from navigation. Net **2470 → 722 syscalls per click** at 20 images (−71 %). All color/activity decisions were consolidated into a single `OverlayColors` class shared by the full rebuild and the patch. **Verification:** new `tests/test_overlay.py` asserts patch == full rebuild pixel-for-pixel across all seven mutations (incl. force-included border cells, duplicate flags and a manual cell whose bbox sticks out of the image) and pins the uint8 scheme; `tests/test_export_db.py` stays green; a widget-level smoke run drove the real `EryCanvas` through every click path on a real 179-cell image with no drift. Mac-verified; nothing platform-specific was touched.

- **2026-08-12 — PF-13 + UI-13 shipped.** *PF-13:* the centroid-distance duplicate detection was verified inert on real data (0 of 3164 cells, with a 1.66× gap between the threshold and the closest pair that exists) and replaced by an area-based size filter against the image median; `duplicates.py` → `sizefilter.py`, `DuplicateDetection` → `SizeFilter` (profile JSON key `size_filter`, old `duplicate_detection` block still read for the on/off choice so existing profiles keep working), profile-editor tab and DE/EN strings rewritten, `profile_signature` prefix changed `d` → `s` so every `counts.json` written under the old rule recomputes once. New `tests/test_sizefilter.py` pins the bounds, the median-vs-mean choice and the disable switch. Measured effect on the TRIBAL folder: −17 cells of 3164 (0.54 %), `%verändert` +0.001 pp. *UI-13:* overview workbook now saveable at any time from the Auswertung menu and from the export preview, built on a worker thread behind the new reusable `BusyDialog`. Verified: both gates plus the size-filter gate green, profile editor builds in DE and EN, workbook writes 9 sheets to an arbitrary path, widget smoke run clean, app starts without error. Mac only — nothing platform-specific touched.

- **2026-08-12 — accessibility round shipped (UI-6, UI-10, UI-13a…g, PF-14).** Perceivable: palette reworked (`fg_muted`, `accent_warn`, `sash`, new `fg_on_accent`) — failing contrast pairs 6/17 → 1/17 dark and 7/17 → 1/17 light, the remaining one being the purely decorative hairline, which 1.4.11 does not cover; fill opacity 0.42 → 0.55 per profile; all 39 font specs routed through `theme.font()` behind a 100/125/150 % setting. The Level A finding — type carried by colour alone — is answered with a per-type contour (`TypeDef.outline`: none/solid/dashed/dotted/dash-dot, plus a profile-wide on/off), deliberately **not** gated by the numbers' zoom threshold, since at full-image view a cell is ~41 px and only ~0.2 % of cells carry a mark. A rendered comparison on real cells showed solid-vs-broken is reliable while three broken patterns are not, so the field defaults to none and profiles are expected to use few styles. Operable: sidebar navigation keys no longer double-fire the window shortcuts (verified 1 → 0), toolbar goes icon-only with tooltips below 1700 px (1659 → 803 px, fits the 900 px minimum), focus ring on all four button styles, buttons 45 px and tree rows 28 px, Escape in every window, and the busy modals register a cancel request instead of being a dead end. **Verification:** three gates green (`test_export_db`, `test_overlay` — extended with profile-opacity and contour checks — and `test_sizefilter`), widget smoke run clean, profile editor and settings build in DE and EN, contrast re-measured, contour behaviour verified against a real 179-cell image across type/exclude/toggle/profile-switch, app starts without error. Mac only.

### Deliberately deferred from #6 (regression / outward-facing risk)
- **NS-1 (rename the `2000` keys/columns)** — the `%verändert_2000` etc. are **user-facing Excel column names**; renaming them silently could break downstream analysis the user has built on those names. Needs explicit sign-off on the new names before changing.
- **SR-2 / SR-3 (centralize the after-mutation refresh & cache invalidation)** — a behavioral refactor of the sidebar/canvas refresh contract; meaningful regression surface that can't be fully validated without the live GUI. Worth doing deliberately, not bundled in.
- **EH-4 (narrow swallowed exceptions)** — broad change across many cosmetic vs data-path sites; low value-to-risk without per-site GUI testing.
- **NS-2 (German/English convention)** — opportunistic cleanup, no functional impact.

---

*This document is the agreed reference from the design review. Tick items off as they land; the Implementation log above tracks what has changed.*
