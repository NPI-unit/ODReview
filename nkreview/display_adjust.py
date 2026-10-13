"""Brightness / contrast / gamma for the DISPLAY of an image (taken over from
ErysReview). Display only: the image file, Cellpose's input, the
masks, every OD value and the export never see it. Stored per image in its
review file (`display`).

Values: brightness −100…100, contrast 50…800 (%), gamma 0.40…2.50.
"""

from __future__ import annotations

import numpy as np

DEFAULT = {"brightness": 0, "contrast": 100, "gamma": 1.0}
LIMITS = {"brightness": (-100, 100), "contrast": (50, 800), "gamma": (0.4, 2.5)}


def clean(d: dict | None) -> dict | None:
    """Valid values, clamped; None for 'nothing changed'."""
    if not isinstance(d, dict):
        return None
    out = {}
    for k, dv in DEFAULT.items():
        try:
            v = float(d.get(k, dv))
        except (TypeError, ValueError):
            v = dv
        lo, hi = LIMITS[k]
        v = max(lo, min(hi, v))
        out[k] = round(v, 2) if k == "gamma" else int(round(v))
    return None if out == DEFAULT else out


def apply(norm: np.ndarray, d: dict | None) -> np.ndarray:
    """`norm`: an image already scaled to 0…1.
    Gamma, then brightness (a shift of ±0.5), then contrast around mid-grey —
    brightness before contrast, so an over-exposed image can be shifted down
    to mid-grey first and then stretched."""
    d = clean(d)
    if d is None:
        return norm
    x = np.power(np.clip(norm, 0.0, 1.0), d["gamma"], dtype=np.float32)
    x = (x + d["brightness"] / 200.0 - 0.5) * (d["contrast"] / 100.0) + 0.5
    return np.clip(x, 0.0, 1.0)


def auto(norm: np.ndarray) -> dict:
    """Stretch the 1st…99th percentile to the full range (gamma 1),
    as far as the limits allow."""
    sample = norm[::4, ::4] if norm.ndim == 2 else norm[::4, ::4].mean(axis=-1)
    lo, hi = (float(v) for v in np.percentile(sample, (1, 99)))
    if hi - lo < 1e-4:
        return dict(DEFAULT)
    return clean({"brightness": (0.5 - (lo + hi) / 2) * 200, "contrast": 100 / (hi - lo), "gamma": 1.0}) \
        or dict(DEFAULT)
