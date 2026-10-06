"""Cell-area outcome — computed on request, never as part of an export.

The per-cell area has always been exported (`area` in `*_stats.csv`, `cells.area`
in erys.db). What was missing is the aggregate: a mean per image and per
timepoint/subject, which is what answers "are the cells getting bigger or
smaller across the study?".

**Which cells.** `is_active` AND the image is inside the evaluation
(`effective_eval_stems`). That is deliberately the same sample that RBC and
%verändert_2000 are built from, so two numbers for one subject never rest on
different denominators. It also excludes border cells, which matters more here
than anywhere else: a cell cut off by the image edge has a systematically
truncated area and would drag every mean down.

**Unit.** Pixels². The profile carries no µm-per-pixel scale, and inventing one
would produce numbers that look physical but are not. Areas are comparable
across images taken at one magnification — which is the within-study comparison
this is for.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from pathlib import Path

from .evaluation import effective_eval_stems
from .grouping import GroupedFolder
from .image_state import compute_image_state
from .profile import ProfileData


@dataclass
class AreaStats:
    """Descriptive statistics over one set of cell areas, in px²."""
    n: int = 0
    mean: float = 0.0
    median: float = 0.0
    sd: float = 0.0
    minimum: float = 0.0
    maximum: float = 0.0

    @classmethod
    def of(cls, areas: list[float]) -> "AreaStats":
        if not areas:
            return cls()
        return cls(
            n=len(areas),
            mean=statistics.fmean(areas),
            median=statistics.median(areas),
            # Sample SD is undefined for a single value; 0.0 is the honest
            # rendering of "no spread measurable", not a computed zero.
            sd=statistics.stdev(areas) if len(areas) > 1 else 0.0,
            minimum=min(areas),
            maximum=max(areas),
        )


@dataclass
class GroupAreas:
    timepoint: str
    subject: str
    stats: AreaStats
    per_image: list[tuple[str, AreaStats]] = field(default_factory=list)


def compute_areas(folder: Path, profile: ProfileData,
                  grouped: GroupedFolder | None,
                  progress=None) -> list[GroupAreas]:
    """Area statistics per image and per (timepoint, subject).

    `progress(done, total)` is called between images so a long run can show
    movement; it is optional and never required for correctness.
    """
    out: list[GroupAreas] = []
    if grouped is None or not grouped.tree:
        return out

    total = sum(len(grouped.tree[tp][sub])
                for tp in grouped.timepoints() for sub in grouped.subjects_in(tp))
    done = 0

    for tp in grouped.timepoints():
        for sub in grouped.subjects_in(tp):
            images = grouped.tree[tp][sub]
            eval_stems = effective_eval_stems(folder, images, profile)
            group_areas: list[float] = []
            per_image: list[tuple[str, AreaStats]] = []
            for info in images:
                done += 1
                if progress is not None:
                    progress(done, total)
                if info.path.stem not in eval_stems:
                    continue
                state = compute_image_state(folder, info, profile)
                if state is None:
                    continue
                areas = [float(state.cell_infos[c].area)
                         for c in state.active_ids if c in state.cell_infos]
                if not areas:
                    continue
                group_areas.extend(areas)
                per_image.append((info.path.stem, AreaStats.of(areas)))
            if per_image:
                out.append(GroupAreas(tp, sub, AreaStats.of(group_areas), per_image))
    return out


def to_rows(groups: list[GroupAreas]) -> list[dict]:
    """Flatten to CSV rows: one per group, then one per image inside it."""
    rows: list[dict] = []
    for g in groups:
        for level, name, st in ([("Gruppe", "", g.stats)]
                                + [("Bild", stem, s) for stem, s in g.per_image]):
            rows.append({
                "ebene": level, "zeitpunkt": g.timepoint, "proband": g.subject,
                "bild": name, "n_zellen": st.n,
                "flaeche_mittel_px2": round(st.mean, 1),
                "flaeche_median_px2": round(st.median, 1),
                "flaeche_sd_px2": round(st.sd, 1),
                "flaeche_min_px2": round(st.minimum, 1),
                "flaeche_max_px2": round(st.maximum, 1),
            })
    return rows


def write_csv(groups: list[GroupAreas], dest) -> None:
    """The Zellflächen table as CSV (UTF-8 with BOM, so Excel reads umlauts)."""
    import csv
    rows = to_rows(groups)
    with open(dest, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["ebene"])
        w.writeheader()
        w.writerows(rows)
