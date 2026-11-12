"""Builds the grading looks as 3D LUTs (.cube), working in OKLab / OKLCH.

Why a LUT instead of ffmpeg's curves / colorbalance / huesaturation / vibrance filters:
those act on R, G and B separately, and a per-channel curve pushes bright saturated colors
toward pure R/Y/G/C/B/M (the "notorious six"): a sunset's orange slides to lemon yellow.
Here every change is made on perceptual lightness, chroma and hue separately, and a color
the display can't show loses chroma at its own lightness and hue instead of being clipped
channel by channel, so hues hold.

Grade order follows the colorist rule "primary correction first, creative look second":
  1. levels   - per-clip black/white points, measured from the footage (neutral, bounded)
  2. tone     - the look's contrast curve, applied to lightness only
  3. chroma   - contrast-linked chroma, per-hue gains, vibrance (relative to the gamut edge)
  4. hue      - small per-hue rotations, with near-neutrals and skin protected
  5. split    - cool shadows / warm highlights as small OKLab a/b offsets
  6. gamut    - reduce chroma (never clip channels) until the color fits sRGB / Rec.709

LUT input and output: full-range Rec.709 / sRGB-encoded RGB, which is what the pipeline has
after HDR conversion. Apply with ffmpeg `lut3d=...:interp=tetrahedral`.
"""
import json
import re
from pathlib import Path

import numpy as np

# ---------------------------------------------------------------- the looks
# Every video gets its own look. There are no seasonal defaults: a video that is graded
# uses looks\<video name>.json if one was made for it, otherwise the neutral base below
# (contrast and color strength, no preference for any hue). The looks used on past
# projects are kept in looks\examples\ as starting points, not as defaults.
#
# Look file keys (all optional, missing ones come from the neutral base):
#   curve          contrast curve points [[in, out], ...] on gray code values 0-1
#   shadows_ab     OKLab [a, b] tint added to shadows    (b < 0 cooler, b > 0 warmer)
#   highlights_ab  OKLab [a, b] tint added to highlights
#   hue_shifts     [[hue, half-width, degrees], ...]  turn a hue band (minus = toward red)
#   chroma_gains   [[hue, half-width, gain], ...]     1.2 = that band 20% more colorful
#   vibrance       boosts dull colors more than vivid ones (0 = off)
#   saturation     overall color strength multiplier
#   denoise        true = light noise reduction first (for pushed low-light footage)
#   exposure_keys  steady exposure over time, for auto-exposure changes (see "steady exposure")
#   protect_skin   false = no skin guard, for footage with no people (golden light on rock or
#                  foliage sits in the skin hue band and would otherwise be held back)
#   strength       how much of the look to use: 0.7 = 70% (subtle), 1 = full (default)
# A hue can be a name from HUES or OKLCH degrees. Bands fall off smoothly (raised cosine).
LOOKS_DIR = Path(__file__).resolve().parent / "looks"
HUES = {"red": 29, "orange": 55, "gold": 80, "yellow": 105, "lime": 125, "green": 145, "teal": 175,
        "cyan": 200, "sky": 230, "blue": 260, "purple": 295, "magenta": 330, "pink": 350}
NEUTRAL = {
    "curve": [[0, 0], [0.06, 0.045], [0.25, 0.25], [0.5, 0.56], [0.75, 0.82], [0.92, 0.95], [1, 1]],
    "shadows_ab": [0, 0], "highlights_ab": [0, 0],
    "hue_shifts": [], "chroma_gains": [],
    "vibrance": 0.25, "saturation": 1.10, "denoise": False, "protect_skin": True,
}
BUILTIN = {
    "neutral": NEUTRAL,
    # No change at all; used to check the LUT machinery is lossless.
    "identity": {"curve": [[0, 0], [1, 1]], "shadows_ab": [0, 0], "highlights_ab": [0, 0],
                 "hue_shifts": [], "chroma_gains": [], "vibrance": 0.0, "saturation": 1.0,
                 "contrast_chroma": 0.0},
}


def _hue(h):
    if isinstance(h, str) and not re.fullmatch(r"\d+(\.\d+)?", h):
        if h.lower() not in HUES:
            raise SystemExit(f"Unknown hue '{h}'. Use degrees or one of: {', '.join(HUES)}")
        return float(HUES[h.lower()])
    return float(h)


def load_look(spec):
    """A look by name (looks\\<name>.json, looks\\examples\\<name>.json, or built in) or by
    .json path, completed with the neutral base for any missing keys."""
    if isinstance(spec, dict):
        look = spec
    else:
        candidates = [Path(spec), LOOKS_DIR / f"{spec}.json", LOOKS_DIR / "examples" / f"{spec}.json"]
        path = next((p for p in candidates if p.suffix == ".json" and p.is_file()), None)
        if path:
            look = json.loads(path.read_text(encoding="utf-8"))
        elif spec in BUILTIN:
            look = BUILTIN[spec]
        else:
            raise SystemExit(f"Look '{spec}' not found (looked for {candidates[1]} and {candidates[2]}).")
    full = {**NEUTRAL, **look}
    full["hue_shifts"] = [(_hue(h), float(w), float(d)) for h, w, d in full["hue_shifts"]]
    full["chroma_gains"] = [(_hue(h), float(w), float(g)) for h, w, g in full["chroma_gains"]]
    return full


def scaled(look, s):
    """The same look at strength s (0 = no look, 1 = full, >1 = stronger). The clip's levels
    are not part of the look, so they still apply in full."""
    if s == 1:
        return look
    lerp = lambda v, one: one + s * (v - one)
    return {**look,
            "curve": [[x, lerp(y, x)] for x, y in look["curve"]],
            "shadows_ab": [s * v for v in look["shadows_ab"]],
            "highlights_ab": [s * v for v in look["highlights_ab"]],
            "hue_shifts": [(h, w, s * d) for h, w, d in look["hue_shifts"]],
            "chroma_gains": [(h, w, lerp(g, 1)) for h, w, g in look["chroma_gains"]],
            "vibrance": s * look["vibrance"], "saturation": lerp(look["saturation"], 1),
            "contrast_chroma": s * look.get("contrast_chroma", CONTRAST_CHROMA), "strength": 1}


def adjust(look, hues=None, hue_shift=None, strength=None, width=30.0):
    """Quick per-video changes on top of a look: strength=0.7 (70% of the look; default is
    the look's own "strength", else 1), then hues='orange+20,blue+10' (percent more or less
    colorful) and hue_shift='yellow-8' (degrees; minus = toward red/orange), at full strength."""
    look = scaled(look, look.get("strength", 1) if strength is None else strength)
    look = {**look, "strength": 1}
    for text, key, conv in ((hues, "chroma_gains", lambda v: 1 + v / 100), (hue_shift, "hue_shifts", float)):
        for item in filter(None, (text or "").replace(" ", "").split(",")):
            m = re.fullmatch(r"([a-zA-Z]+|\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)", item)
            if not m:
                raise SystemExit(f"Can't read '{item}': use a hue and a signed number, e.g. orange+20")
            look[key] = [*look[key], (_hue(m.group(1)), width, conv(float(m.group(2))))]
    return look

CONTRAST_CHROMA = 0.7      # how much of a curve's local steepness carries into chroma
SLOPE_MIN = 0.85           # a flat highlight shoulder trims chroma only a little (film-like rolloff)
SKIN_HUE, SKIN_WIDTH = 62.0, 24.0   # OKLCH hue band where skin sits
SKIN_MAX_GAIN = 1.05       # moderately saturated colors in that band get at most +5% chroma

# ---------------------------------------------------------------- color math (Ottosson OKLab)
_M1 = np.array([[0.4122214708, 0.5363325363, 0.0514459929],
                [0.2119034982, 0.6806995451, 0.1073969566],
                [0.0883024619, 0.2817188376, 0.6299787005]])
_M2 = np.array([[0.2104542553, 0.7936177850, -0.0040720468],
                [1.9779984951, -2.4285922050, 0.4505937099],
                [0.0259040371, 0.7827717662, -0.8086757660]])
_M1_INV, _M2_INV = np.linalg.inv(_M1), np.linalg.inv(_M2)

# The block below was rewritten in Nov 2026 when the scripts moved to the cloud repo: the
# original lines were never captured in a transcript. Same math (sRGB transfer, Ottosson
# OKLab, PCHIP curves, raised-cosine hue bands, exact sRGB gamut edge), checked with the
# identity look (lossless) and the old example looks.
def srgb_decode(v):
    """sRGB / Rec.709-display encoded values (0-1) -> linear light."""
    v = np.asarray(v, dtype=float)
    return np.where(v <= 0.04045, v / 12.92, ((np.maximum(v, 0) + 0.055) / 1.055) ** 2.4)


def srgb_encode(x):
    """Linear light -> sRGB encoded values (0-1 for colors inside the gamut)."""
    x = np.asarray(x, dtype=float)
    return np.where(x <= 0.0031308, 12.92 * x, 1.055 * np.maximum(x, 0) ** (1 / 2.4) - 0.055)


def linear_to_oklab(rgb):
    lms = np.asarray(rgb, dtype=float) @ _M1.T
    return np.cbrt(lms) @ _M2.T


def oklab_to_linear(lab):
    lms_ = np.asarray(lab, dtype=float) @ _M2_INV.T
    return (lms_ ** 3) @ _M1_INV.T


def pchip(points):
    """Monotone cubic (PCHIP, Fritsch-Carlson) through the curve points; no overshoot."""
    pts = sorted((float(x), float(y)) for x, y in points)
    x = np.array([p[0] for p in pts])
    y = np.array([p[1] for p in pts])
    h = np.diff(x)
    d = np.diff(y) / h
    n = len(x)
    m = np.zeros(n)
    if n == 2:
        m[:] = d[0]
    else:
        for i in range(1, n - 1):
            if d[i - 1] * d[i] > 0:
                w1, w2 = 2 * h[i] + h[i - 1], h[i] + 2 * h[i - 1]
                m[i] = (w1 + w2) / (w1 / d[i - 1] + w2 / d[i])

        def edge(h0, h1, d0, d1):
            e = ((2 * h0 + h1) * d0 - h0 * d1) / (h0 + h1)
            if np.sign(e) != np.sign(d0):
                return 0.0
            if np.sign(d0) != np.sign(d1) and abs(e) > abs(3 * d0):
                return 3 * d0
            return e
        m[0] = edge(h[0], h[1], d[0], d[1])
        m[-1] = edge(h[-1], h[-2], d[-1], d[-2])

    def f(v):
        v = np.clip(np.asarray(v, dtype=float), x[0], x[-1])
        i = np.clip(np.searchsorted(x, v, side="right") - 1, 0, n - 2)
        t = (v - x[i]) / h[i]
        t2, t3 = t * t, t * t * t
        return ((2 * t3 - 3 * t2 + 1) * y[i] + (t3 - 2 * t2 + t) * h[i] * m[i]
                + (-2 * t3 + 3 * t2) * y[i + 1] + (t3 - t2) * h[i] * m[i + 1])
    return f


def _band(h, center, width):
    """Raised-cosine weight: 1 at the hue center, falling smoothly to 0 at +-width degrees."""
    dist = np.abs((np.asarray(h, dtype=float) - center + 180) % 360 - 180)
    return np.where(dist < width, 0.5 * (1 + np.cos(np.pi * dist / width)), 0.0)


class GamutEdge:
    """Largest chroma that still fits sRGB / Rec.709 at each OKLab lightness and hue,
    tabulated once (exact bisection) and looked up with bilinear interpolation."""
    _cache = None

    def __init__(self, n_l=257, n_h=361, steps=30):
        if GamutEdge._cache is None:
            Ls = np.linspace(0, 1, n_l)
            hs = np.linspace(0, 360, n_h)
            LL, HH = np.meshgrid(Ls, hs, indexing="ij")
            ca, sb = np.cos(np.radians(HH)), np.sin(np.radians(HH))
            lo, hi = np.zeros_like(LL), np.full_like(LL, 0.5)
            for _ in range(steps):
                mid = (lo + hi) / 2
                lin = oklab_to_linear(np.stack([LL, mid * ca, mid * sb], -1))
                fits = np.all((lin >= -1e-7) & (lin <= 1 + 1e-7), axis=-1)
                lo, hi = np.where(fits, mid, lo), np.where(fits, hi, mid)
            GamutEdge._cache = (Ls, hs, lo)
        self.Ls, self.hs, self.table = GamutEdge._cache

    def __call__(self, L, h):
        L = np.clip(np.asarray(L, dtype=float), 0, 1)
        h = np.asarray(h, dtype=float) % 360
        fl = L * (len(self.Ls) - 1)
        fh = h / 360 * (len(self.hs) - 1)
        i0 = np.clip(np.floor(fl).astype(int), 0, len(self.Ls) - 2)
        j0 = np.clip(np.floor(fh).astype(int), 0, len(self.hs) - 2)
        tl, th = fl - i0, fh - j0
        t = self.table
        return ((1 - tl) * (1 - th) * t[i0, j0] + tl * (1 - th) * t[i0 + 1, j0]
                + (1 - tl) * th * t[i0, j0 + 1] + tl * th * t[i0 + 1, j0 + 1])

def _smoothstep(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0, 1)
    return t * t * (3 - 2 * t)


def grade(rgb, look, black=0.0, white=1.0, edge=None):
    """Apply a look (a name, a .json path, or a look dict) to encoded RGB in [0,1],
    shape (..., 3). Returns encoded RGB."""
    p = load_look(look)
    p = scaled(p, p.get("strength", 1))
    edge = edge or GamutEdge()
    lab = linear_to_oklab(srgb_decode(rgb))
    L, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
    C, h = np.hypot(a, b), np.degrees(np.arctan2(b, a)) % 360

    # 1+2. Levels then the tone curve, on lightness only. Each lightness is mapped the way a
    # neutral gray of that lightness would be, so grays match an RGB curve exactly and colors
    # keep their hue.
    curve = pchip(p["curve"])

    def tone(Lx):
        v = srgb_encode(np.clip(Lx, 0, 1) ** 3)               # gray's code value
        v = np.clip((v - black) / max(white - black, 1e-6), 0, 1)
        return np.cbrt(srgb_decode(curve(v)))

    L2 = tone(L)

    # 3. Chroma. A steeper part of the curve adds contrast between channels in an RGB grade;
    # carry most of that into chroma so the look keeps its punch without the hue skew.
    eps = 2e-3
    slope = (tone(L + eps) - tone(L - eps)) / (2 * eps)
    gain = 1 + p.get("contrast_chroma", CONTRAST_CHROMA) * (np.clip(slope, SLOPE_MIN, 1.8) - 1)
    gain = gain * p.get("saturation", 1.0)
    for center, width, g in p["chroma_gains"]:
        gain = gain * (1 + _band(h, center, width) * (g - 1))
    rel = np.clip(C / np.maximum(edge(L, h), 1e-6), 0, 1)       # 0 = gray, 1 = gamut edge
    gain = gain * (1 + p["vibrance"] * (1 - rel))
    skin = _band(h, SKIN_HUE, SKIN_WIDTH) * _smoothstep(0.5, 0.3, rel) * _smoothstep(0.01, 0.03, C)
    skin = skin * (1.0 if p.get("protect_skin", True) else 0.0)
    gain = gain * (1 - skin) + np.minimum(gain, SKIN_MAX_GAIN) * skin
    C2 = C * gain

    # 4. Hue. Only colored pixels move (a near-gray's hue is noise), and skin barely moves.
    shift = np.zeros_like(h)
    for center, width, deg in p["hue_shifts"]:
        shift = shift + _band(h, center, width) * deg
    h2 = h + shift * _smoothstep(0.015, 0.05, C) * (1 - 0.8 * skin)

    # 5. Split tone.
    shadow_w = np.clip(1 - L2 / 0.6, 0, 1) ** 2
    high_w = np.clip((L2 - 0.4) / 0.6, 0, 1) ** 2
    a2 = C2 * np.cos(np.radians(h2)) + shadow_w * p["shadows_ab"][0] + high_w * p["highlights_ab"][0]
    b2 = C2 * np.sin(np.radians(h2)) + shadow_w * p["shadows_ab"][1] + high_w * p["highlights_ab"][1]

    # 6. Gamut: colors that don't fit get their chroma reduced, at the same lightness and hue,
    # until they just fit (exact search, only for the colors that need it).
    out = np.stack([L2, a2, b2], -1)
    return srgb_encode(fit_gamut(out))


def fit_gamut(lab, steps=22):
    """Linear RGB for OKLab colors, pulling out-of-gamut ones in along chroma only."""
    lin = oklab_to_linear(lab)
    bad = np.any((lin < -1e-6) | (lin > 1 + 1e-6), axis=-1)
    if np.any(bad):
        sub = lab[bad]
        lo, hi = np.zeros(len(sub)), np.ones(len(sub))
        for _ in range(steps):
            mid = (lo + hi) / 2
            test = oklab_to_linear(np.stack([sub[:, 0], sub[:, 1] * mid, sub[:, 2] * mid], -1))
            fits = np.all((test >= -1e-6) & (test <= 1 + 1e-6), axis=-1)
            lo, hi = np.where(fits, mid, lo), np.where(fits, hi, mid)
        lin[bad] = oklab_to_linear(np.stack([sub[:, 0], sub[:, 1] * lo, sub[:, 2] * lo], -1))
    return lin


# ---------------------------------------------------------------- steady exposure
# For clips where the camera's auto exposure changes mid-shot (a drone rising from shaded forest
# to a sunlit peak: the trees go dark and the peak starts out washed-out white). A look file can
# hold "exposure_keys": [[t, shadow_in, shadow_out, high_in, high_out, warm_boost], ...] in the
# clip's own seconds. At each moment, every color first moves by the same code-value amount on
# R, G and B (an exposure change, in log footage), picked by its brightness: shadows by
# shadow_out - shadow_in, highlights by high_out - high_in, black and white untouched. warm_boost
# adds color to warm highlights (sunlit gold) where the camera recorded them washed out.
# high_in / high_out may be null before the bright subject appears. Keys are linearly
# interpolated; the script renders them as a chain of LUTs, each enabled for its own moment.
LUMA709 = np.array([0.2126, 0.7152, 0.0722])


def exposure_pre(shadow_in, shadow_out, high_in=None, high_out=None, warm_boost=1.0):
    """The pre-grade step for one moment (see above), as a function on encoded RGB."""
    pts = [(0.0, 0.0), (shadow_in, shadow_out)]
    if high_in is not None and high_in > shadow_in + 0.02:
        pts.append((high_in, high_out))
    pts.append((1.0, 1.0))
    curve = pchip(pts)

    def f(rgb):
        y = rgb @ LUMA709
        out = np.clip(rgb + (curve(y) - y)[..., None], 0, 1)
        if high_out is None or warm_boost == 1:
            return out
        y2 = out @ LUMA709
        k = 1 + (warm_boost - 1) * (_smoothstep(high_out - 0.15, high_out - 0.05, y2)
                                     * _smoothstep(0.0, 0.03, out[..., 0] - out[..., 2]))
        return np.clip(y2[..., None] + (out - y2[..., None]) * k[..., None], 0, 1)
    return f


def exposure_at(keys, t):
    """Interpolated [shadow_in, shadow_out, high_in, high_out, warm_boost] at clip time t.
    A key without highlights borrows the nearest key's high_in, unchanged (no highlight move)."""
    keys = sorted(keys, key=lambda k: k[0])
    known = [k for k in keys if k[3] is not None]
    rows = []
    for k in keys:
        if k[3] is None and known:
            near = min(known, key=lambda q: abs(q[0] - k[0]))
            k = [k[0], k[1], k[2], near[3], near[3], 1.0]
        rows.append([float(v) if v is not None else np.nan for v in k])
    a = np.array(rows)
    vals = [float(np.interp(t, a[:, 0], a[:, i])) for i in range(1, 6)]
    if np.isnan(vals[2]):
        vals[2] = vals[3] = None
    return vals


def write_cube(path, look, black=0.0, white=1.0, size=65, exposure=None):
    """Bake the look (plus this clip's levels, and one moment's exposure step if given) into a
    .cube file."""
    x = np.linspace(0, 1, size)
    bb, gg, rr = np.meshgrid(x, x, x, indexing="ij")              # red varies fastest
    rgb = np.stack([rr, gg, bb], -1).reshape(-1, 3)
    if exposure is not None:
        rgb = exposure_pre(*exposure)(rgb)
    out = np.clip(grade(rgb, look, black, white), 0, 1)
    name = Path(path).stem if isinstance(look, dict) else Path(str(look)).stem
    name = re.sub(r'[^A-Za-z0-9_. +-]', "_", name)
    with open(path, "w", encoding="ascii") as f:
        f.write(f'TITLE "{name} look, levels {black:.4f}-{white:.4f}, '
                'Rec.709 full-range RGB in -> Rec.709 full-range RGB out"\n')
        f.write(f"LUT_3D_SIZE {size}\nDOMAIN_MIN 0 0 0\nDOMAIN_MAX 1 1 1\n")
        np.savetxt(f, out, fmt="%.6f")
    return path
