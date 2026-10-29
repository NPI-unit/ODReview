# Ki-67 classification – plan (not built yet)

Mockup: https://claude.ai/artifact/RwUwKtJArwVBcQRBNJRqq6 (review · settings · results, light/dark under Tweaks)
Basis: *Manual Cell Counting for Ki-67 Immunostaining*, SOP v0.1 (22 Jan 2026), `Pilot_Boyi/V0.1Ki-67 Cell Counting SOP.pdf`

## What the SOP asks, and what the app has

| SOP step | App |
|---|---|
| 2.1 valid nucleus: round/oval, clear boundary, inside a cell | InstanSeg + the reviewer's confirm (C) / discard (X); “inside a cell” stays the reviewer's call |
| 3. positive if clearly above background | OD is measured against I₀ (background next to the nucleus) |
| 4. grade 1 weak · 2 moderate · 3 strong (“dark, most of the nuclear area”) | mean DAB OD + share of nucleus pixels above an OD |
| ≥ 100 nuclei; two reviewers; reviewer 3 checks ICC | missing |

## Design

1. **Four classes per nucleus:** negative · 1 · 2 · 3, suggested from the **DAB OD** (colour deconvolution, not the grey OD):
   - negative: DAB OD ≤ t₁
   - 1: t₁ < OD ≤ t₂
   - 2: OD > t₂
   - 3: OD > t₃ **and** ≥ cov % of the nucleus above DAB OD 0.15 (SOP: “occupying most of the nuclear area”)
2. **The reviewer decides.** Suggestion drawn dashed; click or a key sets it. Keys: proposal ⌥0–⌥3 (1–4 are the tools, E is “counted”) – open.
3. **Cut-offs from graded nuclei, not guessed.** Grade ~150 nuclei by hand (all classes, 20× images) → the app suggests the cut-offs with the best agreement (weighted κ); the user saves them.
4. **Results per image / subject:** total nuclei, n per class, % positive, **H-score** = 1·%(1) + 2·%(2) + 3·%(3) (0–300), warning while a subject has < 100 nuclei. Export also as the SOP Excel sheet.
5. **Optional:** import a second reviewer → κ per grade and ICC for the totals (replaces reviewer 3's check).

## Example numbers (cut-offs 0.05 / 0.10 / 0.18 / 60 %, guessed – not calibrated)

| Image | Nuclei | – / 1 / 2 / 3 | Positive | H-score |
|---|---|---|---|---|
| Control positive 2_026 (20×) | 42 | 6 / 14 / 15 / 7 | 85.7 % | 155 |
| 41kd-2_027 (20×) | 25 | 2 / 7 / 11 / 5 | 92.0 % | 176 |

41kd at 92 % suggests the guessed cut-offs are too low – calibration first.

## Caveats

- Fixed cut-offs hold within one acquisition series only (control 59 ms vs samples 13 ms exposure; OD compensates via I₀ as long as nothing saturates).
- Ki-67 staining is often granular: mean OD alone underrates it → area share (or a high percentile) is part of the rule.

## Retraining InstanSeg (separate step)

- Only **20×** images (the highest resolution). Training set: `/Volumes/INTENSO/ICC_C2C12_Calibration/training_20x/` (inside the calibration study, so it is marked there).
- A training image counts only when it is **complete** (every nucleus confirmed or drawn, every false one discarded, then Enter).
- Rough guide: ~200–300 nuclei for a visible gain, ~500+ robust; keep ~20 % of the images out for testing.
- 10× keeps the stock model unless it is clearly worse afterwards.
- Needs a “training data” export (image + label mask per reviewed image) – not built yet.
