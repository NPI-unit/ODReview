"""Per-Proband-Zeitpunkt Summary-CSV writer (Frage 3a).

Layout (1:1 the user's spreadsheet):

  Block 1 – Counts per image:
    Bildnummer, RBC, <Subtype1>, <Subtype2>, ..., Normal
  Block 2 – Percentages per image (same row, right of Block 1):
    %<Subtype1>, %<Subtype2>, ..., %Normal
  Block 3 – Summe row at the bottom of both blocks
    Sum of counts, then aggregated percentages
    plus columns: %verändert (total), %verändert_per_2000 (Rückrechnung)
  Block 4 – cell below the Summe row:
    %target_erreicht (= Σ_RBC / target × 100)

Per-image percentage:  100 * n_typ / RBC_bestätigt
Aggregated percentage: 100 * Σ_n_typ / Σ_RBC
%verändert = 100 − %Normal_aggregiert
%verändert_per_2000 = %verändert × 2000 / Σ_RBC   (Frage 3b Formel I)
"""

from __future__ import annotations

import csv
from pathlib import Path

from .grouping import ImageGroupInfo
from .image_state import compute_image_state
from .profile import ProfileData


# ─────────────────────────────────────────────
# Per-image active counts per type — thin wrapper around compute_image_state
# ─────────────────────────────────────────────
def per_image_counts(
    folder: Path, image_info: ImageGroupInfo, profile: ProfileData,
) -> dict | None:
    """For one image, returns {'rbc': n_active, 'by_type': {type_key: n_active}}.
    Returns None if no cellpose mask is cached for that image.

    Delegates to the disk-backed count cache so repeated sidebar/summary calls
    don't reload the mask each time.
    """
    from .counts import image_counts
    return image_counts(folder, image_info, profile)


# ─────────────────────────────────────────────
# Build & write the summary CSV
# ─────────────────────────────────────────────
def write_proband_zeitpunkt_summary(
    folder: Path,
    profile: ProfileData,
    images_in_group: list[ImageGroupInfo],
    out_path: Path,
) -> dict | None:
    """Writes the summary CSV for one (timepoint, subject) group.
    Returns the aggregate dict, or None if no images had cached masks."""
    type_keys_non_normal = [t.key for t in profile.cell_types if t.key != "normal"]
    type_keys_all = type_keys_non_normal + ["normal"]   # Normal as last column
    type_name_by_key = {t.key: (t.name or t.key) for t in profile.cell_types}

    from .evaluation import effective_eval_stems
    eval_stems = effective_eval_stems(folder, images_in_group, profile)

    rows: list[dict] = []
    for info in images_in_group:
        if info.path.stem not in eval_stems:
            continue  # image excluded from the 2000-cell evaluation
        c = per_image_counts(folder, info, profile)
        if c is None:
            continue
        rows.append(c)
    if not rows:
        return None

    target = max(1, profile.target_cells)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter=";")  # German locale-friendly

        # ===== Header row =====
        header_counts = ["Bildnummer", "RBC"] + [type_name_by_key[k] for k in type_keys_all]
        header_gap = [""] * 3
        header_pct = ["%" + type_name_by_key[k] for k in type_keys_all]
        w.writerow(header_counts + header_gap + header_pct)

        # ===== Per-image rows =====
        for r in rows:
            counts_row = [r["image_num"] if r["image_num"] is not None else r["filename"],
                          r["rbc"]]
            for k in type_keys_all:
                counts_row.append(r["by_type"].get(k, 0))
            pct_row = []
            for k in type_keys_all:
                n = r["by_type"].get(k, 0)
                pct = (100.0 * n / r["rbc"]) if r["rbc"] > 0 else 0.0
                pct_row.append(f"{pct:.2f}".replace(".", ","))
            w.writerow(counts_row + ["", "", ""] + pct_row)

        # ===== Spacer rows =====
        w.writerow([])
        w.writerow([])

        # ===== Summe row =====
        sum_rbc = sum(r["rbc"] for r in rows)
        sum_by_type = {k: sum(r["by_type"].get(k, 0) for r in rows) for k in type_keys_all}
        pct_normal_agg = (100.0 * sum_by_type.get("normal", 0) / sum_rbc) if sum_rbc > 0 else 0.0
        pct_veraendert = 100.0 - pct_normal_agg
        pct_veraendert_per_2000 = pct_veraendert * target / sum_rbc if sum_rbc > 0 else 0.0

        summe_counts = ["Summe", sum_rbc] + [sum_by_type[k] for k in type_keys_all]
        summe_pcts = []
        for k in type_keys_all:
            agg = (100.0 * sum_by_type[k] / sum_rbc) if sum_rbc > 0 else 0.0
            summe_pcts.append(f"{agg:.2f}".replace(".", ","))
        # Extra columns on Summe row: %verändert | %verändert per 2000
        extra = [f"{pct_veraendert:.2f}".replace(".", ","),
                 f"{pct_veraendert_per_2000:.2f}".replace(".", ",")]
        w.writerow(summe_counts + ["", "", ""] + summe_pcts + extra)

        # ===== %target_erreicht row (one cell below) =====
        pct_target_reached = 100.0 * sum_rbc / target
        w.writerow([f"% Target ({target})", f"{pct_target_reached:.2f}".replace(".", ",")])

    return {
        "n_images": len(rows),
        "sum_rbc": sum_rbc,
        "sum_by_type": sum_by_type,
        "pct_veraendert": pct_veraendert,
        "pct_veraendert_per_2000": pct_veraendert_per_2000,
        "pct_target_reached": pct_target_reached,
    }
