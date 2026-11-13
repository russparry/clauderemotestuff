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
#   cool_tint      {"ab": [a, b], "keep": [[from hue, to hue], ...], "dark": [l0, l1]}: cooler
#                  white balance except for the hue ranges in keep (default warm, [[330, 100]]),
#                  fading out below lightness l1 (default [0.25, 0.5])
#   sky_deepen     {"amount": 0.07, "hue": 245, "width": 45, "bright": [0.82, 0.97]}: darken
#                  near-white blue sky a little so it can show more blue (like a polarizer)
#   dark_tone      {"light": [l0, l1, l2, l3], "chroma": 0.6, "ab": [a, b]}: inside a lightness
#                  window (fades in l0-l1, out l2-l3) scale color and add an OKLab tint; b < 0 is
#                  bluer. For dark silhouettes (hazy mountains) that came out too brown
#   denoise       true = light noise reduction first (for pushed low-light footage)
#   exposure_offsets, shadow_lift_keys, warm_highlights: see "changes over time" below
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
            "contrast_chroma": s * look.get("contrast_chroma", CONTRAST_CHROMA), "strength": 1,
            **({"cool_tint": {**look["cool_tint"], "ab": [s * v for v in look["cool_tint"]["ab"]]}}
               if look.get("cool_tint") else {}),
            **({"sky_deepen": {**look["sky_deepen"], "amount": s * look["sky_deepen"]["amount"]}}
               if look.get("sky_deepen") else {})}


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

def _hue_window(h, start, end, fade=15.0):
    """1 for hues from start round to end (counterclockwise, wrapping at 360), easing to 0
    over `fade` degrees outside."""
    span = (end - start) % 360
    d = (np.asarray(h, dtype=float) - start) % 360
    outside = np.where(d <= span, 0.0, np.minimum(d - span, 360 - d))
    return 1 - _smoothstep(0, fade, outside)


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

    # 2b. Deepen a bright blue sky (what a polarizer does): near-white blue can't hold more blue
    # on screen, so blue that bright comes down a little first. Weighted by hue and by how
    # colored the pixel is (snow and clouds stay put), and only above "bright" lightness.
    sd = p.get("sky_deepen")
    if sd:
        w = (_band(h, _hue(sd.get("hue", 245)), float(sd.get("width", 45)))
             * _smoothstep(*sd.get("chroma", [0.008, 0.02]), C)
             * _smoothstep(*sd.get("bright", [0.82, 0.97]), L2))
        L2 = L2 - float(sd["amount"]) * w

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

    # 3b. Dark tones ("dark_tone"): inside a lightness window, scale color and add a tint (step 5b).
    # For hazy mountains in silhouette that vibrance and saturation pushed brown and orange (Nov 13
    # 2026 sunset clip). Brightness only (never position), so an object can't come out two-toned.
    dt = p.get("dark_tone")
    if dt:
        l0, l1, l2, l3 = dt.get("light", [0.25, 0.32, 0.50, 0.60])
        dark_w = _smoothstep(l0, l1, L2) * (1 - _smoothstep(l2, l3, L2))
        C2 = C2 * (1 + dark_w * (float(dt.get("chroma", 1.0)) - 1))

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
    if dt:
        a2 = a2 + dark_w * dt.get("ab", [0, 0])[0]
        b2 = b2 + dark_w * dt.get("ab", [0, 0])[1]

    # 5b. Cool tint: a cooler white balance for grays, snow, shaded rock and sky, while the hue
    # ranges in "keep" (default: warm) keep their own color, so golden light and browns stay
    # exactly as they were. Depends on color and brightness only (never position). Fades out in
    # the dark ("dark": [from, to] lightness), where faint colors are mostly noise and a tint
    # turns dim greens teal.
    ct = p.get("cool_tint")
    if ct:
        keep = np.zeros_like(h)
        for start, end in ct.get("keep", [[330, 100]]):
            keep = np.maximum(keep, _hue_window(h, _hue(start), _hue(end)))
        w = (1 - keep * _smoothstep(0.004, 0.014, C)) * _smoothstep(*ct.get("dark", [0.25, 0.5]), L2)
        a2 = a2 + w * ct["ab"][0]
        b2 = b2 + w * ct["ab"][1]

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


# ---------------------------------------------------------------- changes over time, warm highlights
# Optional look keys, for clips whose camera exposure changes mid-shot (a drone rising from shaded
# forest to a sunlit peak). Times are the clip's own seconds, interpolated linearly.
#   exposure_offsets  [[t, offset], ...] undoes the camera's own exposure change: the same code-value
#                     offset for every color (an exposure change, in log footage), with a soft
#                     shoulder above 0.82 so nothing clips. Measured from how much neighbouring
#                     frames brighten or darken (median over well-exposed pixels), not from scene
#                     content, so browns, snow and trees all move together.
#   shadow_lift_keys  [[t, amount], ...] lifts only the darkest tones: zero at black and from 0.4 up,
#                     so midtones such as browns don't move. Keep amount under 0.17.
#   warm_highlights   {"knee", "ceiling", "color", "hue"}: a fixed rule for every frame. Warm, bright colors
#                     above the knee are eased toward the ceiling, with color added in proportion to
#                     the brightness taken away, so a washed-out sunlit peak turns gold and a gold one
#                     barely changes. "hue" (degrees per unit of brightness removed beyond
#                     "hue_from", minus = toward orange) turns clearly washed-out ones back from
#                     the yellow the camera's highlight roll-off gives them. "strength_keys"
#                     [[t, 0-1], ...] limits it to the moments that need it; at 0 it's off. Easing
#                     brightness flattens texture, so use it only where highlights are washed out. Neutral and cool colors (snow, sky) are untouched.
# Nov 2026 lesson: an earlier version moved shadows and highlights by different amounts that changed
# over time; that squeezed the midtones and made all the browns shift at 12 s. Keep time-varying
# changes uniform (exposure) or confined to the darkest tones.
LUMA709 = np.array([0.2126, 0.7152, 0.0722])
TIME_KEYS = ("exposure_offsets", "shadow_lift_keys")


def _key_at(keys, t):
    if not keys:
        return 0.0
    a = np.array(sorted(keys), dtype=float)
    return float(np.interp(t, a[:, 0], a[:, 1]))


# region_grade: a second grade for one area of the frame (e.g. a sunlit peak that the camera
# washed out), blended in through a soft mask the script builds from each frame:
#   "keys":  [[t, exposure, color, hue], ...]  an even exposure offset (same code value on R, G, B),
#            color strength multiplier and hue turn (degrees, minus = toward orange), the same for
#            every pixel in the area, so its texture is kept. (0, 1, 0) = no change.
#   "matte": which pixels: warm (red above blue, "warm" range), bright ("bright" range, code
#            values), in the top of the picture ("top" range, fraction of the height, fading
#            out), blurred ("blur", fraction of the width) and scaled by "gain"; bright pixels
#            that aren't warm ("sky" range: sky, snow) are left out, so there's no halo at the ridge.
def region_at(look, t):
    rg = look.get("region_grade")
    if not rg or t is None:
        return (0.0, 1.0, 0.0)
    a = np.array(rg["keys"], dtype=float)
    return tuple(float(np.interp(t, a[:, 0], a[:, i])) for i in (1, 2, 3))


def region_adjust(rgb, exposure, color, hue):
    out = np.clip(rgb + exposure, 0, 1)
    if color != 1:
        y = out @ LUMA709
        out = np.clip(y[..., None] + (out - y[..., None]) * color, 0, 1)
    if hue:
        lab = linear_to_oklab(srgb_decode(out))
        c, s_ = np.cos(np.radians(hue)), np.sin(np.radians(hue))
        lab = np.stack([lab[..., 0], lab[..., 1] * c - lab[..., 2] * s_, lab[..., 1] * s_ + lab[..., 2] * c], -1)
        out = np.clip(srgb_encode(fit_gamut(lab)), 0, 1)
    return out


# forest_boost: brighten the darker forest like an exposure increase (a gain on the code values, so
# its color and contrast are kept, unlike a lift that adds gray), only inside a soft mask the
# script builds from each frame: darker areas (blurred), with sunlit warm highlights and sky/snow
# cut out sharply, and in the top of the picture ("top" range) only greens, so tall evergreens get
# the same boost top to bottom while the shaded rock under the peak doesn't. Applied before the look.
#   "keys":        [[t, gain], ...]  1 = off
#   "green_extra": evergreens (green above red) get gain ** green_extra, since very dark greens
#                  gain less from a multiply
#   "matte":       {"dark": [a, b], "top": [a, b], "warm_bright": [a, b], "sky": [a, b], "blur": f}
def forest_gain_at(look, t):
    fb = look.get("forest_boost")
    if not fb or t is None:
        return 1.0
    return _key_at(fb["keys"], t)


def forest_boost(rgb, gain, green_extra=1.5, dark_taper=None):
    """dark_taper [lo, hi]: the very darkest specks (below lo, mostly camera noise) get little of the
    gain, easing to the full gain at hi, so the boost doesn't turn noise into grain."""
    g = _smoothstep(0.0, 0.02, rgb[..., 1] - rgb[..., 0])
    k = gain ** (1 + (green_extra - 1) * g)
    if dark_taper:
        k = 1 + (k - 1) * _smoothstep(dark_taper[0], dark_taper[1], rgb @ LUMA709)
    return np.clip(rgb * k[..., None], 0, 1)


def write_boost_cube(path, gain, green_extra=1.5, size=33, dark_taper=None):
    x = np.linspace(0, 1, size)
    bb, gg, rr = np.meshgrid(x, x, x, indexing="ij")
    out = forest_boost(np.stack([rr, gg, bb], -1).reshape(-1, 3), gain, green_extra, dark_taper)
    with open(path, "w", encoding="ascii") as f:
        f.write(f'TITLE "forest boost x{gain:.3f}"\nLUT_3D_SIZE {size}\nDOMAIN_MIN 0 0 0\nDOMAIN_MAX 1 1 1\n')
        np.savetxt(f, out, fmt="%.6f")
    return path


def has_time_keys(look):
    return (any(look.get(k) for k in TIME_KEYS) or bool((look.get("warm_highlights") or {}).get("strength_keys"))
            or bool((look.get("region_grade") or {}).get("keys")) or bool((look.get("forest_boost") or {}).get("keys")))


def moment(look, t):
    """(exposure offset, shadow lift, warm-highlight strength) at clip time t."""
    wh = look.get("warm_highlights") or {}
    d, k, h = region_at(look, t)
    return (_key_at(look.get("exposure_offsets"), t), _key_at(look.get("shadow_lift_keys"), t),
            _key_at(wh.get("strength_keys"), t) if wh.get("strength_keys") else 1.0, d, k - 1, h / 100)


def _shift_luma(rgb, y_new):
    y = rgb @ LUMA709
    return np.clip(rgb + (y_new - y)[..., None], 0, 1)


def pre_transform(look, t=None):
    """The step before the look's grade (exposure offset and shadow lift at time t, then the warm
    highlight rule), as a function on encoded RGB; None if the look uses none of them."""
    offset, lift = moment(look, t)[:2] if (t is not None and has_time_keys(look)) else (0.0, 0.0)
    wh = look.get("warm_highlights")
    if wh and wh.get("strength_keys") and t is not None and _key_at(wh["strength_keys"], t) <= 0:
        wh = None
    if not offset and not lift and not wh:
        return None

    def f(rgb):
        out = rgb
        if offset:
            y = out @ LUMA709
            z = y + offset
            if offset > 0:                    # brightening: squeeze what would pass white back under it
                knee = 0.82                   # (no change at all when the offset is 0, so no jump)
                z = np.where(z > knee, knee + (z - knee) * (1 - knee) / (1 + offset - knee), z)
            out = _shift_luma(out, z)
        if lift:
            u = np.clip((out @ LUMA709) / 0.4, 0, 1)
            out = np.clip(out + (lift * 6.75 * u * (1 - u) ** 2)[..., None], 0, 1)
        strength = _key_at(wh.get("strength_keys"), t) if (wh and wh.get("strength_keys") and t is not None) else 1.0
        if wh and strength > 0:
            knee, ceil, per = wh.get("knee", 0.62), wh.get("ceiling", 0.70), wh.get("color", 5.0)
            y = out @ LUMA709
            warm = _smoothstep(0.0, 0.03, out[..., 0] - out[..., 2])
            eased = np.where(y > knee, knee + (ceil - knee) * (1 - np.exp(-np.maximum(y - knee, 0) / (ceil - knee))), y)
            y2 = y + (eased - y) * warm * strength
            out = _shift_luma(out, y2)
            k = 1 + per * (y - y2)
            yy = out @ LUMA709
            out = np.clip(yy[..., None] + (out - yy[..., None]) * k[..., None], 0, 1)
            turn = wh.get("hue", 0.0)
            if turn:
                # Washed-out warm highlights also come out yellower (the camera's highlight
                # roll-off squeezes red first): turn them toward orange in proportion to the
                # brightness taken away, in OKLab, keeping lightness and chroma.
                lab = linear_to_oklab(srgb_decode(out))
                ang = np.radians(turn * np.maximum((y - y2) - wh.get("hue_from", 0.04), 0))
                c, s_ = np.cos(ang), np.sin(ang)
                a2 = lab[..., 1] * c - lab[..., 2] * s_
                b2 = lab[..., 1] * s_ + lab[..., 2] * c
                lab = np.stack([lab[..., 0], a2, b2], -1)
                out = np.clip(srgb_encode(fit_gamut(lab)), 0, 1)
        return out
    return f


def write_cube(path, look, black=0.0, white=1.0, size=65, t=None, region=False):
    """Bake the look (plus this clip's levels, and the look's pre-grade step at clip time t if it
    has one) into a .cube file."""
    x = np.linspace(0, 1, size)
    bb, gg, rr = np.meshgrid(x, x, x, indexing="ij")              # red varies fastest
    rgb = np.stack([rr, gg, bb], -1).reshape(-1, 3)
    look_d = look if isinstance(look, dict) else load_look(look)
    pre = pre_transform(look_d, t)
    if pre is not None:
        rgb = pre(rgb)
    if region:
        rgb = region_adjust(rgb, *region_at(look_d, t))
    out = np.clip(grade(rgb, look, black, white), 0, 1)
    name = Path(path).stem if isinstance(look, dict) else Path(str(look)).stem
    name = re.sub(r'[^A-Za-z0-9_. +-]', "_", name)
    with open(path, "w", encoding="ascii") as f:
        f.write(f'TITLE "{name} look, levels {black:.4f}-{white:.4f}, '
                'Rec.709 full-range RGB in -> Rec.709 full-range RGB out"\n')
        f.write(f"LUT_3D_SIZE {size}\nDOMAIN_MIN 0 0 0\nDOMAIN_MAX 1 1 1\n")
        np.savetxt(f, out, fmt="%.6f")
    return path


# ---------------------------------------------------------------- masks, as numpy (for stills and tools)
# These mirror make_signature_video.region_matte / forest_matte (built there as ffmpeg filters at
# 480 px wide), so grade_tools.py can show what a render will do in seconds instead of minutes.
def _gblur(img, sigma):
    r = max(1, int(3 * sigma))
    x = np.arange(-r, r + 1)
    k = np.exp(-x ** 2 / (2 * sigma ** 2))
    k /= k.sum()
    p = np.pad(img, r, mode="edge")
    tmp = np.apply_along_axis(lambda v: np.convolve(v, k, mode="valid"), 1, p)
    return np.apply_along_axis(lambda v: np.convolve(v, k, mode="valid"), 0, tmp)


def region_matte_np(rgb, mt):
    """rgb: (H, W, 3) encoded picture at about 480 px wide. Same rules as region_matte."""
    H, W = rgb.shape[:2]
    y = rgb @ LUMA709
    w0, w1 = mt.get("warm", (0.0, 0.03))
    warm = _smoothstep(w0, w1, rgb[..., 0] - rgb[..., 2])
    q = warm * _smoothstep(*mt.get("bright", (0.68, 0.80)), y) * (1 - _smoothstep(*mt.get("top", (0.30, 0.50)), np.arange(H)[:, None] / H))
    sigma = max(0.5, mt.get("blur", 5 / 480) * W)
    return np.clip(_gblur(q, sigma) * mt.get("gain", 1.6), 0, 1) * (1 - _smoothstep(*mt.get("sky", (0.80, 0.90)), y) * (1 - warm))


def forest_matte_np(rgb, mt):
    """rgb: (H, W, 3) encoded picture at about 480 px wide. Same rules as forest_matte."""
    H, W = rgb.shape[:2]
    y = rgb @ LUMA709
    d = 1 - _smoothstep(*mt.get("dark", (0.40, 0.60)), y)
    sigma = max(0.5, mt.get("blur", 6 / 480) * W)
    warm = _smoothstep(0.0, 0.03, rgb[..., 0] - rgb[..., 2])
    keep = np.clip(1 - warm * _smoothstep(*mt.get("warm_bright", (0.48, 0.60)), y) - _smoothstep(*mt.get("sky", (0.75, 0.88)), y), 0, 1)
    if mt.get("top"):
        top = _smoothstep(*mt["top"], np.arange(H)[:, None] / H)
        green = np.maximum(_smoothstep(0.0, 0.02, rgb[..., 1] - rgb[..., 0]), 1 - _smoothstep(0.20, 0.30, y))
        keep = keep * (top + (1 - top) * green)
    return np.clip(_gblur(d, sigma), 0, 1) * keep


def grade_picture(rgb, look, t, black=0.0, white=1.0):
    """The whole grade for one picture (H, W, 3) at clip time t, as the script renders it, minus
    sharpening, vignette and denoising: forest boost through its mask, the look (with its moment's
    pre-grade step), and the region grade through its mask. Encoded RGB in and out."""
    look = look if isinstance(look, dict) else load_look(look)
    shape = rgb.shape
    px = rgb.reshape(-1, 3)
    fb = look.get("forest_boost")
    if fb:
        k = forest_gain_at(look, t)
        if k != 1:
            m = forest_matte_np(rgb, fb.get("matte", {})).reshape(-1, 1)
            px = px + m * (forest_boost(px, k, fb.get("green_extra", 1.5), fb.get("dark_taper")) - px)
    pre = pre_transform(look, t)
    base = pre(px) if pre else px
    out = np.clip(grade(base, look, black, white), 0, 1)
    rg = look.get("region_grade")
    if rg:
        d, kk, h = region_at(look, t)
        if (d, kk, h) != (0.0, 1.0, 0.0):
            b = np.clip(grade(region_adjust(base, d, kk, h), look, black, white), 0, 1)
            m = region_matte_np(rgb, rg.get("matte", {})).reshape(-1, 1)
            out = out + m * (b - out)
    return out.reshape(shape)
