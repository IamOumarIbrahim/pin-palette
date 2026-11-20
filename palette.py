"""Palette extraction and comparison.

A palette is a list of dicts: {"lab": [L, a, b], "hex": "#rrggbb", "pct": 0..100}.
Color distance is CIE76 delta-E in Lab space (about 2.3 = just noticeable).
"""
import io

import numpy as np
from PIL import Image

MIN_PCT = 2.0         # drop clusters smaller than this
MERGE_DE = 10.0       # merge clusters closer than this
SIGNIFICANT_PCT = 5.0  # colors at or above this count toward "complexity"

# Small named-color table, used to build a Pinterest search query from a palette.
NAMED = {
    "black": (20, 20, 20), "charcoal": (54, 69, 79), "grey": (128, 128, 128),
    "silver": (192, 192, 192), "white": (245, 245, 245), "cream": (255, 248, 220),
    "beige": (222, 205, 175), "tan": (190, 160, 120), "brown": (120, 80, 50),
    "chocolate": (75, 45, 30), "red": (200, 30, 40), "burgundy": (110, 20, 40),
    "pink": (240, 160, 180), "blush": (230, 190, 185), "coral": (250, 120, 100),
    "orange": (240, 130, 30), "rust": (170, 80, 40), "peach": (250, 200, 160),
    "yellow": (245, 215, 50), "mustard": (210, 170, 50), "gold": (200, 160, 70),
    "olive": (120, 120, 50), "sage green": (160, 175, 140), "green": (50, 140, 60),
    "forest green": (35, 80, 45), "mint": (170, 230, 200), "teal": (30, 120, 120),
    "turquoise": (60, 200, 200), "sky blue": (140, 195, 235), "blue": (40, 90, 200),
    "navy": (25, 35, 80), "lavender": (190, 170, 220), "purple": (110, 50, 150),
    "plum": (90, 40, 70),
}


def _srgb_to_lab(rgb):
    """rgb: (N,3) floats 0..255 -> (N,3) Lab."""
    c = rgb / 255.0
    c = np.where(c > 0.04045, ((c + 0.055) / 1.055) ** 2.4, c / 12.92)
    m = np.array([[0.4124, 0.3576, 0.1805],
                  [0.2126, 0.7152, 0.0722],
                  [0.0193, 0.1192, 0.9505]])
    xyz = c @ m.T / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    L = 116 * f[:, 1] - 16
    a = 500 * (f[:, 0] - f[:, 1])
    b = 200 * (f[:, 1] - f[:, 2])
    return np.stack([L, a, b], axis=1)


_NAMED_LAB = _srgb_to_lab(np.array(list(NAMED.values()), dtype=float))
_NAMED_KEYS = list(NAMED.keys())


def _kmeans(x, k, iters=15, seed=0):
    rng = np.random.default_rng(seed)
    # k-means++ init
    centers = [x[rng.integers(len(x))]]
    for _ in range(1, k):
        d = np.min(((x[:, None, :] - np.array(centers)[None]) ** 2).sum(-1), axis=1)
        if d.sum() == 0:
            break
        centers.append(x[rng.choice(len(x), p=d / d.sum())])
    centers = np.array(centers)
    for _ in range(iters):
        labels = np.argmin(((x[:, None, :] - centers[None]) ** 2).sum(-1), axis=1)
        new = np.array([x[labels == i].mean(0) if np.any(labels == i) else centers[i]
                        for i in range(len(centers))])
        if np.allclose(new, centers, atol=0.1):
            break
        centers = new
    counts = np.bincount(labels, minlength=len(centers))
    return centers, counts


def extract(data, k=8):
    """data: image bytes. Returns palette sorted by pct desc."""
    img = Image.open(io.BytesIO(data))
    img.draft("RGB", (160, 160))  # fast JPEG decode at reduced size
    img = img.convert("RGB")
    img.thumbnail((96, 96))
    rgb = np.asarray(img, dtype=float).reshape(-1, 3)
    lab = _srgb_to_lab(rgb)
    centers, counts = _kmeans(lab, k)

    # Merge near-duplicate clusters (weighted average).
    clusters = [[c, n] for c, n in zip(centers, counts) if n > 0]
    merged = True
    while merged and len(clusters) > 1:
        merged = False
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                if np.linalg.norm(clusters[i][0] - clusters[j][0]) < MERGE_DE:
                    ci, ni = clusters[i]
                    cj, nj = clusters[j]
                    clusters[i] = [(ci * ni + cj * nj) / (ni + nj), ni + nj]
                    clusters.pop(j)
                    merged = True
                    break
            if merged:
                break

    total = sum(n for _, n in clusters)
    out = []
    for c, n in clusters:
        pct = 100.0 * n / total
        if pct < MIN_PCT:
            continue
        out.append({"lab": [round(float(v), 2) for v in c], "hex": _lab_to_hex(c), "pct": pct})
    # renormalise after dropping tiny clusters
    s = sum(p["pct"] for p in out)
    for p in out:
        p["pct"] = round(float(p["pct"] * 100.0 / s), 2)
    out.sort(key=lambda p: -p["pct"])
    return out


def _lab_to_hex(lab):
    L, a, b = lab
    fy = (L + 16) / 116
    fx, fz = fy + a / 500, fy - b / 200
    def inv(t):
        return t ** 3 if t ** 3 > 0.008856 else (t - 16 / 116) / 7.787
    xyz = np.array([inv(fx) * 0.95047, inv(fy), inv(fz) * 1.08883])
    m = np.array([[3.2406, -1.5372, -0.4986],
                  [-0.9689, 1.8758, 0.0415],
                  [0.0557, -0.2040, 1.0570]])
    c = m @ xyz
    c = np.where(c > 0.0031308, 1.055 * np.clip(c, 0, None) ** (1 / 2.4) - 0.055, 12.92 * c)
    c = np.clip(np.round(c * 255), 0, 255).astype(int)
    return "#%02x%02x%02x" % tuple(c)


def complexity(pal):
    return sum(1 for p in pal if p["pct"] >= SIGNIFICANT_PCT)


def compare(ref, cand):
    """Return metrics: lower = more similar.

    emd      greedy earth-mover distance in delta-E (average color shift, weighted by %)
    pct_diff worst % mismatch: for each significant ref color, |ref% - share of
             candidate pixels whose nearest ref color is this one (within 25 dE)|
    cx_diff  difference in number of significant colors
    """
    a = np.array([p["lab"] for p in ref])
    b = np.array([p["lab"] for p in cand])
    wa = np.array([p["pct"] for p in ref], dtype=float)
    wb = np.array([p["pct"] for p in cand], dtype=float)
    d = np.linalg.norm(a[:, None, :] - b[None], axis=-1)

    # greedy EMD
    ra, rb = wa.copy(), wb.copy()
    cost = 0.0
    for idx in np.argsort(d, axis=None):
        i, j = divmod(int(idx), d.shape[1])
        f = min(ra[i], rb[j])
        if f <= 0:
            continue
        cost += f * d[i, j]
        ra[i] -= f
        rb[j] -= f
    emd = cost / 100.0

    # Each candidate color counts toward its nearest ref color (if within 25 dE).
    nearest = np.argmin(d, axis=0)
    share = np.zeros(len(ref))
    for j, i in enumerate(nearest):
        if d[i, j] < 25:
            share[i] += wb[j]
    pct_diff = 0.0
    for i, p in enumerate(ref):
        if p["pct"] >= SIGNIFICANT_PCT:
            pct_diff = max(pct_diff, abs(p["pct"] - share[i]))

    return {
        "emd": round(float(emd), 2),
        "pct_diff": round(float(pct_diff), 2),
        "cx_diff": abs(complexity(ref) - complexity(cand)),
    }


def query_words(palettes, n=3):
    """Name the dominant colors across reference palettes, for a search query."""
    weight = {}
    for pal in palettes:
        for p in pal:
            name = _NAMED_KEYS[int(np.argmin(np.linalg.norm(_NAMED_LAB - np.array(p["lab"]), axis=1)))]
            weight[name] = weight.get(name, 0) + p["pct"]
    names = sorted(weight, key=lambda k: -weight[k])[:n]
    return " ".join(names) + " aesthetic"
