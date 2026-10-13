"""Content of the help window: the pipeline steps and every formula.

Each formula has `tex` (exact LaTeX, what "Copy LaTeX" puts on the clipboard)
and `mt` (the same in matplotlib mathtext, which draws it offline in the app).
Kept in one place with the web version (nkreview/help/pipeline.html).
"""

from __future__ import annotations

FORMULAS: dict[str, dict] = {
    "gray": {
        "tex": r"G(p) = \operatorname{round}\!\left(\frac{R(p)+G(p)+B(p)}{3}\right)",
        "mt": r"$G(p) = \mathrm{round}\left(\dfrac{R(p)+G(p)+B(p)}{3}\right)$",
        "cap": "Grey value per pixel, ImageJ default (weighted: 0.299 R + 0.587 G + 0.114 B)."},
    "i0": {
        "tex": r"I_0 = \frac{1}{3}\sum_{k=1}^{3} \bar G_{F_k},\qquad \bar G_{F_k} = \frac{1}{s^2}\sum_{p \in F_k} G(p)",
        "mt": r"$I_0 = \dfrac{1}{3}\sum_{k=1}^{3} \bar{G}_{F_k},\qquad \bar{G}_{F_k} = \dfrac{1}{s^2}\sum_{p \in F_k} G(p)$",
        "cap": "Three random s × s background fields next to the cell, seeded per image and cell."},
    "ring": {
        "tex": r"F_k \subset \{\, p : d_{\mathrm{in}} \le d(p) \le d_{\mathrm{out}},\ p \notin \text{cells},\ \widetilde{\mathrm{OD}}_{\mathrm{tot}}(p) < 0.05 \,\}",
        "mt": r"$F_k \subset \{\, p : d_{in} \leq d(p) \leq d_{out},\ p \notin \mathrm{cells},\ \widetilde{OD}_{tot}(p) < 0.05 \,\}$",
        "cap": "Fields lie in a ring around the contour, off every cell and off stained debris."},
    "valAbs": {
        "tex": r"210 \le I_0 \le 220",
        "mt": r"$210 \leq I_0 \leq 220$",
        "cap": "Fixed validity rule (NK, method section). Otherwise the cell is excluded, not deleted."},
    "valRel": {
        "tex": r"\frac{\lvert I_0 - I_0^{\mathrm{bg}} \rvert}{I_0^{\mathrm{bg}}} \le 3\,\%",
        "mt": r"$\dfrac{|I_0 - I_0^{bg}|}{I_0^{bg}} \leq 3\,\%$",
        "cap": "Relative rule for series with different exposure (C2C12 template)."},
    "od": {
        "tex": r"\mathrm{OD} = -\log_{10}\!\left(\frac{\bar I}{I_0}\right),\qquad \bar I = \frac{1}{\lvert C\rvert}\sum_{p\in C} G(p)",
        "mt": r"$\mathrm{OD} = -\log_{10}\left(\dfrac{\bar{I}}{I_0}\right),\qquad \bar{I} = \dfrac{1}{|C|}\sum_{p\in C} G(p)$",
        "cap": "Main value: Beer–Lambert OD of the mean grey value, as Fiji computes it."},
    "pix": {
        "tex": r"\mathrm{OD}(p) = -\log_{10}\!\left(\frac{G(p)}{I_0}\right),\qquad \overline{\mathrm{OD}(p)} \ \ge\ \mathrm{OD}",
        "mt": r"$\mathrm{OD}(p) = -\log_{10}\left(\dfrac{G(p)}{I_0}\right),\qquad \overline{\mathrm{OD}(p)} \geq \mathrm{OD}$",
        "cap": "Per-pixel OD for the distribution (mean, SD, median, p5, p95). The mean of pixel ODs is never below the OD of the mean (Jensen)."},
    "radial": {
        "tex": r"r(p) = 1 - \frac{\delta(p)}{\max_{q\in C}\delta(q)},\qquad P_j = \operatorname*{mean}_{\,p\,:\,\frac{j-1}{5} \le r(p) < \frac{j}{5}} \mathrm{OD}(p)",
        "mt": r"$r(p) = 1 - \dfrac{\delta(p)}{\max_{q\in C}\,\delta(q)},\qquad P_j = \mathrm{mean}\{\mathrm{OD}(p) : \dfrac{j-1}{5} \leq r(p) < \dfrac{j}{5}\}$",
        "cap": "Radial profile in 5 rings, δ(p) distance to the cell edge: r = 0 centre, r = 1 membrane."},
    "rgbod": {
        "tex": r"\mathrm{OD}_c(p) = -\log_{10}\!\left(\frac{I_c(p)}{I_{0,c}}\right),\quad c \in \{R,G,B\}",
        "mt": r"$\mathrm{OD}_c(p) = -\log_{10}\left(\dfrac{I_c(p)}{I_{0,c}}\right),\quad c \in \{R,G,B\}$",
        "cap": "Per-channel OD with the image background as I₀ (pre-filter, contour, DAB extra column)."},
    "deconv": {
        "tex": r"\begin{pmatrix} c_{\mathrm{H}} & c_{\mathrm{DAB}} & c_{\mathrm{res}} \end{pmatrix} = \begin{pmatrix} \mathrm{OD}_R & \mathrm{OD}_G & \mathrm{OD}_B \end{pmatrix} M^{-1},\ M = \begin{pmatrix} m_{\mathrm{H}} \\ m_{\mathrm{DAB}} \\ m_{\mathrm{H}} \times m_{\mathrm{DAB}} \end{pmatrix},\ m_{\mathrm{H}} = (0.650,\,0.704,\,0.286),\ m_{\mathrm{DAB}} = (0.268,\,0.570,\,0.776)",
        "mt": r"$(c_H,\ c_{DAB},\ c_{res}) = (\mathrm{OD}_R,\ \mathrm{OD}_G,\ \mathrm{OD}_B)\,M^{-1},\quad m_H = (0.650, 0.704, 0.286),\ m_{DAB} = (0.268, 0.570, 0.776)$",
        "cap": "Ruifrok–Johnston colour deconvolution, Fiji “H DAB” vectors as rows of M (normalised); the third row is m_H × m_DAB."},
    "tot": {
        "tex": r"\mathrm{OD}_{\mathrm{tot}}(p) = \tfrac{1}{3}\left(\mathrm{OD}_R + \mathrm{OD}_G + \mathrm{OD}_B\right),\quad \widetilde{\mathrm{OD}}_{\mathrm{tot}} = \mathrm{OD}_{\mathrm{tot}} * \mathcal{G}_{\sigma=2}",
        "mt": r"$\mathrm{OD}_{tot}(p) = \dfrac{1}{3}(\mathrm{OD}_R + \mathrm{OD}_G + \mathrm{OD}_B),\quad \widetilde{\mathrm{OD}}_{tot} = \mathrm{OD}_{tot} \ast \mathcal{G}_{\sigma=2}$",
        "cap": "Total OD, Gaussian-smoothed for drawing contours only (never for numbers)."},
    "prefilter": {
        "tex": r"R = \{\, p : (c_{\mathrm{DAB}} * \mathcal{G}_{\sigma=3})(p) > 0.04 \,\},\quad \lvert R_i \rvert \ge 150\ \mathrm{px}",
        "mt": r"$R = \{\, p : (c_{DAB} \ast \mathcal{G}_{\sigma=3})(p) > 0.04 \,\},\quad |R_i| \geq 150\ \mathrm{px}$",
        "cap": "DAB-positive regions; Cellpose runs on padded crops around them (sides multiples of 32)."},
    "hybrid": {
        "tex": r"C_{\mathrm{hyb}} = \operatorname{fill}\!\Big(\big\{\, p : \widetilde{\mathrm{OD}}_{\mathrm{tot}}(p) > 0.05,\ d(p, C_{\mathrm{CP}}) \le 10 \,\big\}_{\text{connected to } C_{\mathrm{CP}}}\Big)",
        "mt": r"$C_{hyb} = \mathrm{fill}(\{\, p : \widetilde{\mathrm{OD}}_{tot}(p) > 0.05,\ d(p, C_{CP}) \leq 10 \,\})$",
        "cap": "Hybrid contour (whole cell): Cellpose finds the cell, the edge comes from the OD (parts connected to the Cellpose cell)."},
    "minarea": {
        "tex": r"\lvert C \rvert \ge 500\ \mathrm{px}",
        "mt": r"$|C| \geq 500\ \mathrm{px}$",
        "cap": "Nucleus template: smaller Cellpose objects are dropped."},
    "pos": {
        "tex": r"\text{positive} \iff \mathrm{OD} > \tau,\qquad \tau = 0.01",
        "mt": r"$\mathrm{positive} \Leftrightarrow \mathrm{OD} > \tau,\qquad \tau = 0.01$",
        "cap": "Above the noise floor (99.9 % of background pseudo-nuclei < 0.006). Not a biological cut-off."},
    "debris": {
        "tex": r"\text{debris flag} \iff \bar c_{\mathrm{H}} \ge 0.04",
        "mt": r"$\mathrm{debris\ flag} \Leftrightarrow \bar{c}_H \geq 0.04$",
        "cap": "Grey-green absorbers (crystals, clumps) load the H channel; nucleus studies only, flag not filter."},
    "share": {
        "tex": r"\text{share}_{+} = \frac{n_{+}}{n_{\text{kept}}},\qquad \overline{\mathrm{OD}}_{\text{subject}} = \frac{1}{n_{\text{kept}}}\sum_{i} \mathrm{OD}_i",
        "mt": r"$\mathrm{share}_{+} = \dfrac{n_{+}}{n_{kept}},\qquad \overline{\mathrm{OD}}_{subject} = \dfrac{1}{n_{kept}}\sum_{i} \mathrm{OD}_i$",
        "cap": "Summary per subject × group × timepoint × marker, kept cells only."},
}

SHEET = ["gray", "i0", "ring", "od", "valAbs", "valRel", "pix", "radial", "rgbod", "deconv", "tot",
         "prefilter", "hybrid", "minarea", "pos", "debris", "share"]

STEPS = [
    {"id": "import", "phase": "Read in", "t": "Import", "s": "Study, folders, roles",
     "body": "A study collects one or more image folders. Each file name is split into parts and every part "
             "gets a role: subject, group, timepoint, cell type, marker, date, image number. Dates such as "
             "2026-08-27 stay one part. Templates pre-fill the scheme and all settings.",
     "params": [("Delimiters", "space, _ , -"), ("Roles", "subject … image no."), ("Dates", "→ YYYY-MM-DD")],
     "f": []},
    {"id": "grey", "phase": "Read in", "t": "Grey value", "s": "RGB → 8-bit grey",
     "body": "Every pixel is turned into a grey value exactly as ImageJ converts RGB to 8-bit. "
             "All OD values of the method come from this grey image.",
     "params": [("Conversion", "(R+G+B)/3, rounded"), ("Alternative", "weighted 0.299/0.587/0.114")],
     "f": ["gray"]},
    {"id": "pre", "phase": "Processing", "t": "DAB pre-filter", "s": "Where is stain?",
     "body": "Colour deconvolution separates the DAB signal. Only regions with DAB are cut out and sent to "
             "Cellpose – unstained debris never reaches it, and a sparse image takes seconds instead of a "
             "minute. Dense images run as a whole.",
     "params": [("DAB threshold", "0.04"), ("Min. region", "150 px"), ("Crop", "+48 px, ≥ 256 px, multiple of 32")],
     "f": ["rgbod", "deconv", "prefilter"]},
    {"id": "seg", "phase": "Processing", "t": "Segmentation", "s": "", "body": "", "params": [], "f": []},
    {"id": "i0", "phase": "Processing", "t": "Background I₀", "s": "3 fields next to each cell",
     "body": "For every cell, three background fields are drawn at random in a ring around the contour, away "
             "from other cells and stained debris. Their mean is I₀. The draw is seeded, so it is "
             "reproducible; fields can be moved by hand (tool 3) or redrawn (R).",
     "params": [], "f": ["i0", "ring"]},
    {"id": "od", "phase": "Measurement", "t": "Optical density", "s": "OD of the mean grey value",
     "body": "The mean grey value inside the contour is compared with I₀. A cell whose I₀ fails the validity "
             "rule stays in the table as excluded – nothing disappears silently.",
     "params": [], "f": ["od"]},
    {"id": "dist", "phase": "Measurement", "t": "Distribution", "s": "Inside each cell",
     "body": "Each pixel gets its own OD against the cell's I₀. From these: mean, SD, median, p5, p95, share "
             "above 0.3, a histogram and a radial profile from centre to membrane. DAB OD and total OD are "
             "extra columns.",
     "params": [("Profile rings", "5"), ("Share above", "OD 0.3"), ("Extra", "DAB OD, total OD, ring-I₀ check")],
     "f": ["pix", "radial", "tot"]},
    {"id": "export", "phase": "Output", "t": "Review & export", "s": "Check, then tables",
     "body": "In the review you discard cells, draw missing ones (e.g. Ki67-negative nuclei), move I₀ fields "
             "and mark images as reviewed. The export writes cells.csv (one row per cell), summary.csv, "
             "settings.json, overlay PNGs and an Excel file.",
     "params": [("cells.csv", "subject, group, od, i0, status …"), ("summary.csv", "n, OD mean/SD/median, share positive"),
                ("Exported", "reviewed images (default)")],
     "f": ["share"]},
]

TARGETS = {
    "cell": {
        "label": "Whole cell · NK",
        "seg": {"s": "Cellpose on RGB + OD edge",
                "body": "Cellpose-SAM finds and separates the cells on the colour image. The edge is then redrawn "
                        "from the total OD, so the strongly stained rim Cellpose tends to cut off is included, "
                        "while halo and debris stay outside.",
                "params": [("Cellpose", "cpsam, diameter 30"), ("Edge threshold", "OD_tot 0.05"), ("Grow", "≤ 10 px")],
                "f": ["hybrid"]},
        "i0": [("Fields", "3 × (9 × 9 px)"), ("Ring", "14–40 px"), ("Valid", "I₀ 210–220")],
        "od": [("Ī", "mean grey value in C"), ("Rule", "fixed 210–220"), ("Positive", "off")],
        "od_f": ["valAbs"],
    },
    "nuc": {
        "label": "Nucleus · C2C12",
        "seg": {"s": "Cellpose on the DAB map",
                "body": "Pale Ki67 nuclei are much clearer on the DAB map than on the colour image (27 instead of "
                        "8 nuclei on the pilot), so Cellpose looks at the DAB map and its contour is used as is. "
                        "Tiny objects are dropped; debris is flagged by its high H channel. Unstained nuclei are "
                        "invisible without a counterstain – draw them by hand (tool 2).",
                "params": [("Cellpose sees", "DAB map (OD 0–0.2)"), ("Min. area", "500 px"), ("Debris flag", "H OD ≥ 0.04")],
                "f": ["minarea", "debris"]},
        "i0": [("Fields", "3 × (9 × 9 px)"), ("Ring", "40–90 px"), ("Valid", "± 3 % of free background")],
        "od": [("Ī", "mean grey value in C"), ("Rule", "relative ± 3 %"), ("Positive", "OD > 0.01")],
        "od_f": ["valRel", "pos"],
    },
}


def step(step_id: str, target: str) -> dict:
    base = dict(next(s for s in STEPS if s["id"] == step_id))
    t = TARGETS[target]
    if step_id == "seg":
        base.update(t["seg"])
    elif step_id == "i0":
        base["params"] = t["i0"]
    elif step_id == "od":
        base["params"] = t["od"]
        base["f"] = ["od"] + t["od_f"]
    return base
