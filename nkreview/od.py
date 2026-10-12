"""Optical density: background I₀, Beer–Lambert OD and colour deconvolution.

All OD values are decadic: OD = −log₁₀(I / I₀), per RGB channel, with I₀ the
image's own background (no step tablet — I₀ makes it a true transmission OD).
Stain separation is Ruifrok–Johnston on that OD vector: c = OD_rgb · M⁻¹,
with M the unit stain vectors (rows). Same maths as Fiji's Colour
Deconvolution "H DAB", without its 8-bit rounding.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage as ndi

# Fiji / Ruifrok & Johnston 2001 "H DAB". The third row is filled with the
# orthogonal complement (what Fiji does when only two stains are given).
STAIN_PRESETS: dict[str, list[list[float]]] = {
    "H DAB": [[0.650, 0.704, 0.286],
              [0.268, 0.570, 0.776],
              [0.0, 0.0, 0.0]],
}
DEFAULT_PRESET = "H DAB"
# Channel names as they appear in the export. The counterstain is unknown
# (maybe none), so channel 1 is reported neutrally as the "H" channel.
CHANNELS = ("h", "dab", "rest")
DAB = 1


def stain_matrix(vectors: list[list[float]]) -> np.ndarray:
    M = np.array(vectors, dtype=float)
    for i in range(3):
        n = np.linalg.norm(M[i])
        if n > 1e-9:
            M[i] /= n
    if np.linalg.norm(M[2]) < 1e-9:
        c = np.cross(M[0], M[1])
        M[2] = c / np.linalg.norm(c)
    return M


def od_rgb(img: np.ndarray, i0: np.ndarray) -> np.ndarray:
    """HxWx3 float32 OD per channel. Not clipped at 0: background noise then
    averages to ~0 inside a cell instead of biasing the mean upward."""
    rgb = np.clip(img[..., :3].astype(np.float32), 1.0, None)
    return (-np.log10(rgb / np.asarray(i0, dtype=np.float32))).astype(np.float32)


def deconvolve(od: np.ndarray, vectors: list[list[float]]) -> np.ndarray:
    """HxWx3 OD -> HxWx3 stain concentrations (OD units), order = CHANNELS."""
    inv = np.linalg.inv(stain_matrix(vectors)).astype(np.float32)
    return od @ inv


def total_od(od: np.ndarray) -> np.ndarray:
    return od.mean(axis=-1)


@dataclass
class Background:
    i0: tuple[float, float, float]
    source: str                     # "auto" | "roi"


def estimate_background(img: np.ndarray, cell_mask: np.ndarray | None,
                        roi: tuple[int, int, int, int] | None = None,
                        margin: int = 15) -> Background:
    """I₀ per channel: median of the ROI if given, else of every pixel that is
    not within `margin` px of a cell."""
    rgb = img[..., :3]
    if roi is not None:
        x0, y0, x1, y1 = (int(v) for v in roi)
        x0, x1 = sorted((x0, x1)); y0, y1 = sorted((y0, y1))
        patch = rgb[max(y0, 0):y1 + 1, max(x0, 0):x1 + 1].reshape(-1, 3)
        if len(patch) >= 25:
            return Background(tuple(float(v) for v in np.median(patch, axis=0)), "roi")
    if cell_mask is not None and cell_mask.any():
        keep = ~ndi.binary_dilation(cell_mask, iterations=margin)
        pix = rgb[keep]
        if len(pix) < 1000:
            pix = rgb.reshape(-1, 3)
    else:
        pix = rgb.reshape(-1, 3)
    # Subsample: the median of ~2 M pixels is stable at 1/16 of them.
    return Background(tuple(float(v) for v in np.median(pix[::16], axis=0)), "auto")


def smoothed_total_od(img: np.ndarray, i0, sigma: float = 2.0) -> np.ndarray:
    """Total OD, lightly blurred — only for drawing contours, never for numbers."""
    return ndi.gaussian_filter(total_od(od_rgb(img, i0)), sigma)
