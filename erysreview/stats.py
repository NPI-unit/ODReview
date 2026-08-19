"""Descriptive statistics over (timepoint, subject) groups.

One record per evaluated group, restricted to its effective evaluation set
(the editable 2000-cell cap). The in-app Statistics tab turns these records
into box-plots (by timepoint / by subject) and a descriptive table.
"""

from __future__ import annotations

import statistics
from pathlib import Path

from .evaluation import group_eval_summary
from .grouping import GroupedFolder, UNGROUPED_SUBJECT, UNGROUPED_TIMEPOINT, natural_key
from .profile import ProfileData

# Metric keys that are always available.
METRIC_PCT_CHANGED = "pct_changed"
METRIC_PCT_CHANGED_2000 = "pct_changed_2000"
METRIC_RBC = "rbc"


def collect_group_records(
    folder: Path, profile: ProfileData, grouped: GroupedFolder | None,
) -> list[dict]:
    """Return one record per group that has at least one evaluated image."""
    records: list[dict] = []
    if grouped is None:
        return records
    for tp in grouped.timepoints():
        if tp == UNGROUPED_TIMEPOINT:
            continue
        for sub in grouped.subjects_in(tp):
            if sub == UNGROUPED_SUBJECT:
                continue
            images = grouped.tree[tp][sub]
            summ = group_eval_summary(folder, images, profile)
            if summ["n_images"] == 0:
                continue
            records.append({
                "timepoint": tp,
                "subject": sub,
                "n_images": summ["n_images"],
                "rbc": summ["n_active"],
                "by_type": summ["by_type"],
                "pct_changed": summ["pct_changed"],
                "pct_changed_2000": summ["pct_changed_2000"],
                "reached": summ["reached"],
                "time_s": summ["time_s"],
            })
    return records


def available_metrics(profile: ProfileData) -> list[tuple[str, str]]:
    """List of (metric_key, display_label) selectable in the stats tab."""
    out: list[tuple[str, str]] = [
        (METRIC_PCT_CHANGED, "% verändert"),
        (METRIC_PCT_CHANGED_2000, "% verändert pro 2000"),
    ]
    for t in profile.cell_types:
        if t.key == "normal":
            continue
        out.append((f"pct_type::{t.key}", f"% {t.name or t.key}"))
    out.append((METRIC_RBC, "RBC gesamt (absolut)"))
    for t in profile.cell_types:
        out.append((f"count_type::{t.key}", f"Anzahl {t.name or t.key} (absolut)"))
    return out


def metric_value(record: dict, metric_key: str) -> float:
    """Extract a single metric value from a group record."""
    if metric_key == METRIC_PCT_CHANGED:
        return float(record["pct_changed"])
    if metric_key == METRIC_PCT_CHANGED_2000:
        return float(record["pct_changed_2000"])
    if metric_key == METRIC_RBC:
        return float(record["rbc"])
    if metric_key.startswith("pct_type::"):
        key = metric_key.split("::", 1)[1]
        rbc = record["rbc"]
        n = record["by_type"].get(key, 0)
        return (100.0 * n / rbc) if rbc > 0 else 0.0
    if metric_key.startswith("count_type::"):
        key = metric_key.split("::", 1)[1]
        return float(record["by_type"].get(key, 0))
    return 0.0


def describe(values: list[float]) -> dict:
    """Descriptive statistics for a list of values."""
    vals = [v for v in values if v is not None]
    n = len(vals)
    if n == 0:
        return {"n": 0, "mean": 0.0, "median": 0.0, "sd": 0.0,
                "min": 0.0, "max": 0.0, "q1": 0.0, "q3": 0.0}
    s = sorted(vals)
    mean = statistics.fmean(s)
    median = statistics.median(s)
    sd = statistics.stdev(s) if n > 1 else 0.0
    if n >= 2:
        q = statistics.quantiles(s, n=4, method="inclusive")
        q1, q3 = q[0], q[2]
    else:
        q1 = q3 = s[0]
    return {"n": n, "mean": mean, "median": median, "sd": sd,
            "min": s[0], "max": s[-1], "q1": q1, "q3": q3}


def grouped_values(records: list[dict], by: str, metric_key: str
                   ) -> dict[str, list[float]]:
    """Group metric values by 'timepoint' or 'subject'. Returns ordered dict."""
    out: dict[str, list[float]] = {}
    for r in records:
        key = r[by]
        out.setdefault(key, []).append(metric_value(r, metric_key))
    return dict(sorted(out.items(), key=lambda kv: natural_key(kv[0])))
