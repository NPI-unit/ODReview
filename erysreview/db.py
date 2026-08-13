"""Consolidated per-study reporting store  ->  outputs/erys.db  (SQLite, stdlib).

Storage-first reporting layer (the interactive `.erys_cache` of masks + review
JSON remains the live source of truth — this module never touches it). On export
we snapshot every group's atoms into one SQLite file:

  * one row per CELL  (type, flags, area, centroid)         -> table `cells`
  * one row per IMAGE (group keys, eval membership, params)  -> table `images`
  * the run + profile snapshot                               -> table `run_meta`
  * the profile's cell-type catalogue                        -> table `cell_types`
  * every metric's plain-language formula (the legend)       -> table `metric_definitions`

Aggregates are NOT stored — they are SQL VIEWS over the atoms, so the view
definition *is* the documented formula and nothing can drift:

  * v_image_summary  – per image: active RBC count
  * v_group_type     – per (timepoint, subject, type): active count + %
  * v_group_summary  – per (timepoint, subject): ΣRBC, %verändert,
                       %verändert_per_target, %Target  (JOINs run_meta.target)

"Snapshot per export": the target_cells in force at export time and each image's
in_eval membership (computed by the procedural cap in evaluation.py) are stored
as data; the views reference run_meta.target_cells — never a hard-coded 2000.

The numbers here are guaranteed to equal group_eval_summary() because both
derive from the same compute_image_state() call (see tests/test_export_db.py).
"""

from __future__ import annotations

import datetime as _dt
import sqlite3
from pathlib import Path

from .grouping import GroupedFolder, ImageGroupInfo, UNGROUPED_TIMEPOINT
from .image_state import compute_image_state
from .profile import ProfileData

# 2: images gained `reviewed` (was the human done with this image?).
SCHEMA_VERSION = 2


# ─────────────────────────────────────────────
# Metric legend (mirrors the SQL view formulas in plain notation)
# ─────────────────────────────────────────────
METRIC_DEFINITIONS: list[tuple[str, str, str, str]] = [
    # (metric_key, label, formula, description)
    ("rbc", "RBC", "Σ aktiv gezählter Zellen je Bild",
     "Aktive Zellen = segmentierte Zellen ohne Rand-/Duplikat-/manuell ausgeschlossene, "
     "plus manuell eingeschlossene."),
    ("pct_type", "%<Typ>", "100 × Σ Typ / ΣRBC",
     "Anteil eines Zelltyps, aggregiert über die Eval-Bilder der Gruppe."),
    ("pct_changed", "%verändert", "100 − %Normal  =  100 × (ΣRBC − ΣNormal) / ΣRBC",
     "Anteil nicht-normaler (veränderter) Erythrozyten in der Gruppe."),
    ("pct_changed_per_target", "%verändert_per_target  (Datei: %verändert_2000)",
     "%verändert × Ziel / ΣRBC",
     "Rückrechnung des Veränderungsanteils auf die Ziel-Zellzahl. In den Legacy-CSV/"
     "Uebersicht-Spalten weiterhin '_2000' genannt; 'Ziel' ist der tatsächliche Wert."),
    ("pct_target", "%Target", "100 × ΣRBC / Ziel",
     "Wie weit die gezählte Zellzahl die Ziel-Zellzahl erreicht."),
    ("in_eval", "Eval-Bild", "Auto-Füllung in Bildreihenfolge bis ΣRBC ≥ Ziel",
     "Bilder, die zur Auswertung zählen; manuelle Overrides (eval_include) gewinnen."),
    ("target", "Ziel", "profile.target_cells zum Export-Zeitpunkt",
     "Die Ziel-Zellzahl ist pro Profil einstellbar; hier als Snapshot gespeichert."),
]


# ─────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────
_DDL_TABLES = """
CREATE TABLE IF NOT EXISTS run_meta (
    id                  INTEGER PRIMARY KEY CHECK (id = 1),
    schema_version      INTEGER,
    app_version         TEXT,
    exported_at         TEXT,
    source_folder       TEXT,
    profile_name        TEXT,
    target_cells        INTEGER,
    model               TEXT,
    diameter            REAL,
    flow_threshold      REAL,
    cellprob_threshold  REAL
);

CREATE TABLE IF NOT EXISTS cell_types (
    key         TEXT PRIMARY KEY,
    name        TEXT,
    color       TEXT,
    is_normal   INTEGER,
    sort_order  INTEGER
);

CREATE TABLE IF NOT EXISTS images (
    stem               TEXT PRIMARY KEY,
    timepoint          TEXT,
    subject            TEXT,
    image_num          INTEGER,
    in_eval            INTEGER,
    eval_override      TEXT,          -- 'in' / 'out' / NULL (= auto)
    n_active           INTEGER,       -- denormalized control (= Σ active cells)
    diameter           REAL,
    flow_threshold     REAL,
    cellprob_threshold REAL,
    model              TEXT,
    cellpose_version   TEXT,
    reviewed           INTEGER,       -- image marked "fertig" by a human (ReviewState.is_finished)
    source_mtime       REAL,          -- max(mask, review) mtime for incremental upsert
    exported_at        TEXT
);

CREATE TABLE IF NOT EXISTS cells (
    stem               TEXT,
    cell_id            INTEGER,
    type_key           TEXT,          -- raw label (matches *_stats.csv 'type')
    type_counted_as    TEXT,          -- bucket used by by_type (raw, or default if type deleted)
    label_source       TEXT,          -- 'reviewer' / 'auto'
    is_active          INTEGER,
    is_border          INTEGER,
    is_duplicate       INTEGER,
    is_force_included  INTEGER,
    is_force_excluded  INTEGER,
    is_manual          INTEGER,
    area               REAL,
    centroid_x         REAL,
    centroid_y         REAL,
    PRIMARY KEY (stem, cell_id)
);

CREATE TABLE IF NOT EXISTS metric_definitions (
    metric_key   TEXT PRIMARY KEY,
    label        TEXT,
    formula      TEXT,
    description  TEXT
);

CREATE INDEX IF NOT EXISTS idx_cells_stem    ON cells(stem);
CREATE INDEX IF NOT EXISTS idx_cells_active  ON cells(stem, is_active);
CREATE INDEX IF NOT EXISTS idx_images_group  ON images(timepoint, subject);
"""

# Views recreated every run so their definitions track the code.
_DDL_VIEWS = """
DROP VIEW IF EXISTS v_image_summary;
DROP VIEW IF EXISTS v_group_type;
DROP VIEW IF EXISTS v_group_summary;

CREATE VIEW v_image_summary AS
SELECT im.stem, im.timepoint, im.subject, im.image_num, im.in_eval,
       (SELECT COUNT(*) FROM cells c WHERE c.stem = im.stem AND c.is_active = 1) AS rbc
FROM images im;

CREATE VIEW v_group_summary AS
WITH groups AS (
    SELECT DISTINCT timepoint, subject FROM images WHERE in_eval = 1
),
img AS (
    SELECT timepoint, subject, COUNT(*) AS n_images
    FROM images WHERE in_eval = 1
    GROUP BY timepoint, subject
),
act AS (
    SELECT im.timepoint, im.subject,
           COUNT(*) AS sum_rbc,
           SUM(CASE WHEN c.type_counted_as = 'normal' THEN 1 ELSE 0 END) AS sum_normal
    FROM images im JOIN cells c ON c.stem = im.stem
    WHERE im.in_eval = 1 AND c.is_active = 1
    GROUP BY im.timepoint, im.subject
)
SELECT
    g.timepoint,
    g.subject,
    COALESCE(img.n_images, 0)  AS n_images,
    COALESCE(act.sum_rbc, 0)   AS sum_rbc,
    COALESCE(act.sum_normal,0) AS sum_normal,
    (SELECT target_cells FROM run_meta LIMIT 1) AS target,
    CASE WHEN COALESCE(act.sum_rbc, 0) > 0
         THEN 100.0 * (act.sum_rbc - act.sum_normal) / act.sum_rbc
         ELSE 0.0 END AS pct_changed,
    CASE WHEN COALESCE(act.sum_rbc, 0) > 0
         THEN (100.0 * (act.sum_rbc - act.sum_normal) / act.sum_rbc)
              * (SELECT target_cells FROM run_meta LIMIT 1) / act.sum_rbc
         ELSE 0.0 END AS pct_changed_per_target,
    CASE WHEN (SELECT target_cells FROM run_meta LIMIT 1) > 0
         THEN 100.0 * COALESCE(act.sum_rbc, 0) / (SELECT target_cells FROM run_meta LIMIT 1)
         ELSE 0.0 END AS pct_target
FROM groups g
LEFT JOIN img ON img.timepoint = g.timepoint AND img.subject = g.subject
LEFT JOIN act ON act.timepoint = g.timepoint AND act.subject = g.subject;

CREATE VIEW v_group_type AS
SELECT im.timepoint, im.subject, c.type_counted_as AS type_key,
       COUNT(*) AS n,
       CASE WHEN gs.sum_rbc > 0 THEN 100.0 * COUNT(*) / gs.sum_rbc ELSE 0.0 END AS pct
FROM images im
JOIN cells c ON c.stem = im.stem
JOIN v_group_summary gs ON gs.timepoint = im.timepoint AND gs.subject = im.subject
WHERE im.in_eval = 1 AND c.is_active = 1
GROUP BY im.timepoint, im.subject, c.type_counted_as;
"""


def _connect(dest: Path) -> sqlite3.Connection:
    """Open the store, rebuilding it from scratch if it predates SCHEMA_VERSION.

    The DDL is all CREATE TABLE IF NOT EXISTS, so an older file would keep its
    old columns and the next INSERT would fail on the new one. Dropping is safe:
    this database is a derived snapshot, regenerated in full from .erys_cache on
    every export — no original data lives here.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        try:
            probe = sqlite3.connect(str(dest))
            row = probe.execute(
                "SELECT schema_version FROM run_meta WHERE id = 1").fetchone()
            probe.close()
            stale = row is None or int(row[0]) < SCHEMA_VERSION
        except sqlite3.Error:
            stale = True          # unreadable or no run_meta yet -> rebuild
        if stale:
            print(f"[db] {dest.name}: older schema, rebuilding", flush=True)
            dest.unlink()
    conn = sqlite3.connect(str(dest))
    conn.row_factory = sqlite3.Row
    conn.executescript(_DDL_TABLES)
    return conn


# ─────────────────────────────────────────────
# Build / upsert
# ─────────────────────────────────────────────
def _source_mtime(folder: Path, stem: str, image_path: Path) -> float:
    from .cellpose_runner import mask_read_paths
    from .persistence import ReviewState
    npy, _ = mask_read_paths(folder, image_path)
    m = npy.stat().st_mtime if (npy and npy.exists()) else 0.0
    rev = ReviewState.review_path(folder, stem)
    r = rev.stat().st_mtime if rev.exists() else 0.0
    return max(m, r)


def _cell_rows(state, stem: str, profile: ProfileData) -> list[tuple]:
    """Build cells-table rows from one ImageState, mirroring compute_image_state's
    by_type bucketing so SQL aggregates equal group_eval_summary exactly."""
    default_key = profile.default_type_key()
    type_keys = {t.key for t in profile.cell_types}
    rows: list[tuple] = []
    for cid, info in state.cell_infos.items():
        raw = state.review.types.get(str(cid), default_key)
        counted_as = raw if raw in type_keys else default_key
        rows.append((
            stem, int(cid), raw, counted_as,
            "reviewer" if str(cid) in state.review.types else "auto",
            1 if cid in state.active_ids else 0,
            1 if cid in state.border_ids else 0,
            1 if cid in state.duplicate_ids else 0,
            1 if cid in state.included else 0,
            1 if cid in state.excluded else 0,
            1 if info.is_manual else 0,
            float(info.area),
            round(float(info.cx), 2), round(float(info.cy), 2),
        ))
    return rows


def _eval_override(folder: Path, stem: str) -> str | None:
    from .persistence import ReviewState
    v = ReviewState.load(folder, stem).eval_include
    return None if v is None else ("in" if v else "out")


def write_db(folder: Path, profile: ProfileData, grouped: GroupedFolder,
             dest: Path) -> Path | None:
    """Snapshot the whole study into `dest`. Incremental per image (skips images
    whose mask+review are unchanged since the last write). Returns dest, or None
    if there is nothing grouped to write."""
    from . import __version__
    from .cellpose_runner import cached_params, has_cache
    from .evaluation import effective_eval_stems
    from .persistence import ReviewState

    if grouped is None or not grouped.tree:
        return None

    conn = _connect(dest)
    try:
        now = _dt.datetime.now().isoformat(timespec="seconds")

        # run_meta + cell_types + legend: replaced wholesale each export.
        cp = profile.cellpose
        conn.execute("DELETE FROM run_meta")
        conn.execute(
            "INSERT INTO run_meta (id, schema_version, app_version, exported_at, "
            "source_folder, profile_name, target_cells, model, diameter, "
            "flow_threshold, cellprob_threshold) VALUES (1,?,?,?,?,?,?,?,?,?,?)",
            (SCHEMA_VERSION, __version__, now, str(folder), profile.name,
             int(max(1, profile.target_cells)), cp.model, float(cp.diameter),
             float(cp.flow_threshold), float(cp.cellprob_threshold)),
        )
        conn.execute("DELETE FROM cell_types")
        conn.executemany(
            "INSERT INTO cell_types (key, name, color, is_normal, sort_order) "
            "VALUES (?,?,?,?,?)",
            [(t.key, (t.name or t.key), getattr(t, "color", None),
              1 if t.key == "normal" else 0, i)
             for i, t in enumerate(profile.cell_types)],
        )
        conn.execute("DELETE FROM metric_definitions")
        conn.executemany(
            "INSERT INTO metric_definitions (metric_key, label, formula, description) "
            "VALUES (?,?,?,?)", METRIC_DEFINITIONS,
        )

        seen_stems: set[str] = set()
        for tp in grouped.timepoints():
            for sub in grouped.subjects_in(tp):
                images = grouped.tree[tp][sub]
                eval_stems = effective_eval_stems(folder, images, profile)
                for info in images:
                    if not has_cache(folder, info.path):
                        continue
                    stem = info.path.stem
                    seen_stems.add(stem)
                    src_mtime = _source_mtime(folder, stem, info.path)

                    # Incremental: skip if this image is already current in the DB.
                    row = conn.execute(
                        "SELECT source_mtime FROM images WHERE stem = ?", (stem,)
                    ).fetchone()
                    if row is not None and row["source_mtime"] is not None \
                            and float(row["source_mtime"]) >= src_mtime:
                        # still update group keys / in_eval (cheap, may have changed)
                        conn.execute(
                            "UPDATE images SET timepoint=?, subject=?, image_num=?, "
                            "in_eval=?, eval_override=? WHERE stem=?",
                            (tp, sub, info.image_num,
                             1 if stem in eval_stems else 0,
                             _eval_override(folder, stem), stem),
                        )
                        continue

                    state = compute_image_state(folder, info, profile)
                    if state is None:
                        continue
                    params = cached_params(folder, info.path) or {}

                    conn.execute("DELETE FROM cells WHERE stem = ?", (stem,))
                    conn.execute("DELETE FROM images WHERE stem = ?", (stem,))
                    conn.execute(
                        "INSERT INTO images (stem, timepoint, subject, image_num, "
                        "in_eval, eval_override, n_active, diameter, flow_threshold, "
                        "cellprob_threshold, model, cellpose_version, reviewed, "
                        "source_mtime, exported_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (stem, tp, sub, info.image_num,
                         1 if stem in eval_stems else 0,
                         _eval_override(folder, stem), int(state.n_active),
                         params.get("diameter"), params.get("flow_threshold"),
                         params.get("cellprob_threshold"), params.get("model"),
                         params.get("cellpose_version"),
                         1 if ReviewState.load(folder, stem).is_finished else 0,
                         src_mtime, now),
                    )
                    conn.executemany(
                        "INSERT INTO cells (stem, cell_id, type_key, type_counted_as, "
                        "label_source, is_active, is_border, is_duplicate, "
                        "is_force_included, is_force_excluded, is_manual, area, "
                        "centroid_x, centroid_y) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        _cell_rows(state, stem, profile),
                    )

        # Prune images (and their cells) no longer present in the folder.
        if seen_stems:
            placeholders = ",".join("?" * len(seen_stems))
            args = tuple(seen_stems)
            conn.execute(f"DELETE FROM cells WHERE stem NOT IN ({placeholders})", args)
            conn.execute(f"DELETE FROM images WHERE stem NOT IN ({placeholders})", args)

        conn.executescript(_DDL_VIEWS)
        conn.commit()
    finally:
        conn.close()
    return dest
