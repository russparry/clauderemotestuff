#!/usr/bin/env python3
"""Tools for grading a video, the way the Nov 2026 drone clip was worked out (GRADING_GUIDE.md).

  analyze CLIP              what the footage is: size, HDR/log, its hues, and how brightness, the
                            shadows and any warm sunlit highlights change over time (exposure too)
  stills CLIP --times ...   graded stills in seconds (same math as a render, minus sharpening),
                            one row per look, to compare looks before a 15-20 minute render
  keys CLIP --region ...    region_grade keys for a washed-out warm highlight (a sunlit peak)
  keys CLIP --forest ...    forest_boost keys that hold the shadows at a target brightness
  check VIDEO               a finished render: grain in dark areas, trees two-toned or not,
                            flicker, warm-highlight color over time; --vs OLD for before/after

Times are the clip's own seconds. Stills and sheets are saved in review/ (not committed).
Examples:
  python3 grade_tools.py analyze before/clip.mp4 --to 26
  python3 grade_tools.py stills before/clip.mp4 --times 8,13,17,21 --look clip --vs neutral
  python3 grade_tools.py keys before/clip.mp4 --region 12-17 --settled 20-23
  python3 grade_tools.py keys before/clip.mp4 --forest 13-26 --target 55 --max-gain 1.4
  python3 grade_tools.py check graded/clip_graded.mp4 --source before/clip.mp4 --vs old.mp4
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import look_lut as L  # noqa: E402
import make_signature_video as M  # noqa: E402

REVIEW = HERE / "review"
HUE_NAMES = sorted(L.HUES.items(), key=lambda kv: kv[1])


def hue_name(h):
    return min(HUE_NAMES, key=lambda kv: abs((h - kv[1] + 180) % 360 - 180))[0]


def frame(path, t, width=480, crop=None):
    """Encoded RGB (H, W, 3) at time t, scaled to `width` (aspect kept). crop='w:h:x:y' first."""
    info = M.probe(path)
    w, h = (info["w"], info["h"]) if not crop else tuple(int(v) for v in crop.split(":")[:2])
    hh = M.even(width * h / w)
    vf = (f"crop={crop}," if crop else "") + (M.TONEMAP + "," if info["hdr"] else "") + f"scale={width}:{hh}"
    f = M.ff_frame(Path(path), t, vf + ",format=gbrp16le", width, hh)
    if f is None:
        sys.exit(f"Couldn't read a frame at {t} s from {path}")
    return f


def lab_of(rgb):
    lab = L.linear_to_oklab(L.srgb_decode(rgb))
    return lab[..., 0], np.hypot(lab[..., 1], lab[..., 2]), np.degrees(np.arctan2(lab[..., 2], lab[..., 1])) % 360


def warm_highlights(f):
    """Sunlit warm highlights in the top half: (brightness, color, hue, share) or None."""
    top = f[: f.shape[0] // 2]
    Lc, C, h = lab_of(top)
    mk = (Lc > 0.6) & (h > 35) & (h < 110) & (C > 0.02)
    if mk.sum() < 200:
        return None
    return float(np.median(top[mk] @ L.LUMA709)), float(np.median(C[mk])), float(np.median(h[mk])), float(mk.mean())


def parse_range(text):
    a, b = (float(v) for v in text.split("-"))
    return a, b


def levels_for(clip, t_from, t_to, info):
    sample = (f"scale=480:{M.even(480 * info['h'] / info['w'])}", 480, M.even(480 * info["h"] / info["w"]))
    to_rgb = M.TONEMAP if info["hdr"] else "format=gbrp16le"
    lumas = M.sample_lumas(Path(clip), [t_from + (t_to - t_from) * (i + 0.5) / 12 for i in range(12)], to_rgb, sample)
    black, white, _ = M.levels_from(lumas, M.active_box(lumas))
    return black, white


# ---------------------------------------------------------------- analyze
def cmd_analyze(a):
    clip = Path(a.clip)
    info = M.probe(clip)
    times, end = M.frame_times(clip)
    t0, t1 = a.trim_from or 0.0, min(a.trim_to or end, end)
    fps = len(times) / end
    name = clip.name.lower()
    print(f"{clip.name}: {info['w']}x{info['h']}, {fps:.2f} fps, {end:.2f} s, audio={info['audio'] or 'none'}, hdr={info['hdr']}")
    frames = {round(t, 2): frame(clip, t) for t in np.arange(t0, t1 + 1e-6, a.every)}
    allpx = np.concatenate([f.reshape(-1, 3) for f in frames.values()])
    Lc, C, h = lab_of(allpx)
    y = allpx @ L.LUMA709
    log = "d_log" in name or "dlog" in name or "slog" in name or "log" in name.split("_")[-1] or C.mean() < 0.015
    print(f"  brightness 0.3/50/99.7%: {np.percentile(y, [0.3, 50, 99.7]).round(3)}; average color {C.mean():.4f}"
          + ("  -> looks like LOG footage (flat, washed out): it needs real contrast and color" if log else ""))
    if not info["hdr"] and info["w"] / info["h"] > 1.2:
        print("  horizontal clip: for a signature video, ask: fill the vertical frame (crops the sides) or --fit (bars)")
    col = C > 0.02
    hist, edges = np.histogram(h[col], bins=np.arange(0, 361, 15), weights=C[col])
    hist = hist / max(hist.sum(), 1e-9)
    print("  main hues (by color weight):", ", ".join(f"{hue_name(e + 7.5)} {e:.0f}-{e + 15:.0f} {v * 100:.0f}%"
                                                   for e, v in sorted(zip(edges[:-1], hist), key=lambda x: -x[1])[:5] if v > 0.03))
    print("\n   t    | overall | shadows (lower half) | warm sunlit highlights: brightness color hue share | exposure change")
    # exposure: median code-value change between neighbours (log footage: exposure moves every code value)
    r = subprocess.run([M.FF, "-v", "error", "-ss", str(t0), "-t", str(t1 - t0), "-i", str(clip), "-vf",
                        "fps=10,scale=240:-2,format=rgb48le", "-f", "rawvideo", "-"], capture_output=True)
    H10 = M.even(240 * info["h"] / info["w"])
    dense = np.frombuffer(r.stdout, "<u2")
    dense = dense[: dense.size // (H10 * 240 * 3) * H10 * 240 * 3].reshape(-1, H10, 240, 3) / 65535.0 @ L.LUMA709
    steps = [0.0] + [float(np.median((b - a_)[(a_ > 0.15) & (a_ < 0.78) & (b > 0.15) & (b < 0.78)]))
                     if ((a_ > 0.15) & (a_ < 0.78) & (b > 0.15) & (b < 0.78)).sum() > 300 else 0.0
                     for a_, b in zip(dense[:-1], dense[1:])]
    expo = np.cumsum(steps)
    dt = t0 + np.arange(len(expo)) / 10
    rows = []
    for t, f in frames.items():
        yy = f @ L.LUMA709
        wh = warm_highlights(f)
        e = float(np.interp(t, dt, expo)) if len(expo) else 0.0
        rows.append({"t": t, "overall": float(np.median(yy)), "shadows": float(np.median(yy[f.shape[0] // 2:])),
                     "warm": wh, "exposure": e})
        whs = f"{wh[0]:.3f}  {wh[1]:.4f} {wh[2]:5.1f}  {wh[3] * 100:4.1f}%" if wh else "        -"
        print(f"  {t:5.1f} |  {np.median(yy):.3f}  |  {np.median(yy[f.shape[0] // 2:]):.3f}              | {whs:38s} | {e:+.3f}")
    REVIEW.mkdir(exist_ok=True)
    out = REVIEW / f"{clip.stem}_analysis.json"
    json.dump({"clip": str(clip), "info": {k: v for k, v in info.items()}, "fps": fps, "rows": rows}, open(out, "w"), indent=1)
    print(f"\nsaved {out}")
    print("Read it like this: shadows falling while exposure stays put = the scene (backlight), not the camera; "
          "warm highlights near 0.8-0.9 with weak color = washed out by the camera (region_grade); "
          "exposure moving = the camera re-exposing (exposure_offsets).")


# ---------------------------------------------------------------- stills
def cmd_stills(a):
    clip = Path(a.clip)
    info = M.probe(clip)
    times = [float(t) for t in a.times.split(",")]
    _, end = M.frame_times(clip)
    black, white = (0.0, 1.0) if a.no_levels else levels_for(clip, a.trim_from or 0.0, a.trim_to or end, info)
    looks = [a.look] + (a.vs.split(",") if a.vs else [])
    frames = [frame(clip, t, a.width) for t in times]
    rows = []
    print(f"levels {black:.3f}-{white:.3f}; rows: " + ", ".join(looks) + "; columns: " + ", ".join(f"{t:g} s" for t in times))
    for name in looks:
        row = []
        for t, f in zip(times, frames):
            g = f if name == "raw" else L.grade_picture(f, name, t, black, white)
            row.append(g)
            Y = 16 + 219 * (g @ L.LUMA709)
            wh = warm_highlights(g)
            print(f"  {name[:28]:28s} {t:6.2f} s: median {np.median(Y):4.0f}, shadows {np.median(Y[g.shape[0] // 2:]):4.0f}"
                  + (f", warm highlights {16 + 219 * wh[0]:4.0f} color {wh[1]:.3f} hue {wh[2]:3.0f}" if wh else ""))
        rows.append(np.concatenate(row, 1))
    img = (np.clip(np.concatenate(rows, 0), 0, 1) * 255).round().astype(np.uint8)
    REVIEW.mkdir(exist_ok=True)
    out = REVIEW / f"{clip.stem}_stills.jpg"
    hh, ww = img.shape[:2]
    subprocess.run([M.FF, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{ww}x{hh}", "-i", "-",
                    "-q:v", "2", str(out)], input=img.tobytes(), check=True)
    print(f"saved {out}")


# ---------------------------------------------------------------- keys
def cmd_keys(a):
    clip = Path(a.clip)
    info = M.probe(clip)
    if a.region:
        r0, r1 = parse_range(a.region)
        s0, s1 = parse_range(a.settled)
        settled = [warm_highlights(frame(clip, t)) for t in np.arange(s0, s1 + 1e-6, 0.5)]
        settled = [s for s in settled if s]
        if not settled:
            sys.exit("No warm sunlit highlights in the settled window.")
        Ys, Cs, Hs = (float(np.median([s[i] for s in settled])) for i in range(3))
        keys = [[round(r0 - 0.5, 2), 0.0, 1.0, 0.0]]
        for t in np.arange(r0, r1 + 1e-6, 0.5):
            wh = warm_highlights(frame(clip, t))
            if not wh:
                continue
            keys.append([round(float(t), 2), round(min(0.0, Ys - wh[0]), 4), round(max(1.0, Cs / wh[1]), 3),
                         round(min(0.0, ((Hs - wh[2] + 180) % 360) - 180), 1)])
        keys.append([round(r1 + 0.5, 2), 0.0, 1.0, 0.0])
        print(f"settled highlights ({s0:g}-{s1:g} s): brightness {Ys:.3f}, color {Cs:.4f}, hue {Hs:.1f}")
        print('"region_grade": ' + json.dumps({"name": "sunlit highlights", "keys": keys,
                                                 "matte": {"warm": [0.0, 0.03], "bright": [0.68, 0.80], "top": [0.30, 0.50],
                                                           "sky": [0.80, 0.90], "blur": round(5 / 480, 5), "gain": 1.6}}))
        print("Check the mask on stills (stills --look) before rendering: it must not catch pale midtones mid-frame.")
    if a.forest:
        f0, f1 = parse_range(a.forest)
        look = L.load_look(a.look) if a.look else L.load_look(clip.stem)
        fb = dict(look.get("forest_boost") or {})
        mt = fb.get("matte", {"dark": [0.40, 0.60], "warm_bright": [0.48, 0.60], "sky": [0.75, 0.88], "blur": 0.0125})
        _, end = M.frame_times(clip)
        black, white = levels_for(clip, 0.0, end, info)
        keys = [[round(f0 - 0.5, 2), 1.0]]
        for t in np.arange(f0, f1 + 1e-6, 0.5):
            f = frame(clip, t)
            m = L.forest_matte_np(f, mt) > 0.5
            if m.sum() < 200:
                keys.append([round(float(t), 2), 1.0])
                continue

            def shadows(gain):
                lk = dict(look)
                lk["forest_boost"] = {**fb, "keys": [[0, gain], [1e9, gain]], "matte": mt, "green_extra": fb.get("green_extra", 1.0)}
                g = L.grade_picture(f, lk, t, black, white)
                return np.median(16 + 219 * (g @ L.LUMA709)[m])
            lo, hi = 1.0, a.max_gain
            if shadows(1.0) < a.target:
                for _ in range(12):
                    mid = (lo + hi) / 2
                    lo, hi = (mid, hi) if shadows(mid) < a.target else (lo, mid)
            keys.append([round(float(t), 2), round(lo, 3)])
        g = np.array([k[1] for k in keys])
        g[1:-1] = np.convolve(np.pad(g, 1, mode="edge"), np.ones(3) / 3, mode="valid")[1:-1]
        keys = [[k[0], round(float(v), 3)] for k, v in zip(keys, g)]
        print(f"forest_boost keys for shadows at Y {a.target:g} (capped at x{a.max_gain:g}; grain grows with the gain):")
        print('"forest_boost": ' + json.dumps({"keys": keys, "green_extra": 1.0, "matte": mt,
                                               "denoise": "vaguedenoiser=threshold=4:method=soft:nsteps=5"}))


# ---------------------------------------------------------------- check
def picture_box(path, t):
    """The picture inside any black bars of a finished video: crop string w:h:x:y."""
    info = M.probe(path)
    f = M.ff_frame(Path(path), t, "scale=270:480:force_original_aspect_ratio=decrease,pad=270:480:(ow-iw)/2:(oh-ih)/2,format=gbrp16le", 270, 480)
    rows = np.where((f @ L.LUMA709).max(axis=1) > 0.08)[0]
    cols = np.where((f @ L.LUMA709).max(axis=0) > 0.08)[0]
    s = max(info["w"] / 270, info["h"] / 480)
    ox, oy = (270 - info["w"] / s) / 2, (480 - info["h"] / s) / 2
    x0, x1 = max(0, (cols[0] - ox) * s), min(info["w"], (cols[-1] + 1 - ox) * s)
    y0, y1 = max(0, (rows[0] - oy) * s), min(info["h"], (rows[-1] + 1 - oy) * s)
    w, h = M.even(x1 - x0) - 8, M.even(y1 - y0) - 8
    return f"{w}:{h}:{M.even(x0) + 4}:{M.even(y0) + 4}"


def grain(y):
    pad = np.pad(y, 1, mode="edge")
    blur = sum(pad[i:i + y.shape[0], j:j + y.shape[1]] for i in range(3) for j in range(3)) / 9
    return np.abs(y - blur)


def cmd_check(a):
    vid = Path(a.video)
    _, end = M.frame_times(vid)
    times = [float(t) for t in a.times.split(",")] if a.times else [round(end * f, 2) for f in (0.15, 0.35, 0.55, 0.75, 0.9)]
    crop = a.crop or picture_box(vid, times[len(times) // 2])
    cw, ch = (int(v) for v in crop.split(":")[:2])
    vids = [("new", vid)] + ([("old", Path(a.vs))] if a.vs else [])
    off = a.source_offset
    print(f"picture area {crop}; times {', '.join(f'{t:g}' for t in times)}")
    print("   t    | video | dark areas: brightness grain | trees top/lower (1 = even, <0.85 = dark tops) | warm highlights")
    sheet = {n: [] for n, _ in vids}
    for t in times:
        src = frame(Path(a.source), t + off, 480) if a.source else None
        for n, v in vids:
            full = frame(v, t, min(cw, 1440), crop)
            yf = full @ L.LUMA709 * 219 + 16
            dark = yf < 80
            gr = grain(yf)[dark].mean() if dark.sum() > 500 else float("nan")
            g = frame(v, t, 480, crop)
            Y = 16 + 219 * (g @ L.LUMA709)
            ratio = float("nan")
            if src is not None:
                if src.shape[:2] != g.shape[:2]:      # picture cropped from bars: map the raw frame onto it
                    ri = np.linspace(0, src.shape[0] - 1, g.shape[0]).round().astype(int)
                    ci = np.linspace(0, src.shape[1] - 1, g.shape[1]).round().astype(int)
                    src = src[ri][:, ci]
                _, Cs, hs = lab_of(src)
                ys = src @ L.LUMA709
                trees = (hs > 115) & (hs < 210) & (Cs > 0.01) & (ys < 0.35)
                rows = np.arange(g.shape[0])[:, None] * np.ones((1, g.shape[1]))
                up, lo = trees & (rows < g.shape[0] * 0.3), trees & (rows > g.shape[0] * 0.5)
                if up.sum() > 20 and lo.sum() > 20:
                    ratio = np.median(Y[up]) / np.median(Y[lo])
            wh = warm_highlights(g)
            flag = "  <- two-toned?" if ratio == ratio and ratio < 0.85 else ""
            print(f"  {t:5.1f} | {n:5s} |   {np.median(yf[dark]) if dark.any() else float('nan'):4.0f}      {gr:5.2f}      |"
                  f"   {ratio:.2f}{flag:16s}                             | "
                  + (f"{16 + 219 * wh[0]:4.0f} color {wh[1]:.3f} hue {wh[2]:3.0f}" if wh else "-"))
            sheet[n].append(np.clip(frame(v, t, 480, crop), 0, 1))
    for n, v in vids:
        r = subprocess.run([M.FF, "-v", "error", "-i", str(v), "-vf", f"crop={crop},scale=240:-2,format=rgb48le", "-f", "rawvideo", "-"],
                           capture_output=True)
        hh = M.even(240 * ch / cw)
        fr = np.frombuffer(r.stdout, "<u2")
        fr = fr[: fr.size // (hh * 240 * 3) * hh * 240 * 3].reshape(-1, hh, 240, 3) / 65535.0
        yy = (fr @ L.LUMA709).mean(axis=(1, 2)) * 219
        jumps = np.abs(np.diff(yy))
        print(f"flicker ({n}): biggest frame-to-frame brightness change {jumps.max():.2f} levels (typical {np.median(jumps):.2f}); "
              "camera motion gives up to about 1-2")
    REVIEW.mkdir(exist_ok=True)
    img = np.concatenate([np.concatenate(sheet[n], 1) for n, _ in vids], 0)
    img = (img * 255).round().astype(np.uint8)
    out = REVIEW / f"{vid.stem}_check.jpg"
    hh, ww = img.shape[:2]
    subprocess.run([M.FF, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{ww}x{hh}", "-i", "-",
                    "-q:v", "2", str(out)], input=img.tobytes(), check=True)
    print(f"saved {out} (rows: {', '.join(n for n, _ in vids)})")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("analyze")
    p.add_argument("clip")
    p.add_argument("--from", dest="trim_from", type=float)
    p.add_argument("--to", dest="trim_to", type=float)
    p.add_argument("--every", type=float, default=1.0, help="seconds between measured frames (default 1)")
    p = sub.add_parser("stills")
    p.add_argument("clip")
    p.add_argument("--times", required=True, help="e.g. 8,13,17,21")
    p.add_argument("--look", default="neutral", help="look name, .json path, or 'raw'")
    p.add_argument("--vs", help="more looks for extra rows, comma-separated (e.g. 'raw,neutral')")
    p.add_argument("--width", type=int, default=480)
    p.add_argument("--from", dest="trim_from", type=float)
    p.add_argument("--to", dest="trim_to", type=float)
    p.add_argument("--no-levels", action="store_true")
    p = sub.add_parser("keys")
    p.add_argument("clip")
    p.add_argument("--region", help="time range where warm highlights are washed out, e.g. 12-17")
    p.add_argument("--settled", help="time range where they look right, e.g. 20-23")
    p.add_argument("--forest", help="time range to hold the shadows, e.g. 13-26")
    p.add_argument("--target", type=float, default=55, help="shadow brightness to hold (video levels 16-235)")
    p.add_argument("--max-gain", type=float, default=1.4, help="cap: more gain = more grain (the user prefers darker and clean)")
    p.add_argument("--look", help="look to grade with (default: the clip's own look file)")
    p = sub.add_parser("check")
    p.add_argument("video")
    p.add_argument("--source", help="the raw clip, to find the trees and check they're one tone")
    p.add_argument("--source-offset", type=float, default=0.0, help="clip seconds at the video's 0 s (its --from)")
    p.add_argument("--vs", help="an earlier render to compare with")
    p.add_argument("--times", help="e.g. 15,17,20 (default: five spread out)")
    p.add_argument("--crop", help="picture area w:h:x:y (default: found inside any black bars)")
    a = ap.parse_args()
    {"analyze": cmd_analyze, "stills": cmd_stills, "keys": cmd_keys, "check": cmd_check}[a.cmd](a)


if __name__ == "__main__":
    main()
