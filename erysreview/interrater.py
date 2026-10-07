"""Inter-/intra-rater agreement (mockup v6) — without any UI.

A second rater's package (e.g. Anna's) is not merged into my reviews: her
reviews are stored beside mine, in `<folder>/.erys_cache/raters/<name>/`.

Rule: only identical masks. The cells are compared one to one by their
mask id, so a package is accepted image by image only where its mask file is
byte-identical to the local one (computed once, then shared as packages).
Images with other masks are named and left out; no matching by overlap.

Per cell and rater the category is the cell's type, or "excluded" (removed
by hand, or auto-excluded at the border / by the size filter and not
re-included) — the same activity rule as the status line. Manually added
cells exist for one rater only; they are counted, not compared.

Measures:
  - percent agreement and Cohen's κ (with 95 % CI) over all compared cells
  - per proband × timepoint a chosen metric (default "% verändert pro
    2000"; selection.json per B, with the images left out) for both raters, and on these
    pairs ICC(2,1) — two-way random, absolute agreement, single rater
    (Shrout & Fleiss 1979; McGraw & Wong 1996 for the CI) — and Bland-Altman
    (mean difference, ±1.96 SD limits of agreement).
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import math
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .persistence import ReviewState, cache_dir_for, review_dir, write_json_atomic
from .profile import ProfileData

EXCLUDED = "_excluded"


# ─────────────────────────────────────────────
# Storage
# ─────────────────────────────────────────────
def raters_dir(folder: Path) -> Path:
    return cache_dir_for(folder) / "raters"


def rater_dir(folder: Path, name: str) -> Path:
    return review_dir(folder, name)


def rater_info(folder: Path, name: str) -> dict:
    p = rater_dir(folder, name) / "rater.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def list_raters(folder: Path) -> list[str]:
    """Names of the second ratings stored for this folder."""
    d = raters_dir(folder)
    out = []
    if d.is_dir():
        for sub in sorted(d.iterdir()):
            meta = sub / "rater.json"
            if meta.is_file():
                try:
                    out.append(json.loads(meta.read_text(encoding="utf-8")).get("name") or sub.name)
                except ValueError:
                    out.append(sub.name)
    return out


def load_review(folder: Path, rater: str | None, stem: str) -> ReviewState | None:
    """A review of one rating: rater None = the main reviews (A). None if
    that rating has no review for the image."""
    p = review_dir(folder, rater) / f"{stem}_review.json"
    return ReviewState.load_file(p) if p.is_file() else None


def load_rater_review(folder: Path, name: str, stem: str) -> ReviewState | None:
    return load_review(folder, name, stem)


def _write_rater_meta(folder: Path, name: str, **extra) -> None:
    meta = rater_info(folder, name)
    meta.update(extra)
    meta["name"] = name
    write_json_atomic(rater_dir(folder, name) / "rater.json", meta)


# ─────────────────────────────────────────────
# Import with the identical-masks rule
# ─────────────────────────────────────────────
def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _local_mask_digest(folder: Path, stem: str) -> str | None:
    """Digest of the mask the app would show for this image (cache first,
    then the export layout next to the image — as mask_read_paths)."""
    from .cellpose_runner import mask_read_paths
    npy = cache_dir_for(folder) / f"{stem}_masks.npy"
    if not npy.is_file():
        npy, _meta = mask_read_paths(folder, folder / f"{stem}.tif")
    try:
        return _digest(npy.read_bytes()) if npy is not None else None
    except OSError:
        return None


def check_package(zip_path: Path, folder: Path) -> dict[str, str]:
    """{stem: "same" | "different" | "no_local" | "no_mask" | "no_review"} —
    whether each image of the package may be compared."""
    out: dict[str, str] = {}
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        for stem in manifest.get("stems", []):
            npy = f"cache/{stem}_masks.npy"
            if npy not in names:
                out[stem] = "no_mask"
                continue
            local = _local_mask_digest(folder, stem)
            if local is None:
                out[stem] = "no_local"
            elif local != _digest(zf.read(npy)):
                out[stem] = "different"
            elif f"cache/{stem}_review.json" not in names:
                out[stem] = "no_review"
            else:
                out[stem] = "same"
    return out


def import_rater(zip_path: Path, folder: Path, name: str | None = None) -> dict:
    """Store a package's reviews as a second rater — only for images whose
    masks are identical to the local ones. Never touches my reviews.
    Returns {"name", "imported": [stems], "rejected": {stem: reason}}.
    Raises ValueError("no_identical_masks") if not a single image qualifies."""
    with zipfile.ZipFile(zip_path) as zf:
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
    rater = (name or manifest.get("reviewer") or Path(zip_path).stem).strip()
    status = check_package(zip_path, folder)
    ok = [s for s, st in status.items() if st == "same"]
    rejected = {s: st for s, st in status.items() if st != "same"}
    if not ok:
        raise ValueError("no_identical_masks")
    d = rater_dir(folder, rater)
    d.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        for stem in ok:
            (d / f"{stem}_review.json").write_bytes(zf.read(f"cache/{stem}_review.json"))
    _write_rater_meta(folder, rater, kind="package", package=Path(zip_path).name,
                      imported_at=_dt.datetime.now().isoformat(timespec="seconds"),
                      created_at=manifest.get("created_at"), stems=ok, rejected=rejected,
                      masks={stem: _local_mask_digest(folder, stem) for stem in ok})
    ensure_a_backup(folder, rater)
    return {"name": rater, "imported": ok, "rejected": rejected}


def create_self_rating(folder: Path, name: str, stems: list[str], blind: bool = False) -> None:
    """A second rating by me (intra-rater) or anyone at this computer: an
    empty namespace on today's masks. The masks' digests are kept, so a later
    re-segmentation takes the image out of the comparison."""
    rater_dir(folder, name).mkdir(parents=True, exist_ok=True)
    _write_rater_meta(folder, name, kind="self", blind=bool(blind),
                      created_at=_dt.datetime.now().isoformat(timespec="seconds"),
                      masks={stem: _local_mask_digest(folder, stem) for stem in stems})
    ensure_a_backup(folder, name)


def ensure_a_backup(folder: Path, rater: str) -> str:
    """The backup "A vor Abgleich mit <rater>": made once when the comparison
    starts, remembered in the rater's meta. The statistics compare B with
    it, so corrections taken over from B later cannot flatter κ / ICC.
    Returns the backup id."""
    from . import backups
    bid = rater_info(folder, rater).get("a_backup")
    if bid and backups.get(folder, bid) is not None:
        return bid
    b = backups.create(folder, f"A vor Abgleich mit {rater}", "abgleich")
    _write_rater_meta(folder, rater, a_backup=b.id)
    return b.id


def a_backup_dir(folder: Path, rater: str) -> Path | None:
    from . import backups
    bid = rater_info(folder, rater).get("a_backup")
    b = backups.get(folder, bid) if bid else None
    return b.path if b is not None else None


def decisions(folder: Path, rater: str) -> dict[str, dict[str, str]]:
    """{stem: {cell id: "taken" | "kept"}} — what was decided per difference."""
    p = rater_dir(folder, rater) / "decisions.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def set_decision(folder: Path, rater: str, stem: str, cid: int, value: str | None) -> None:
    d = decisions(folder, rater)
    per = d.setdefault(stem, {})
    if value is None:
        per.pop(str(cid), None)
    else:
        per[str(cid)] = value
    write_json_atomic(rater_dir(folder, rater) / "decisions.json", d)


# ── what goes into the agreement statistics (per B) ──
DEFAULT_METRIC = "pct_changed_2000"
DIFF_MODES = ("all", "changed")


def _selection_path(folder: Path, rater: str) -> Path:
    return rater_dir(folder, rater) / "auswahl.json"


def selection(folder: Path, rater: str) -> dict:
    """{"excluded": set of stems left out of the statistics, "metric": key}.
    Default: every common image, "% verändert pro 2000"."""
    try:
        d = json.loads(_selection_path(folder, rater).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    return {"excluded": set(d.get("excluded") or []), "metric": d.get("metric") or DEFAULT_METRIC,
            "diffs": d.get("diffs") if d.get("diffs") in DIFF_MODES else "all"}


def _write_selection(folder: Path, rater: str, sel: dict) -> None:
    write_json_atomic(_selection_path(folder, rater),
                      {"excluded": sorted(sel["excluded"]), "metric": sel["metric"], "diffs": sel["diffs"]})


def set_excluded(folder: Path, rater: str, stems, excluded: bool) -> None:
    sel = selection(folder, rater)
    sel["excluded"] = (sel["excluded"] | set(stems)) if excluded else (sel["excluded"] - set(stems))
    _write_selection(folder, rater, sel)


def set_metric(folder: Path, rater: str, metric: str) -> None:
    sel = selection(folder, rater)
    sel["metric"] = metric
    _write_selection(folder, rater, sel)


def set_diff_mode(folder: Path, rater: str, mode: str) -> None:
    sel = selection(folder, rater)
    sel["diffs"] = mode if mode in DIFF_MODES else "all"
    _write_selection(folder, rater, sel)


def is_shown_diff(a: str, b: str, mode: str, default: str = "normal") -> bool:
    """Which differences the comparison walks through. "all": every one.
    "changed": only where A or B gave a type other than normal — not the ones
    that are just normal ↔ excluded (counted or not)."""
    if mode != "changed":
        return True
    return any(c not in (default, EXCLUDED) for c in (a, b))


def common_stems(folder: Path, rater: str) -> set[str]:
    """Images both A and B have rated (a review in both namespaces)."""
    def stems(d: Path) -> set[str]:
        # not macOS' "._…" companions (exFAT/FAT drives such as USB sticks)
        return ({f.name[:-len("_review.json")] for f in d.glob("*_review.json") if not f.name.startswith("._")}
                if d.is_dir() else set())
    return stems(review_dir(folder, None)) & stems(rater_dir(folder, rater))


_PAIR_CACHE: dict[tuple, tuple | None] = {}


def _mtime(p: Path | None) -> float:
    try:
        return p.stat().st_mtime if p is not None else 0.0
    except OSError:
        return 0.0


def pair_cats(folder: Path, info, profile: ProfileData, rater_b: str):
    """(A's categories, B's categories — mask cells plus counted manual cells —,
    differs) for one image, A as it is now; None if not comparable. Cached on
    the review and mask file times, so list filters and the proband line do
    not reload masks on every click."""
    from .cellpose_runner import mask_read_paths
    from .counts import profile_signature
    stem = info.path.stem
    key = (str(folder), rater_b, stem, profile_signature(profile),
           _mtime(review_dir(folder, None) / f"{stem}_review.json"),
           _mtime(rater_dir(folder, rater_b) / f"{stem}_review.json"),
           _mtime(mask_read_paths(folder, info.path)[0]))
    if key in _PAIR_CACHE:
        return _PAIR_CACHE[key]
    pair = _image_pair(folder, info, profile, None, rater_b)
    out = None
    if pair is not None and pair != "changed":
        ids, auto, ra, rb, _m = pair
        ca, cb = categories(ids, auto, ra, profile), categories(ids, auto, rb, profile)
        default = profile.default_type_key()
        diffs = [(ca[c], cb[c]) for c in ids if ca[c] != cb[c]]
        out = (list(ca.values()) + _manual(ra, default), list(cb.values()) + _manual(rb, default),
               {m: any(is_shown_diff(a, b, m, default) for a, b in diffs) for m in DIFF_MODES})
    if len(_PAIR_CACHE) > 4000:
        _PAIR_CACHE.clear()
    _PAIR_CACHE[key] = out
    return out


def _manual(rv: ReviewState, default: str) -> list[str]:
    return [rv.types.get(str(m.cell_id), default) for m in rv.manual_cells if m.cell_id not in rv.force_excluded]


def image_differs(folder: Path, info, profile: ProfileData, rater_b: str, mode: str = "all") -> bool:
    """Does A (now) rate at least one cell of this image differently from B
    (in the sense of `mode`, see is_shown_diff)?"""
    pc = pair_cats(folder, info, profile, rater_b)
    return bool(pc and pc[2].get(mode, False))


def group_values(folder: Path, infos, profile: ProfileData, rater_b: str, excluded: set[str],
                 metric: str) -> tuple[float, float, int] | None:
    """The metric for A (now) and B over one proband's ticked common images:
    (A, B, images); None if there is none."""
    target = max(1, profile.target_cells)
    ga: list[str] = []
    gb: list[str] = []
    n = 0
    for info in infos:
        if info.path.stem in excluded:
            continue
        pc = pair_cats(folder, info, profile, rater_b)
        if pc is None:
            continue
        ga += pc[0]; gb += pc[1]; n += 1
    return (metric_of(ga, metric, target), metric_of(gb, metric, target), n) if n else None


def group_record(cats: list[str], target: int) -> dict:
    """A rater's group as a stats record (stats.metric_value reads it), from
    the categories of all its cells (EXCLUDED ones do not count)."""
    act = [c for c in cats if c != EXCLUDED]
    by_type: dict[str, int] = {}
    for c in act:
        by_type[c] = by_type.get(c, 0) + 1
    n = len(act)
    pct = 100.0 * (n - by_type.get("normal", 0)) / n if n else 0.0
    return {"rbc": n, "by_type": by_type, "pct_changed": pct,
            "pct_changed_2000": pct * target / n if n else 0.0}


def metric_of(cats: list[str], metric: str, target: int) -> float:
    from .stats import metric_value
    return metric_value(group_record(cats, target), metric)


def take_cell(target: ReviewState, source: ReviewState, cid: int) -> dict:
    """Give `target` (A) the rating `source` (B) has for mask cell `cid`:
    type and counted / excluded / re-included. Returns what A had before
    (for undo with restore_cell)."""
    key = str(cid)
    before = {"type": target.types.get(key), "excl": cid in target.force_excluded,
              "incl": cid in target.force_included}
    for name in ("force_excluded", "force_included"):
        lst = [x for x in getattr(target, name) if x != cid]
        if cid in getattr(source, name):
            lst.append(cid)
        setattr(target, name, lst)
    if key in source.types:
        target.types[key] = source.types[key]
    else:
        target.types.pop(key, None)
    return before


def restore_cell(target: ReviewState, cid: int, before: dict) -> None:
    key = str(cid)
    target.force_excluded = [x for x in target.force_excluded if x != cid] + ([cid] if before["excl"] else [])
    target.force_included = [x for x in target.force_included if x != cid] + ([cid] if before["incl"] else [])
    if before["type"] is None:
        target.types.pop(key, None)
    else:
        target.types[key] = before["type"]


def masks_unchanged(folder: Path, rater: str | None, stem: str) -> bool:
    """True if the image's mask is still the one the rating was made on."""
    if not rater:
        return True
    want = (rater_info(folder, rater).get("masks") or {}).get(stem)
    return want is None or want == _local_mask_digest(folder, stem)


def rater_study(base: ProfileData, rater: str, blind: bool = False) -> ProfileData:
    """The study to open a second rating with: the base study's settings and
    folder, reading/writing the rater's namespace. Saved; reused if present."""
    import copy as _copy
    from .profile import load_profile, save_profile
    name = f"{base.name} · {rater}"
    existing = load_profile(name)
    if existing is not None and existing.rater == rater:
        return existing
    p = _copy.deepcopy(base)
    p.name, p.rater, p.rater_base, p.blind = name, rater, base.name, bool(blind)
    p.created_at = p.updated_at = ""
    p.sidebar_open_groups = []
    save_profile(p)
    return p


def blind_locked(folder: Path, rater: str) -> tuple[bool, int, int]:
    """(locked, finished, total) — a blind rating keeps the comparison closed
    until every image it covers is marked finished."""
    info = rater_info(folder, rater)
    if not info.get("blind"):
        return False, 0, 0
    stems = list((info.get("masks") or {}).keys())
    done = sum(1 for st in stems if (load_review(folder, rater, st) or ReviewState()).is_finished)
    return done < len(stems), done, len(stems)


# ─────────────────────────────────────────────
# Cells
# ─────────────────────────────────────────────
def mask_cells(masks: np.ndarray, profile: ProfileData) -> tuple[list[int], set[int]]:
    """Mask cell ids and the auto-excluded ones (border / size filter) — the
    same for both raters, because masks and study are the same."""
    from .editor import ReviewEditor
    ed = ReviewEditor(profile)
    ed.load(masks, ReviewState())
    return sorted(ed.cell_infos), ed.border_ids | ed.duplicate_ids


def categories(cell_ids, auto: set[int], review: ReviewState, profile: ProfileData) -> dict[int, str]:
    """{cell id: type key or EXCLUDED} — the status line's activity rule."""
    excl, incl = set(review.force_excluded), set(review.force_included)
    default = profile.default_type_key()
    out = {}
    for cid in cell_ids:
        if cid in excl or (cid in auto and cid not in incl):
            out[cid] = EXCLUDED
        else:
            out[cid] = review.types.get(str(cid), default)
    return out


# ─────────────────────────────────────────────
# Statistics
# ─────────────────────────────────────────────
def cohen_kappa(a: list[str], b: list[str]) -> tuple[float, float, float]:
    """κ with a 95 % CI (large-sample SE)."""
    n = len(a)
    if n == 0:
        return float("nan"), float("nan"), float("nan")
    cats = sorted(set(a) | set(b))
    idx = {c: i for i, c in enumerate(cats)}
    m = np.zeros((len(cats), len(cats)))
    for x, y in zip(a, b):
        m[idx[x], idx[y]] += 1
    po = np.trace(m) / n
    pe = float((m.sum(1) / n) @ (m.sum(0) / n))
    if pe >= 1:
        return 1.0, 1.0, 1.0
    k = (po - pe) / (1 - pe)
    se = math.sqrt(po * (1 - po) / (n * (1 - pe) ** 2))
    return k, max(-1.0, k - 1.96 * se), min(1.0, k + 1.96 * se)


def icc_2_1(x: list[float], y: list[float]) -> tuple[float, float, float]:
    """ICC(2,1) for two raters — see icc_2_1_matrix."""
    return icc_2_1_matrix(np.column_stack([x, y]))


def icc_2_1_matrix(data) -> tuple[float, float, float]:
    """ICC(2,1): two-way random effects, absolute agreement, single rater
    (Shrout & Fleiss 1979), with the 95 % CI of McGraw & Wong (1996).
    `data`: n subjects × k raters."""
    from scipy.stats import f as fdist
    data = np.asarray(data, dtype=float)
    n, k = data.shape
    if n < 3:
        return float("nan"), float("nan"), float("nan")
    gm = data.mean()
    ss_r = k * ((data.mean(1) - gm) ** 2).sum()
    ss_c = n * ((data.mean(0) - gm) ** 2).sum()
    ss_e = ((data - gm) ** 2).sum() - ss_r - ss_c
    msr, msc = ss_r / (n - 1), ss_c / (k - 1)
    mse = ss_e / ((n - 1) * (k - 1))
    denom = msr + (k - 1) * mse + k * (msc - mse) / n
    if denom <= 0:
        return float("nan"), float("nan"), float("nan")
    icc = (msr - mse) / denom
    if icc >= 1.0:                      # perfect agreement: the CI formula divides by zero
        return 1.0, float("nan"), float("nan")
    try:
        a = k * icc / (n * (1 - icc))
        b = 1 + k * icc * (n - 1) / (n * (1 - icc))
        v = (a * msc + b * mse) ** 2 / ((a * msc) ** 2 / (k - 1) + (b * mse) ** 2 / ((n - 1) * (k - 1)))
        fl, fu = fdist.ppf(0.975, n - 1, v), fdist.ppf(0.975, v, n - 1)
        lo = n * (msr - fl * mse) / (fl * (k * msc + (k * n - k - n) * mse) + n * msr)
        hi = n * (fu * msr - mse) / (k * msc + (k * n - k - n) * mse + n * fu * msr)
    except (ZeroDivisionError, FloatingPointError, ValueError):
        lo = hi = float("nan")
    return float(icc), float(lo), float(hi)


def bland_altman(x: list[float], y: list[float]) -> tuple[float, float, float]:
    """Mean difference (y − x) and the ±1.96 SD limits of agreement."""
    d = np.asarray(y, float) - np.asarray(x, float)
    if len(d) < 2:
        return float("nan"), float("nan"), float("nan")
    md, sd = float(d.mean()), float(d.std(ddof=1))
    return md, md - 1.96 * sd, md + 1.96 * sd


# ─────────────────────────────────────────────
# The comparison
# ─────────────────────────────────────────────
@dataclass
class GroupPair:
    tp: str
    sub: str
    n_images: int
    active_a: int
    active_b: int
    pct_a: float          # % verändert, rater A
    pct_b: float
    val_a: float = 0.0    # the chosen metric (ICC, Bland-Altman, plots)
    val_b: float = 0.0


@dataclass
class Agreement:
    rater_a: str
    rater_b: str
    categories: list[str]
    matrix: np.ndarray                    # rows A, columns B
    n_cells: int
    n_same: int
    kappa: tuple[float, float, float]
    groups: list[GroupPair]
    icc: tuple[float, float, float]
    ba: tuple[float, float, float]
    n_images: int
    manual_only_a: int
    manual_only_b: int
    per_image: list[tuple[str, int, int]] = field(default_factory=list)   # (name, n, same)
    cells: list[tuple[str, int, str, str]] = field(default_factory=list)  # (image, id, A, B)
    n_masks_changed: int = 0                     # left out: masks changed since a rating
    metric: str = DEFAULT_METRIC
    n_left_out: int = 0                          # common images unticked in the list

    @property
    def pct_same(self) -> float:
        return 100.0 * self.n_same / self.n_cells if self.n_cells else 0.0


def _pct_changed(cats: dict[int, str], manual: list[str]) -> tuple[int, float]:
    act = [c for c in cats.values() if c != EXCLUDED] + manual
    changed = sum(1 for c in act if c != "normal")
    return len(act), (100.0 * changed / len(act) if act else 0.0)


def _image_pair(folder: Path, info, profile: ProfileData, base: str | None, rater_b: str,
                a_dir: Path | None = None):
    """(ids, auto, review A, review B) for one image, or None if it is not
    comparable (no B review, no masks, or masks changed since a rating)."""
    from .cellpose_runner import load_cached_masks
    stem = info.path.stem
    rb = load_review(folder, rater_b, stem)
    if rb is None:
        return None
    if not (masks_unchanged(folder, rater_b, stem) and masks_unchanged(folder, base, stem)):
        return "changed"
    masks = load_cached_masks(folder, info.path)
    if masks is None:
        return None
    if a_dir is not None:                    # A as backed up when the comparison started
        p = a_dir / f"{stem}_review.json"
        ra = ReviewState.load_file(p) if p.is_file() else ReviewState()
    else:
        ra = load_review(folder, base, stem) or ReviewState()
    ids, auto = mask_cells(masks, profile)
    return ids, auto, ra, rb, masks


def compare(folder: Path, grouped, profile: ProfileData, rater_b: str, rater_a: str = "",
            base: str | None = None, a_dir: Path | None = None, excluded: set[str] | None = None,
            metric: str = DEFAULT_METRIC) -> Agreement:
    """Rating A (`base`; None = the main reviews) against `rater_b`, over
    every image both have on unchanged masks, except the stems in `excluded`
    (unticked in the list). ICC and Bland-Altman run on `metric` per group.
    `rater_a` is A's display name."""
    excluded = excluded or set()
    target = max(1, profile.target_cells)
    from .grouping import UNGROUPED_TIMEPOINT
    a_all, b_all, cells, per_image, groups = [], [], [], [], []
    man_a = man_b = n_images = n_changed = n_left = 0
    default = profile.default_type_key()
    for tp in grouped.timepoints():
        if tp == UNGROUPED_TIMEPOINT:
            continue
        for sub in grouped.subjects_in(tp):
            ga: list[str] = []
            gb: list[str] = []
            n_grp = 0
            for info in grouped.tree[tp][sub]:
                if info.path.stem in excluded:
                    n_left += load_review(folder, rater_b, info.path.stem) is not None
                    continue
                pair = _image_pair(folder, info, profile, base, rater_b, a_dir)
                if pair == "changed":
                    n_changed += 1
                    continue
                if pair is None:
                    continue
                ids, auto, ra, rb, _m = pair
                ca, cb = categories(ids, auto, ra, profile), categories(ids, auto, rb, profile)
                same = 0
                for cid in ids:
                    a_all.append(ca[cid]); b_all.append(cb[cid])
                    same += ca[cid] == cb[cid]
                    cells.append((info.path.name, cid, ca[cid], cb[cid]))
                ma = [ra.types.get(str(m.cell_id), default) for m in ra.manual_cells
                      if m.cell_id not in ra.force_excluded]
                mb = [rb.types.get(str(m.cell_id), default) for m in rb.manual_cells
                      if m.cell_id not in rb.force_excluded]
                man_a += len(ma); man_b += len(mb)
                per_image.append((info.path.name, len(ids), same))
                ga += list(ca.values()) + ma
                gb += list(cb.values()) + mb
                n_grp += 1
                n_images += 1
            if n_grp:
                na, pa = _pct_changed(dict(enumerate(ga)), [])
                nb, pb = _pct_changed(dict(enumerate(gb)), [])
                groups.append(GroupPair(tp, sub, n_grp, na, nb, pa, pb,
                                        metric_of(ga, metric, target), metric_of(gb, metric, target)))
    order = [t.key for t in profile.cell_types]
    present = set(a_all) | set(b_all)
    cats = [c for c in order if c in present] + sorted(present - set(order) - {EXCLUDED}) + \
        ([EXCLUDED] if EXCLUDED in present else [])
    idx = {c: i for i, c in enumerate(cats)}
    m = np.zeros((len(cats), len(cats)), dtype=int)
    for x, y in zip(a_all, b_all):
        m[idx[x], idx[y]] += 1
    xa, yb = [g.val_a for g in groups], [g.val_b for g in groups]
    ag = Agreement(rater_a or "A", rater_b, cats, m, len(a_all), int(np.trace(m)) if len(cats) else 0,
                   cohen_kappa(a_all, b_all), groups, icc_2_1(xa, yb), bland_altman(xa, yb),
                   n_images, man_a, man_b, per_image, cells)
    ag.n_masks_changed = n_changed
    ag.metric, ag.n_left_out = metric, n_left
    return ag


@dataclass
class CellDiff:
    cid: int
    a: str
    b: str
    bbox: tuple[int, int, int, int]       # x0, y0, x1, y1


def image_diffs(folder: Path, info, profile: ProfileData, rater_b: str, base: str | None = None):
    """(masks, review A, review B, [CellDiff]) for one image — what the
    comparison view shows; None if the image is not comparable."""
    pair = _image_pair(folder, info, profile, base, rater_b)
    if pair is None or pair == "changed":
        return None
    ids, auto, ra, rb, masks = pair
    ca, cb = categories(ids, auto, ra, profile), categories(ids, auto, rb, profile)
    from .overlay import compute_cell_infos
    infos = compute_cell_infos(masks, set())
    diffs = [CellDiff(cid, ca[cid], cb[cid],
                      (infos[cid].bbox_x0, infos[cid].bbox_y0, infos[cid].bbox_x1, infos[cid].bbox_y1))
             for cid in ids if ca[cid] != cb[cid] and cid in infos]
    diffs.sort(key=lambda d: (d.bbox[1] // 80, d.bbox[0]))      # reading order, row by row
    return masks, ra, rb, diffs


# ─────────────────────────────────────────────
# More than two ratings
# ─────────────────────────────────────────────
def fleiss_kappa(table) -> float:
    """Fleiss' κ (1971) from an n_subjects × n_categories table of counts
    (every row sums to the number of raters)."""
    t = np.asarray(table, dtype=float)
    n, _ = t.shape
    r = t.sum(1)
    if n == 0 or not np.all(r == r[0]) or r[0] < 2:
        return float("nan")
    m = r[0]
    p_j = t.sum(0) / (n * m)
    p_i = ((t * t).sum(1) - m) / (m * (m - 1))
    pbar, pe = p_i.mean(), (p_j ** 2).sum()
    return float((pbar - pe) / (1 - pe)) if pe < 1 else 1.0


@dataclass
class MultiAgreement:
    raters: list[str]
    n_cells: int
    n_images: int
    fleiss: float
    icc: tuple[float, float, float]
    groups: list[tuple[str, str, list[float]]]       # tp, sub, the metric per rater


def compare_many(folder: Path, grouped, profile: ProfileData, ratings: list[str | None],
                 labels: list[str], a_dir: Path | None = None, excluded: set[str] | None = None,
                 metric: str = DEFAULT_METRIC) -> MultiAgreement:
    """All ratings at once, on the images every one of them has (unchanged
    masks), without `excluded`: Fleiss' κ over the cells, ICC(2,1) over the
    groups' `metric`."""
    excluded = excluded or set()
    target = max(1, profile.target_cells)
    from .cellpose_runner import load_cached_masks
    from .grouping import UNGROUPED_TIMEPOINT
    rows: list[list[str]] = []
    groups = []
    n_images = 0
    for tp in grouped.timepoints():
        if tp == UNGROUPED_TIMEPOINT:
            continue
        for sub in grouped.subjects_in(tp):
            per_rater: list[list[str]] = [[] for _ in ratings]
            n_grp = 0
            for info in grouped.tree[tp][sub]:
                stem = info.path.stem
                if stem in excluded:
                    continue
                def _a():
                    if a_dir is not None:
                        p = a_dir / f"{stem}_review.json"
                        return ReviewState.load_file(p) if p.is_file() else ReviewState()
                    return load_review(folder, None, stem) or ReviewState()
                revs = [load_review(folder, r, stem) if r else _a() for r in ratings]
                if any(rv is None for rv in revs) or not all(masks_unchanged(folder, r, stem) for r in ratings):
                    continue
                masks = load_cached_masks(folder, info.path)
                if masks is None:
                    continue
                ids, auto = mask_cells(masks, profile)
                cats = [categories(ids, auto, rv, profile) for rv in revs]
                for cid in ids:
                    rows.append([c[cid] for c in cats])
                for k, c in enumerate(cats):
                    per_rater[k] += list(c.values())
                n_grp += 1
                n_images += 1
            if n_grp:
                groups.append((tp, sub, [metric_of(v, metric, target) for v in per_rater]))
    allcats = sorted({c for r in rows for c in r})
    idx = {c: i for i, c in enumerate(allcats)}
    table = np.zeros((len(rows), len(allcats)), dtype=int)
    for i, r in enumerate(rows):
        for c in r:
            table[i, idx[c]] += 1
    icc = icc_2_1_matrix([g[2] for g in groups]) if groups else (float("nan"),) * 3
    return MultiAgreement(labels, len(rows), n_images, fleiss_kappa(table) if rows else float("nan"), icc, groups)


def write_csv(ag: Agreement, path: Path, type_names: dict[str, str]) -> None:
    """One row per compared cell, then a summary block."""
    import csv
    name = lambda k: type_names.get(k, "ausgeschlossen" if k == EXCLUDED else k)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["bild", "zelle", f"rater_{ag.rater_a}", f"rater_{ag.rater_b}", "gleich"])
        for img, cid, a, b in ag.cells:
            w.writerow([img, cid, name(a), name(b), int(a == b)])
        w.writerow([])
        m = ag.metric.replace("::", "_")
        w.writerow(["zeitpunkt", "proband", "bilder", f"{m}_{ag.rater_a}", f"{m}_{ag.rater_b}"])
        for g in ag.groups:
            w.writerow([g.tp, g.sub, g.n_images, f"{g.val_a:.2f}", f"{g.val_b:.2f}"])
        w.writerow([])
        k, klo, khi = ag.kappa
        i, ilo, ihi = ag.icc
        md, lo, hi = ag.ba
        for row in (("zellen_verglichen", ag.n_cells), ("uebereinstimmung_pct", f"{ag.pct_same:.2f}"),
                    ("cohen_kappa", f"{k:.3f}"), ("kappa_ci95", f"{klo:.3f}–{khi:.3f}"),
                    (f"icc_2_1_{m}", f"{i:.3f}"), ("icc_ci95", f"{ilo:.3f}–{ihi:.3f}"),
                    ("bland_altman_mittlere_differenz", f"{md:.3f}"), ("loa_unten", f"{lo:.3f}"),
                    ("loa_oben", f"{hi:.3f}"), ("manuelle_zellen_nur_A", ag.manual_only_a),
                    ("manuelle_zellen_nur_B", ag.manual_only_b),
                    ("kennzahl", ag.metric), ("bilder_nicht_in_rechnung", ag.n_left_out)):
            w.writerow(row)
