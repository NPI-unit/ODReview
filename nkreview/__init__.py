"""ODReview — optical density (DAB) of cells and nuclei (NK cells, Tregs, muscle cells) in brightfield images.

Derived from ErysReview @ 3cacb26 (Qt shell, look, installer); the measuring
layer is new: Cellpose on DAB-ROI crops, hybrid OD contour, Beer–Lambert OD
with per-image I₀, Ruifrok–Johnston H-DAB deconvolution.
"""

__version__ = "0.9.0"
