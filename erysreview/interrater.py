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
  - per proband × timepoint "% verändert" for both raters, and on these
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

from .persistence import ReviewState, cache_dir_for, write_json_atomic
from .profile import ProfileData

EXCLUDED = "_excluded"


# ─────────────────────────────────────────────
# Storage
# ─────────────────────────────────────────────
def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", name.strip()).strip("_") or "rater"


def raters_dir(folder: Path) -> Path:
    return cache_dir_for(folder) / "raters"


def rater_dir(folder: Path, name: str) -> Path:
    return raters_dir(folder) / _slug(name)


def list_raters(folder: Path) -> list[str]:
    """Names of the second raters stored for this folder."""
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


def load_rater_review(folder: Path, name: str, stem: str) -> ReviewState | None:
    p = rater_dir(folder, name) / f"{stem}_review.json"
    return ReviewState.load_file(p) if p.is_file() else None


# ─────────────────────────────────────────────
# Import with the identical-masks rule
# ─────────────────────────────────────────────
def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _local_mask_digest(folder: Path, stem: str) -> str | None:
    """Digest of the mask the app would show for this image (cache first,
    then the export layout next to the image — as mask_read_paths)."""
    from .cellpose_runner import mask_read_paths
    npy, _meta = mask_read_paths(folder, folder / f"{stem}.tif")
    if npy is None:
        hits = list(cache_dir_for(folder).glob(f"{stem}_masks.npy"))
        npy = hits[0] if hits else None
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
    write_json_atomic(d / "rater.json", {
        "name": rater, "package": Path(zip_path).name,
        "imported_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "created_at": manifest.get("created_at"), "stems": ok, "rejected": rejected})
    return {"name": rater, "imported": ok, "rejected": rejected}


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

    @property
    def pct_same(self) -> float:
        return 100.0 * self.n_same / self.n_cells if self.n_cells else 0.0


def _pct_changed(cats: dict[int, str], manual: list[str]) -> tuple[int, float]:
    act = [c for c in cats.values() if c != EXCLUDED] + manual
    changed = sum(1 for c in act if c != "normal")
    return len(act), (100.0 * changed / len(act) if act else 0.0)


def compare(folder: Path, grouped, profile: ProfileData, rater_b: str, rater_a: str = "") -> Agreement:
    """My reviews (A) against `rater_b`'s, over every image both have."""
    from .cellpose_runner import load_cached_masks
    from .grouping import UNGROUPED_TIMEPOINT
    a_all, b_all, cells, per_image, groups = [], [], [], [], []
    man_a = man_b = n_images = 0
    for tp in grouped.timepoints():
        if tp == UNGROUPED_TIMEPOINT:
            continue
        for sub in grouped.subjects_in(tp):
            ga: dict[int, str] = {}
            gb: dict[int, str] = {}
            mga: list[str] = []
            mgb: list[str] = []
            n_grp = 0
            for info in grouped.tree[tp][sub]:
                rb = load_rater_review(folder, rater_b, info.path.stem)
                if rb is None:
                    continue
                masks = load_cached_masks(folder, info.path)
                if masks is None:
                    continue
                ra = ReviewState.load(folder, info.path.stem)
                ids, auto = mask_cells(masks, profile)
                ca, cb = categories(ids, auto, ra, profile), categories(ids, auto, rb, profile)
                same = 0
                for cid in ids:
                    a_all.append(ca[cid]); b_all.append(cb[cid])
                    same += ca[cid] == cb[cid]
                    cells.append((info.path.name, cid, ca[cid], cb[cid]))
                default = profile.default_type_key()
                ma = [ra.types.get(str(m.cell_id), default) for m in ra.manual_cells
                      if m.cell_id not in ra.force_excluded]
                mb = [rb.types.get(str(m.cell_id), default) for m in rb.manual_cells
                      if m.cell_id not in rb.force_excluded]
                man_a += len(ma); man_b += len(mb)
                per_image.append((info.path.name, len(ids), same))
                off = len(ga)
                ga.update({off + i: c for i, c in enumerate(ca.values())})
                gb.update({off + i: c for i, c in enumerate(cb.values())})
                mga += ma; mgb += mb
                n_grp += 1
                n_images += 1
            if n_grp:
                na, pa = _pct_changed(ga, mga)
                nb, pb = _pct_changed(gb, mgb)
                groups.append(GroupPair(tp, sub, n_grp, na, nb, pa, pb))
    order = [t.key for t in profile.cell_types]
    cats = [c for c in order if c in set(a_all) | set(b_all)] + \
        sorted((set(a_all) | set(b_all)) - set(order) - {EXCLUDED}) + \
        ([EXCLUDED] if EXCLUDED in set(a_all) | set(b_all) else [])
    idx = {c: i for i, c in enumerate(cats)}
    m = np.zeros((len(cats), len(cats)), dtype=int)
    for x, y in zip(a_all, b_all):
        m[idx[x], idx[y]] += 1
    xa, yb = [g.pct_a for g in groups], [g.pct_b for g in groups]
    return Agreement(rater_a or "A", rater_b, cats, m, len(a_all), int(np.trace(m)) if len(cats) else 0,
                     cohen_kappa(a_all, b_all), groups, icc_2_1(xa, yb), bland_altman(xa, yb),
                     n_images, man_a, man_b, per_image, cells)


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
        w.writerow(["zeitpunkt", "proband", "bilder", f"pct_veraendert_{ag.rater_a}", f"pct_veraendert_{ag.rater_b}"])
        for g in ag.groups:
            w.writerow([g.tp, g.sub, g.n_images, f"{g.pct_a:.2f}", f"{g.pct_b:.2f}"])
        w.writerow([])
        k, klo, khi = ag.kappa
        i, ilo, ihi = ag.icc
        md, lo, hi = ag.ba
        for row in (("zellen_verglichen", ag.n_cells), ("uebereinstimmung_pct", f"{ag.pct_same:.2f}"),
                    ("cohen_kappa", f"{k:.3f}"), ("kappa_ci95", f"{klo:.3f}–{khi:.3f}"),
                    ("icc_2_1_pct_veraendert", f"{i:.3f}"), ("icc_ci95", f"{ilo:.3f}–{ihi:.3f}"),
                    ("bland_altman_mittlere_differenz", f"{md:.3f}"), ("loa_unten", f"{lo:.3f}"),
                    ("loa_oben", f"{hi:.3f}"), ("manuelle_zellen_nur_A", ag.manual_only_a),
                    ("manuelle_zellen_nur_B", ag.manual_only_b)):
            w.writerow(row)
