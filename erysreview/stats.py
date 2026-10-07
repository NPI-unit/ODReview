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


def grouped_values(records: list[dict], by: str, metric_key: str, tp_order: list[str] | None = None
                   ) -> dict[str, list[float]]:
    """Group metric values by 'timepoint' (in the study's order) or 'subject'.
    Returns ordered dict."""
    return {k: [v for v, _r in pts] for k, pts in grouped_points(records, by, metric_key, tp_order).items()}


def grouped_points(records: list[dict], by: str, metric_key: str, tp_order: list[str] | None = None
                   ) -> dict[str, list[tuple[float, dict]]]:
    """Like grouped_values, with the record behind every value — what the
    plots need to name a point (Proband · Zeitpunkt)."""
    from .grouping import order_timepoints
    out: dict[str, list[tuple[float, dict]]] = {}
    for r in records:
        out.setdefault(r[by], []).append((metric_value(r, metric_key), r))
    keys = order_timepoints(out, tp_order) if by == "timepoint" else sorted(out, key=natural_key)
    return {k: out[k] for k in keys}


def trajectories(records: list[dict], metric_key: str, tp_order: list[str] | None = None
                 ) -> tuple[list[str], dict[str, list[tuple[int, float, dict]]]]:
    """(timepoints in order, {subject: [(timepoint index, value, record)]}) —
    one line per proband over the timepoints."""
    from .grouping import order_timepoints
    tps = order_timepoints({r["timepoint"] for r in records}, tp_order)
    pos = {t: i for i, t in enumerate(tps)}
    lines: dict[str, list[tuple[int, float, dict]]] = {}
    for r in records:
        lines.setdefault(r["subject"], []).append((pos[r["timepoint"]], metric_value(r, metric_key), r))
    for sub in lines:
        lines[sub].sort(key=lambda t: t[0])
    return tps, {s: lines[s] for s in sorted(lines, key=natural_key)}


def fmt_duration(seconds: float) -> str:
    """h:mm:ss, or m:ss below an hour (the Übersicht's time columns)."""
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def time_totals(records: list[dict]) -> tuple[float, dict[str, float]]:
    """(total review time, time per subject) over the group records."""
    per: dict[str, float] = {}
    total = 0.0
    for r in records:
        total += r["time_s"]
        per[r["subject"]] = per.get(r["subject"], 0.0) + r["time_s"]
    return total, dict(sorted(per.items(), key=lambda kv: natural_key(kv[0])))
