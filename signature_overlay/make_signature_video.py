#!/usr/bin/env python3
"""Hand-drawn signature over a clip: it writes on over the scene, stays bright while the
scene fades to black, then holds on black for 2 s.

Folders (all inside this script's folder):
  before\\   videos waiting to be edited (drop clips here)
  4k\\       <name>_4k.mp4     2160x3840 at the clip's own frame rate (the only output)
  looks\\    <name>.json       a video's own color look, used when that video is graded
  signature_white_transparent_2x_1.mov  the signature (used automatically)

Timing: --start is when the pen starts drawing, in seconds from the beginning of the
clip. The picture fades to black 0.5 s after the pen stops (--fade-after), over 1 s,
and the signature then holds alone on black for 2 s (--hold). Without --start, the
signature is placed so the fade begins 3 s before the clip ends.

Usually the picture is left untouched (--no-grade). When a video is graded it gets its
own look: looks\\<name>.json, made for that video's hues, plus --hues / --hue-shift for
quick changes. With no look file, the neutral base is used (contrast and color strength,
no hue preference). Looks are perceptual 3D LUTs built by look_lut.py on measured levels.
Graded renders end with a QC report; --scopes also saves waveform/vectorscope images.

Examples (on Windows, "py" instead of "python3"):
  python3 make_signature_video.py                    every video in before/ not done yet
  python3 make_signature_video.py IMG_4406.MOV --start 12.5
  python3 make_signature_video.py IMG_4406.MOV --start 12.5 --fade-after 1 --preview --sheet
  python3 make_signature_video.py /path/clip.mp4 --from 15 --to 45 --start latest --hues "orange+20,blue+10"

Grade only (no signature, fade or hold; the clip's own frame and sound; output in graded/):
  python3 make_signature_video.py IMG_4406.MOV --grade-only
  python3 make_signature_video.py drone.mp4 --grade-only --from 5 --to 30 --preview

The full recipe (and why each number is what it is) is in SIGNATURE_OVERLAY_RECIPE.md; the
general grading playbook (any video) is in GRADING_GUIDE.md.
Change the look/placement constants below only on purpose; they are the approved style.
"""
import argparse
import collections
import concurrent.futures
import ctypes
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import look_lut  # noqa: E402

BEFORE, OUT_4K, GRADED = HERE / "before", HERE / "4k", HERE / "graded"
SIGNATURE = HERE / "signature_white_transparent_2x_1.mov"
VIDEO_EXT = {".mov", ".mp4", ".m4v", ".mkv"}
PREVIEW_DIR = Path(tempfile.gettempdir()) / "signature_previews"
LUT_DIR = Path(tempfile.gettempdir()) / "signature_luts"

# ---------------------------------------------------------------- recipe constants
OUT_W, OUT_H = 2160, 3840              # master: 9:16 vertical 4K, same as the reference video
INK_BOX = (543, 1675, 1599, 2272)      # where the INK lands in the 4K master: x0, y0, x1, y1
FADE_AFTER = 0.5                       # picture starts fading this long after the pen stops
FADE_DUR = 1.0                         # length of the fade to black
HOLD_AFTER_FADE = 2.0                  # signature alone on black after the fade completes, then cut
DEFAULT_FADE_LEAD = 3.0                # no --start given: fade begins this long before the clip ends

LEVELS_PCT = (0.3, 99.7)               # black/white points measured at these luma percentiles
LEVELS_MAX_BLACK = 0.12                # never remove more than this much black lift ...
LEVELS_MIN_WHITE = 0.80                # ... or stretch highlights by more than 1/0.8 = 1.25x
LUMA = np.array([0.2126, 0.7152, 0.0722])
SCENE_CUT = 0.12                       # --shots: scene-change score that counts as a cut
MIN_SHOT = 0.5                         # --shots: cuts closer together than this are merged

TONEMAP = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
           "tonemap=hable:desat=0,zscale=t=bt709,format=gbrp16le")
# The original per-channel filter grade, kept for --legacy-grade comparisons.
_LEGACY_TONE = ("curves=master='0/0 0.06/0.045 0.25/0.25 0.5/0.56 0.75/0.82 0.92/0.95 1/1',"
                "colorbalance=rs=-0.015:bs=0.015:rh=0.02:bh=-0.015")
_LEGACY_COLOR = ("huesaturation=colors=g:saturation=0.08,huesaturation=colors=m+r:saturation=0.15,"
                 "huesaturation=colors=b+c:saturation=-0.06,vibrance=intensity=0.2")
LEGACY_LOOKS = {
    "summer": f"{_LEGACY_TONE},huesaturation=colors=y:hue=8:saturation=0.06,{_LEGACY_COLOR}",
    "fall": f"{_LEGACY_TONE},huesaturation=colors=y:hue=-6:saturation=0.08,{_LEGACY_COLOR}",
    "dusk": ("curves=master='0/0 0.08/0.055 0.25/0.24 0.45/0.51 0.65/0.77 0.85/0.94 1/1',"
             "colorbalance=rs=-0.02:bs=0.025:rh=0.04:bh=-0.03,"
             "huesaturation=colors=y:hue=-8:saturation=0.2,huesaturation=colors=r:saturation=0.25,"
             "huesaturation=colors=m:saturation=0.2,huesaturation=colors=b+c:saturation=0.15,"
             "vibrance=intensity=0.65"),
}
DENOISE = "hqdn3d=4:3:6:5"   # for looks with "denoise": pushed low light shows mottled noise in the sky
# Sharpening: cas works on RGB; unsharp needs YUV, so the chain converts in between.
SHARPEN_RGB, SHARPEN_YUV = "cas=strength=0.4", "unsharp=13:13:0.3:13:13:0.3"
VIGNETTE = "vignette=angle=0.4"
TAGS = "setparams=range=tv:colorspace=bt709:color_primaries=bt709:color_trc=bt709"
INK_Y = {"white": 235, "black": 16}
# --keep-hdr: HDR stays HDR (10-bit HEVC). White ink sits at HDR reference white (8-bit
# video-range code: 75% HLG signal, or 203 nits PQ), which matches SDR white, not peak.
HDR_TRC = {"hlg": "arib-std-b67", "pq": "smpte2084"}
HDR_INK_Y = {"hlg": 180, "pq": 143}
ROTATE = {0: "", 90: "transpose=1,", 180: "hflip,vflip,", 270: "transpose=2,"}  # clockwise
SHADOW_SIGMA, SHADOW_GAIN, SHADOW_OPACITY = 10, 1.6, 0.55   # --ink-shadow, sizes in 4K pixels
MIN_INK_CONTRAST = 3.0                 # white ink vs the picture behind it (WCAG large-text ratio)

COLOR_OUT = ["-pix_fmt", "yuv420p", "-colorspace", "bt709", "-color_primaries", "bt709",
             "-color_trc", "bt709", "-color_range", "tv"]
MASTER_CRF = 10                        # x265 quality for masters (lower = more detail kept)
# Masters are 10-bit HEVC (Main 10), standard color (Rec.709 SDR), like the drone's own files: the
# user wants 10-bit kept ("keep the 10 bit color regardless") and found the 8-bit H.264 CRF 17
# master less sharp than the original. Measured on the lake clip (Nov 2026), 3 s against a lossless
# render of the same grade; detail = fine texture kept in the most textured 20%, match = its
# correlation with the reference:
#   8-bit H.264 CRF 17 (the old master)  46 Mb/s  detail 84%  match 0.76
#   10-bit HEVC 2-pass 21 Mb/s (phone)   21 Mb/s  detail 85%  match 0.84
#   10-bit HEVC CRF 12 / 10 / 8          81 / 124 / 180 Mb/s  detail 86 / 89 / 92%  match 0.84 / 0.89 / 0.92
# CRF 10 lands at the drone's own data rate (130 Mb/s), i.e. about the original's file size.


def master_enc(crf=MASTER_CRF, lossless=False):
    params = ("log-level=error:colorprim=bt709:transfer=bt709:colormatrix=bt709:range=limited"
              + (":lossless=1" if lossless else ""))
    return ["-c:v", "libx265", "-preset", "medium", *([] if lossless else ["-crf", f"{crf:g}"]),
            "-pix_fmt", "yuv420p10le", "-tag:v", "hvc1", "-x265-params", params,
            "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
            "-c:a", "aac", "-b:a", "192k"]
PREVIEW_ENC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", *COLOR_OUT,
               "-c:a", "aac", "-b:a", "128k"]


def hdr_enc(hdr, preset, crf, extra=(), dovi=False):
    """10-bit HEVC with the HDR tags. With dovi, the clip's Dolby Vision data is carried over
    frame by frame; without it, iPhones and Instagram display the same pixels differently."""
    trc = HDR_TRC[hdr]
    dv = (["-maxrate", "60M", "-bufsize", "120M", "-dolbyvision", "1", "-strict", "unofficial"] if dovi
          else ["-dolbyvision", "0"])
    return ["-c:v", "libx265", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p10le", "-tag:v", "hvc1",
            "-x265-params", f"colorprim=bt2020:transfer={trc}:colormatrix=bt2020nc:range=limited",
            "-colorspace", "bt2020nc", "-color_primaries", "bt2020", "-color_trc", trc, "-color_range", "tv",
            *dv, *extra, "-c:a", "aac", "-b:a", "192k"]

SCOPES = ("waveform=mode=column:filter=lowpass:graticule=green:flags=numbers+dots",
          "vectorscope=mode=color2:graticule=green:flags=name")


def find_ffmpeg():
    """The bundled ffmpeg in pylib/ (imageio-ffmpeg 0.6.0: ffmpeg 7.1 on Windows, 7.0.2 on Linux).
    Pinned on purpose: the ffmpeg 9.0.2 in imageio-ffmpeg 0.8.x has a broken `unsharp` at 16-bit
    (brightens the picture and tints it magenta), which wrecks every graded render."""
    if os.environ.get("FFMPEG"):
        return os.environ["FFMPEG"]
    sys.path.insert(0, str(HERE / "pylib"))
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError):
        pass
    found = shutil.which("ffmpeg")
    if found:
        return found
    sys.exit('ffmpeg not found. Run:  python3 -m pip install --target "%s" "imageio-ffmpeg==0.6.0"'
             % (HERE / "pylib"))


def find_hdr_ffmpeg():
    """ffmpeg for untouched HDR renders, which need libx265's -dolbyvision option (ffmpeg 7.1+).
    On Linux that's the ffmpeg 9.0.2 in pylib_hdr/ (imageio-ffmpeg 0.8.1); those renders don't
    sharpen, so its unsharp bug doesn't matter there. Falls back to the main ffmpeg."""
    if os.environ.get("FFMPEG_HDR"):
        return os.environ["FFMPEG_HDR"]
    found = sorted((HERE / "pylib_hdr" / "imageio_ffmpeg" / "binaries").glob("ffmpeg-*"))
    return str(found[-1]) if found else FF


FF = find_ffmpeg()
FF_HDR = find_hdr_ffmpeg()


def ff_text(args):
    """Run ffmpeg, return (stdout, stderr) as text."""
    r = subprocess.run([FF, "-hide_banner", *args], capture_output=True)
    return r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")


def ff_frame(path, t, vf, w, h, pix="rgb48le", before_input=()):
    """One frame at time t through filter chain vf, as a numpy array (h, w, channels) in 0-1."""
    r = subprocess.run([FF, "-v", "error", *before_input, "-ss", f"{max(t, 0):.3f}", "-i", str(path),
                        "-frames:v", "1", "-vf", f"{vf},format={pix}" if vf else f"format={pix}",
                        "-f", "rawvideo", "-"], capture_output=True)
    if pix == "rgb48le":
        data = np.frombuffer(r.stdout, "<u2")[: w * h * 3]
        return data.reshape(h, w, 3) / 65535.0 if data.size == w * h * 3 else None
    data = np.frombuffer(r.stdout, np.uint8)[: w * h * 3 // 2]       # yuv420p
    if data.size < w * h * 3 // 2:
        return None
    y = data[: w * h].reshape(h, w).astype(float)
    u = data[w * h: w * h * 5 // 4].reshape(h // 2, w // 2).astype(float)
    v = data[w * h * 5 // 4:].reshape(h // 2, w // 2).astype(float)
    return y, u, v


def probe(path):
    _, err = ff_text(["-i", str(path)])
    vline = next((l for l in err.splitlines() if re.search(r"Stream #\d+:\d+.*: Video:", l)), None)
    if not vline:
        sys.exit(f"No video stream found in {path}")
    w, h = map(int, re.search(r", (\d{2,5})x(\d{2,5})", vline).groups())
    hdr = "hlg" if "arib-std-b67" in vline else "pq" if "smpte2084" in vline else None
    alpha = bool(re.search(r"\b(yuva|rgba|argb|bgra|abgr|gbrap|ya8|ya16)", vline))
    audio = None
    for n, codec in enumerate(re.findall(r"Stream #\d+:\d+.*: Audio: (\w+)", err)):
        if codec != "none":                     # iPhone spatial-audio track shows as "none"
            audio = f"0:a:{n}"
            break
    sample_bytes = 2 if re.search(r"p(9|10|12|14|16)(le|be)", vline) else 1
    dovi = "DOVI configuration record" in err      # iPhone Dolby Vision (profile 8.4 on HLG)
    return {"w": w, "h": h, "hdr": hdr, "alpha": alpha, "audio": audio, "sample_bytes": sample_bytes,
            "dovi": dovi}


def free_ram():
    """Available physical memory in bytes (Linux or Windows), or None if it can't be read."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except OSError:
        pass

    class MemoryStatus(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    *((n, ctypes.c_ulonglong) for n in (
                        "total_phys", "avail_phys", "total_page", "avail_page",
                        "total_virtual", "avail_virtual", "avail_extended"))]
    try:
        status = MemoryStatus()
        status.dwLength = ctypes.sizeof(status)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
        return status.avail_phys
    except (AttributeError, OSError):
        return None


def frame_times(path, stream="0:v:0"):
    """Exact presentation times of every frame (or audio packet) of a stream, plus its end time."""
    out, _ = ff_text(["-v", "error", "-i", str(path), "-map", stream, "-c", "copy",
                      "-f", "framemd5", "-"])
    num, den = map(int, re.search(r"#tb 0: (\d+)/(\d+)", out).groups())
    tb = num / den
    pts, ends = [], []
    for line in out.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        parts = [p.strip() for p in line.split(",")]
        p, d = int(parts[2]), int(parts[3])
        pts.append(p)
        ends.append(p + d)
    t0 = min(pts)
    return sorted((p - t0) * tb for p in pts), (max(ends) - t0) * tb


def ink_plane(sinfo):
    """Filter that turns a signature frame into an 8-bit 'how much ink' plane source."""
    return "extractplanes=a,format=gray" if sinfo["alpha"] else "format=yuv420p,extractplanes=y"


def pen_times(sig, sinfo):
    """First and last frame where the pen is moving (frame-to-frame ink change)."""
    out, _ = ff_text(["-v", "error", "-i", str(sig), "-vf",
                      ink_plane(sinfo) + ",signalstats,metadata=print:key=lavfi.signalstats.YDIF:file=-",
                      "-f", "null", "-"])
    times = re.findall(r"pts_time:([\d.]+)", out)
    diffs = re.findall(r"YDIF=([\d.e+-]+)", out)
    moving = [float(t) for t, d in zip(times, diffs) if float(d) > 0.01]
    if not moving:
        sys.exit("Could not detect pen movement in the signature clip.")
    return moving[0], moving[-1]


def ink_box(sig, sinfo):
    """Paper luma (None for transparent clips) and the ink's bounding box on the last frame."""
    w, h = sinfo["w"], sinfo["h"]
    r = subprocess.run([FF, "-v", "error", "-sseof", "-0.1", "-i", str(sig), "-frames:v", "1",
                        "-vf", ink_plane(sinfo), "-f", "rawvideo", "-"], capture_output=True)
    y = r.stdout[: w * h]
    if sinfo["alpha"]:
        paper, is_ink = None, (lambda v: v > 127)
    else:
        paper = collections.Counter(y).most_common(1)[0][0]
        is_ink = (lambda v, t=(paper + 16) // 2: v < t)
    table = bytes(1 if is_ink(v) else 0 for v in range(256))
    xs, ys = [], []
    for row in range(h):
        line = y[row * w:(row + 1) * w].translate(table)
        first = line.find(1)
        if first >= 0:
            xs += [first, line.rfind(1)]
            ys.append(row)
    if not xs:
        sys.exit("No ink found on the signature clip's last frame.")
    return paper, (min(xs), min(ys), max(xs), max(ys))


FILL_SAMPLE = ("scale=270:480:force_original_aspect_ratio=increase,crop=270:480", 270, 480)


def sample_lumas(bg, abs_times, to_rgb, sample=FILL_SAMPLE):
    """Luma of frames at these source times, framed like the output (small), after HDR conversion."""
    frame_vf, fw, fh = sample
    vf = f"{frame_vf},{to_rgb}"
    frames = (ff_frame(bg, t, vf, fw, fh) for t in abs_times)
    return [f @ LUMA for f in frames if f is not None]


def active_box(lumas, thresh=0.05):
    """The picture area inside any black bars (letterbox / pillarbox), as frame fractions
    x0, y0, x1, y1, pulled in slightly so the soft edge of a bar isn't counted."""
    if not lumas:
        return (0.0, 0.0, 1.0, 1.0)
    m = np.max(np.stack(lumas), axis=0)
    rows, cols = np.where(m.max(axis=1) > thresh)[0], np.where(m.max(axis=0) > thresh)[0]
    if rows.size == 0 or cols.size == 0:
        return (0.0, 0.0, 1.0, 1.0)
    h, w = m.shape
    pad_y, pad_x = 2 / h if rows.size < h else 0, 2 / w if cols.size < w else 0
    return (cols[0] / w + pad_x, rows[0] / h + pad_y, (cols[-1] + 1) / w - pad_x, (rows[-1] + 1) / h - pad_y)


def crop_frac(arr, box):
    h, w = arr.shape[:2]
    return arr[int(box[1] * h):int(math.ceil(box[3] * h)), int(box[0] * w):int(math.ceil(box[2] * w))]


def levels_from(lumas, box):
    """Black and white points from luma percentiles inside the picture area. Bounded so it
    corrects flat footage but never restyles (haze, fog and night stay what they are)."""
    if not lumas:
        return 0.0, 1.0, None
    lo, hi = np.percentile(np.concatenate([crop_frac(l, box).ravel() for l in lumas]), LEVELS_PCT)
    black = min(lo, LEVELS_MAX_BLACK) if lo > 0.01 else 0.0
    white = max(hi, LEVELS_MIN_WHITE) if hi < 0.97 else 1.0
    return black, white, (lo, hi)


def detect_shots(bg, trim_in, box, duration, sample=FILL_SAMPLE):
    """Cut points (section seconds) from scene-change scores on the picture area only;
    static black bars would otherwise dilute every score."""
    x0, y0, x1, y1 = box
    vf = (f"{sample[0]},"
          f"crop=iw*{x1 - x0:.4f}:ih*{y1 - y0:.4f}:iw*{x0:.4f}:ih*{y0:.4f},"
          f"select='gt(scene\\,{SCENE_CUT})',metadata=print:key=lavfi.scene_score:file=-")
    out, _ = ff_text(["-v", "error", *trim_in, "-i", str(bg), "-vf", vf, "-an", "-f", "null", "-"])
    cuts = []
    for t in (float(x) for x in re.findall(r"pts_time:([\d.]+)", out)):
        if MIN_SHOT <= t <= duration - MIN_SHOT and (not cuts or t - cuts[-1] >= MIN_SHOT):
            cuts.append(t)
    edges = [0.0, *cuts, duration]
    return list(zip(edges[:-1], edges[1:]))


def parse_shot_looks(text):
    """'10,11,13=sunset;5=hills' or '2-4=warm' -> {shot number: look name or file}."""
    mapping = {}
    for group in filter(None, (text or "").split(";")):
        nums, _, look = group.partition("=")
        look_lut.load_look(look.strip())          # fails early with a clear message if missing
        for part in nums.split(","):
            lo, _, hi = part.strip().partition("-")
            for n in range(int(lo), int(hi or lo) + 1):
                mapping[n] = look.strip()
    return mapping


def write_cube_job(job):
    return look_lut.write_cube(*job)


def forest_matte(mt, pw, ph, work_w=480):
    """Filter chain: picture (16-bit RGB, pw x ph) -> soft mask of the darker forest, same size and
    format. Darkness, blurred; then, unblurred: not sunlit-warm, not sky, and in the top of the
    picture ("top" range) only greens or dark things (backlit treetops are tinted warm, not green),
    so a tall evergreen is boosted evenly from top to bottom while the shaded rock under the peak is not. (A plain fade-in from the top split the trees in two:
    dark tops, bright lower halves.) See look_lut forest_boost."""
    d0, d1 = mt.get("dark", (0.40, 0.60))
    t0, t1 = mt.get("top") or (None, None)
    w0, w1 = mt.get("warm_bright", (0.48, 0.60))
    s0, s1 = mt.get("sky", (0.75, 0.88))
    sigma = max(0.5, mt.get("blur", 6 / 480) * work_w)
    wh = even(work_w * ph / pw)
    rgb = "st(0,r(X,Y)/65535);st(1,g(X,Y)/65535);st(2,b(X,Y)/65535);st(3,0.2126*ld(0)+0.7152*ld(1)+0.0722*ld(2));"
    q = (rgb + f"st(5,clip((ld(3)-{d0})/{d1 - d0},0,1));st(5,1-ld(5)*ld(5)*(3-2*ld(5)));65535*ld(5)")
    keep = (rgb + "st(4,clip((ld(0)-ld(2))/0.03,0,1));st(4,ld(4)*ld(4)*(3-2*ld(4)));"
            f"st(5,clip((ld(3)-{w0})/{w1 - w0},0,1));st(5,ld(5)*ld(5)*(3-2*ld(5)));"
            f"st(6,clip((ld(3)-{s0})/{s1 - s0},0,1));st(6,ld(6)*ld(6)*(3-2*ld(6)));"
            + (f"st(7,clip((Y/H-{t0})/{t1 - t0},0,1));st(7,ld(7)*ld(7)*(3-2*ld(7)));"
               "st(8,clip((ld(1)-ld(0))/0.02,0,1));st(8,ld(8)*ld(8)*(3-2*ld(8)));"
               "st(9,clip((ld(3)-0.20)/0.10,0,1));st(8,max(ld(8),1-ld(9)*ld(9)*(3-2*ld(9))));"
               "65535*clip(1-ld(4)*ld(5)-ld(6),0,1)*(ld(7)+(1-ld(7))*ld(8))" if t0 is not None else
               "65535*clip(1-ld(4)*ld(5)-ld(6),0,1)"))
    return (f"scale={work_w}:{wh}:flags=area,format=gbrp16le,split[fq][fk];"
            f"[fq]geq=r='{q}':g='{q}':b='{q}',gblur=sigma={sigma:.2f}[fq2];"
            f"[fk]geq=r='{keep}':g='{keep}':b='{keep}'[fk2];"
            f"[fq2][fk2]blend=all_mode=multiply,scale={pw}:{ph}:flags=bilinear,format=gbrp16le")


def boost_steps(look, t_from, t_to, rate=15, tol=0.005):
    """[(start, middle), ...] slices where the forest boost is constant enough for one LUT."""
    ticks = np.arange(t_from, t_to, 1.0 / rate)
    starts, last = [], None
    for t in ticks:
        v = look_lut.forest_gain_at(look, t)
        if last is None or abs(v - last) > tol:
            starts.append(float(t))
            last = v
    ends = starts[1:] + [t_to]
    return [(st, (st + en) / 2) for st, en in zip(starts, ends)]


def region_matte(mt, pw, ph, work_w=480):
    """Filter chain: picture (16-bit RGB, pw x ph) -> soft mask of the region, same size and format.
    Built at work_w wide: warm x bright x top-of-picture, blurred and scaled by gain, then times
    "not bright-and-neutral" (sky, snow) unblurred so the ridge line stays clean. See look_lut."""
    w0, w1 = mt.get("warm", (0.0, 0.03))
    b0, b1 = mt.get("bright", (0.68, 0.80))
    t0, t1 = mt.get("top", (0.30, 0.50))
    s0, s1 = mt.get("sky", (0.80, 0.90))
    sigma = max(0.5, mt.get("blur", 5 / 480) * work_w)
    gain = mt.get("gain", 1.6)
    wh = even(work_w * ph / pw)
    rgb = "st(0,r(X,Y)/65535);st(1,g(X,Y)/65535);st(2,b(X,Y)/65535);st(3,0.2126*ld(0)+0.7152*ld(1)+0.0722*ld(2));"
    warm = f"st(4,clip((ld(0)-ld(2)-{w0})/{w1 - w0},0,1));st(4,ld(4)*ld(4)*(3-2*ld(4)));"
    q = (rgb + warm + f"st(5,clip((ld(3)-{b0})/{b1 - b0},0,1));st(5,ld(5)*ld(5)*(3-2*ld(5)));"
         f"st(6,clip((Y/H-{t0})/{t1 - t0},0,1));st(6,1-ld(6)*ld(6)*(3-2*ld(6)));65535*ld(4)*ld(5)*ld(6)")
    ns = (rgb + warm + f"st(5,clip((ld(3)-{s0})/{s1 - s0},0,1));st(5,ld(5)*ld(5)*(3-2*ld(5)));"
          "65535*(1-ld(5)*(1-ld(4)))")
    return (f"scale={work_w}:{wh}:flags=area,format=gbrp16le,split[mq][mn];"
            f"[mq]geq=r='{q}':g='{q}':b='{q}',gblur=sigma={sigma:.2f},"
            f"lutrgb=r='min(val*{gain}\\,maxval)':g='min(val*{gain}\\,maxval)':b='min(val*{gain}\\,maxval)'[mq2];"
            f"[mn]geq=r='{ns}':g='{ns}':b='{ns}'[mn2];"
            f"[mq2][mn2]blend=all_mode=multiply,scale={pw}:{ph}:flags=bilinear,format=gbrp16le")


def exposure_steps(look, t_from, t_to, rate=15, tol=0.002):
    """The moments (clip seconds) where a look's time keys change enough to need their own LUT:
    checked 15 times a second, a new LUT when a setting moves by more than tol (0.2% of the
    signal range, too small to see as a step). Returns [(start, middle), ...] covering t_from to
    t_to; each LUT uses the settings at the middle of its slice."""
    ticks = np.arange(t_from, t_to, 1.0 / rate)
    starts, last = [], None
    for t in ticks:
        v = np.array(look_lut.moment(look, t))
        if last is None or np.max(np.abs(v - last)) > tol:
            starts.append(float(t))
            last = v
    ends = starts[1:] + [t_to]
    return [(st, (st + en) / 2) for st, en in zip(starts, ends)]


def even(v):
    return int(round(v / 2.0)) * 2


def strip_rotation(tmp, out, ff=None):
    # iPhone clips carry a -90 degree rotation tag that ffmpeg copies even though the
    # pixels are already upright; strip it or players show the video sideways.
    # -strict unofficial keeps the Dolby Vision configuration box; without it the remux drops it.
    subprocess.run([ff or FF, "-v", "error", "-y", "-display_rotation:v:0", "0", "-i", str(tmp),
                    "-map", "0", "-c", "copy", "-movflags", "+faststart", "-strict", "unofficial", str(out)],
                   check=True)
    tmp.unlink()


def ink_layer(sinfo, paper, offset, sw, sh, ink, shadow, k, rotate=0, tags=TAGS, ink_y=None):
    """Filter chain: signature clip -> [ink] (and [shadow] with --ink-shadow), tagged layers."""
    head = f"[1:v]setpts=PTS-STARTPTS+{offset:.4f}/TB,{ROTATE[rotate]}scale={sw}:{sh}:flags=lanczos,"
    if sinfo["alpha"]:
        # Transparent clip: use its own alpha, but snap near-zero values to 0. Converting
        # 12-bit ProRes alpha to 8-bit dithers empty areas to 1/255, which shows as a
        # faint box on black. Both layers are tagged so ffmpeg doesn't convert either.
        chain = (head + "format=yuva420p,split[c][a];"
                 "[a]extractplanes=a,lut=c0='clip((val-4)*255/251\\,0\\,255)',setparams=range=pc[al];"
                 f"[c]format=yuv420p,{tags}[c1];")
    else:
        # Black-on-white recording: key out the paper by brightness.
        chain = (head + f"format=yuv420p,{tags},split[c1][a];"
                 f"[a]lutyuv=y='clip(({paper}-val)*255/({paper}-16)\\,0\\,255)',extractplanes=y,"
                 "setparams=range=pc[al];")
    color = f"lutyuv=y={ink_y if ink_y is not None else INK_Y[ink]}:u=128:v=128"
    if not shadow:
        # HDR output is 10-bit: convert the layer directly, never through RGB.
        # Standard-color ink stays 8-bit and untagged past alphamerge (flat white, nothing to
        # lose); the overlay blends it into the 10-bit picture. Measured on the lake clip (Nov
        # 2026): converting it to 10-bit crushed the picture's blacks (64 -> 0), and retagging it
        # as video range put its white at 1023; this way black is 64 and white 940-949.
        deep = ",format=yuva420p10le" if "bt2020" in tags else ""
        return chain + f"[c1]{color}[cc];[cc][al]alphamerge{deep}[ink]"
    # A soft dark glow under the strokes, so white ink still reads over a bright sky.
    return (chain + "[c1]split[c2][c3];[al]split[al1][al2];"
            f"[c2]{color}[cc];[cc][al1]alphamerge[ink];"
            f"[al2]gblur=sigma={max(1.0, SHADOW_SIGMA * k):.1f},"
            f"lut=c0='clip(val*{SHADOW_GAIN}\\,0\\,255)*{SHADOW_OPACITY}',setparams=range=pc[sa];"
            "[c3]lutyuv=y=16:u=128:v=128[sc];[sc][sa]alphamerge[shadow]")


def picture_stats(path, times, box):
    """Luma, chroma-strength and cast of the finished picture area at these output times."""
    ys, sats, us, vs = [], [], [], []
    for t in times:
        f = ff_frame(path, t, "scale=540:960", 540, 960, pix="yuv420p")
        if f is None:
            continue
        y, u, v = (crop_frac(p, box) for p in f)
        ys.append(y.ravel())
        sats.append(np.hypot(u - 128, v - 128).ravel())
        us.append(u.mean())
        vs.append(v.mean())
    if not ys:
        return None
    return np.concatenate(ys), np.concatenate(sats).mean(), np.mean(us) - 128, np.mean(vs) - 128


def shot_table(path, segments, t_end, box):
    """One line per shot, so shots cut together can be checked for consistency."""
    lines = [" shot  start-end     look     black  median  white   sat   cast U/V"]
    for i, (a, b, look) in enumerate(segments, 1):
        if a >= t_end:
            break
        b = min(b, t_end)
        st = picture_stats(path, [a + (b - a) * f for f in (0.2, 0.5, 0.8)], box)
        if st:
            lo, mid, hi = np.percentile(st[0], [0.5, 50, 99.5])
            lines.append(f"  {i:3d}  {a:5.1f}-{b:5.1f}  {look:7s}  {lo:5.0f}  {mid:5.0f}  {hi:5.0f}  "
                         f"{st[1]:5.1f}  {st[2]:+5.1f} {st[3]:+5.1f}")
    return lines


def qc_report(path, t_end, box, samples=10):
    """Measured levels of the finished picture (before the fade), against Rec.709 targets."""
    st = picture_stats(path, [t_end * (i + 0.5) / samples for i in range(samples)], box)
    if st is None:
        return []
    y, sat, du, dv = st
    lo, mid, hi = np.percentile(y, [0.5, 50, 99.5])
    clipped, crushed = (y >= 234).mean() * 100, (y <= 17).mean() * 100

    def mark(ok):
        return "ok  " if ok else "WARN"
    return [
        f"{mark(lo <= 24)} black   (0.5%)  Y={lo:5.1f}   target <= 24 (video black is 16)",
        f"{mark(190 <= hi <= 235)} white   (99.5%) Y={hi:5.1f}   target 190-235 (scene dependent)",
        f"{mark(clipped < 1.0)} clipped         {clipped:5.2f}%  target < 1% of pixels at peak white",
        f"{mark(crushed < 5.0)} crushed         {crushed:5.2f}%  target < 5% of pixels at black",
        f"ok   midtones (median) Y={mid:5.1f}   typical 80-140; dusk and night run lower",
        f"{mark(sat < 90)} saturation      {sat:5.1f}   typical 40-80; above ~90 starts to look neon",
        f"{mark(abs(du) <= 12 and abs(dv) <= 12)} color cast      U{du:+5.1f} V{dv:+5.1f}  "
        "neutral is 0; a strong cast is fine if intended (sunsets lean V+ U-)",
    ]


def ink_contrast(path, t, k, box=INK_BOX, frame=(OUT_W, OUT_H)):
    """Contrast of white ink against the picture right under it, just before the pen starts."""
    w, h = even(frame[0] * k), even(frame[1] * k)
    f = ff_frame(path, t, "", w, h, pix="yuv420p")
    if f is None:
        return None
    y = f[0][int(box[1] * k):int(box[3] * k), int(box[0] * k):int(box[2] * k)]
    lum = look_lut.srgb_decode(np.clip((np.percentile(y, 75) - 16) / 219, 0, 1))
    return (1.0 + 0.05) / (lum + 0.05)


def scope_sheet(path, moments, out_png):
    """Frame + waveform + vectorscope for each moment, stacked (PNG keeps subtle color casts)."""
    rows = []
    for i, t in enumerate(moments):
        row = out_png.with_name(f"{out_png.stem}_{i}.png")
        subprocess.run([FF, "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
                        "-filter_complex",
                        "[0:v]format=yuv444p,split=3[f][w][v];[f]scale=270:480[f2];"
                        f"[w]{SCOPES[0]},scale=480:480[w2];[v]{SCOPES[1]},scale=480:480[v2];"
                        "[f2][w2][v2]hstack=3", str(row)], check=True)
        rows.append(row)
    subprocess.run([FF, "-v", "error", "-y", *sum((["-i", str(r)] for r in rows), []),
                    "-filter_complex", f"vstack={len(rows)}" if len(rows) > 1 else "null",
                    str(out_png)], check=True)
    for r in rows:
        r.unlink()


def render(bg, sig, a):
    k = 0.25 if a.preview else 1.0
    info = probe(bg)
    only = a.grade_only
    if only:
        # Grade only: the clip's own frame (landscape stays landscape, 4K stays 4K), no signature,
        # no fade, sound copied as is.
        if a.no_grade:
            sys.exit("--grade-only with --no-grade would change nothing.")
        FW, FH = info["w"], info["h"]
        W, H = even(FW * k), even(FH * k)
        tag = "_reversed" if a.reverse else ""
        master = (PREVIEW_DIR / f"{bg.stem}{tag}_graded_preview.mp4" if a.preview
                  else GRADED / f"{bg.stem}{tag}_graded.mp4")
    else:
        # --own-frame: the clip's own frame (a 3840x2160 shot makes a 3840x2160 video: whole
        # picture, no bars, no cropping). Otherwise the vertical 2160x3840 master.
        FW, FH = (info["w"], info["h"]) if a.own_frame else (OUT_W, OUT_H)
        if a.own_frame and a.fit:
            a.fit = False                                    # nothing to fit: the frame is the picture's
        W, H = even(FW * k), even(FH * k)
        tag = (("_reversed" if a.reverse else "") + ("_original" if a.no_grade else "")
               + ("_shadow" if a.ink_shadow else "") + ("_letterbox" if a.fit else "")
               + (("_landscape" if FW > FH else "_ownframe") if a.own_frame else ""))
        if a.preview:
            master = PREVIEW_DIR / f"{bg.stem}{tag}_preview.mp4"
        else:
            master = OUT_4K / f"{bg.stem}{tag}_4k.mp4"
    sinfo = None if only else probe(sig)
    times, bg_end = frame_times(bg)
    trim_in, notes, t0 = [], [], 0.0
    if a.trim_from is not None or a.trim_to is not None:
        t0 = a.trim_from or 0.0
        t1 = bg_end if a.trim_to is None else min(a.trim_to, bg_end)
        if not 0 <= t0 < t1:
            sys.exit(f"--from/--to is outside the clip (0 to {bg_end:.2f} s).")
        if a.trim_to is not None and a.trim_to > bg_end:
            notes.append(f"--to {a.trim_to:g} is past the clip's end; using {bg_end:.2f} s")
        times = [t - t0 for t in times if t0 <= t < t1]
        bg_end = t1 - t0
        trim_in = ["-ss", f"{t0:.4f}", "-to", f"{t1:.4f}"]
    if a.reverse:
        # ffmpeg's reverse filter holds every decoded frame of the section in memory.
        need = len(times) * info["w"] * info["h"] * 1.5 * info["sample_bytes"]
        avail = free_ram()
        if avail and need > 0.85 * avail:
            sys.exit(f"Reversing needs about {need / 1e9:.1f} GB of memory but only {avail / 1e9:.1f} GB "
                     "is free. Close other apps or use a shorter section with --from/--to.")
        notes.append(f"reversing holds {len(times)} frames in memory, about {need / 1e9:.1f} GB"
                     + (f" ({avail / 1e9:.1f} GB free)" if avail else ""))
    fps = len(times) / bg_end
    if only:
        pen_start = pen_stop = 0.0
        paper, (ix0, iy0, ix1, iy1) = None, (0, 0, 1, 1)
    else:
        pen_start, pen_stop = pen_times(sig, sinfo)
        paper, (ix0, iy0, ix1, iy1) = ink_box(sig, sinfo)

    # Timing. "start" = when ink first appears on screen, in background-clip seconds.
    draw = pen_stop - pen_start
    if only:
        start = 0.0
    elif a.start is None:
        start = bg_end - DEFAULT_FADE_LEAD - a.fade_after - draw
    elif a.start == "latest":
        # As late as possible: the fade reaches black on the clip's last frame.
        start = times[-1] - a.fade_dur - a.fade_after - draw
    else:
        start = float(a.start)
    if not only and not 0 <= start < bg_end:
        sys.exit(f"--start {start:.2f} is outside the clip (0 to {bg_end:.2f} s).")
    sig_offset = start - pen_start
    pen_done = start + draw
    fade_start = pen_done + a.fade_after
    total = bg_end if only else fade_start + a.fade_dur + a.hold
    pad_frames = 0 if only else max(0, round((total - bg_end) * fps))
    if not only and fade_start + a.fade_dur > bg_end:
        notes.append(f"clip ends at {bg_end:.2f} s, before the fade finishes; the rest cuts to black")
    # --keep-audio: the music is copied as-is (no fade, no re-encode) and simply stops where
    # the video ends, after the 2 s hold. The hold is never stretched to fit the music.
    keep_audio = (a.keep_audio or only) and bool(info["audio"])

    # Placement: turn the signature if asked (its ink box turns with it), then scale it so its
    # ink matches the target box exactly. --ink-box replaces the standard box.
    rot = a.sig_rotate % 360
    cw, ch = (1, 1) if only else (sinfo["w"], sinfo["h"])
    if rot == 90:
        ix0, iy0, ix1, iy1, cw, ch = ch - 1 - iy1, ix0, ch - 1 - iy0, ix1, ch, cw
    elif rot == 180:
        ix0, iy0, ix1, iy1 = cw - 1 - ix1, ch - 1 - iy1, cw - 1 - ix0, ch - 1 - iy0
    elif rot == 270:
        ix0, iy0, ix1, iy1, cw, ch = iy0, cw - 1 - ix1, iy1, cw - 1 - ix0, ch, cw
    box_4k = a.ink_box or INK_BOX
    fit_w4 = min(OUT_W, OUT_H * info["w"] / info["h"])          # the picture's size in the 4K frame
    fit_h4 = min(OUT_H, OUT_W * info["h"] / info["w"])
    if a.own_frame and not a.ink_box and not only:
        # Own frame: the same rule as --fit. The ink is 48.9% of the picture's short side wide
        # (1056 px on a 3840x2160 shot) and sits at the exact center of the frame.
        iw4 = (INK_BOX[2] - INK_BOX[0]) / OUT_W * min(FW, FH)
        ih4 = iw4 * (iy1 - iy0) / (ix1 - ix0)
        cx, cy = FW / 2, FH / 2
        box_4k = (round(cx - iw4 / 2), round(cy - ih4 / 2), round(cx + iw4 / 2), round(cy + ih4 / 2))
    elif a.fit and not a.ink_box and min(fit_w4 / OUT_W, fit_h4 / OUT_H) < 0.99:
        # Whole-picture framing: the signature shrinks with the picture. Its ink keeps the usual
        # width relative to the picture's short side (1056 of 2160 px = 48.9%, as on a vertical
        # video), and sits at the exact center of the picture.
        iw4 = (INK_BOX[2] - INK_BOX[0]) / OUT_W * min(fit_w4, fit_h4)
        ih4 = iw4 * (iy1 - iy0) / (ix1 - ix0)
        cx, cy = OUT_W / 2, OUT_H / 2
        box_4k = (round(cx - iw4 / 2), round(cy - ih4 / 2), round(cx + iw4 / 2), round(cy + ih4 / 2))
    s = (box_4k[2] - box_4k[0]) * k / (ix1 - ix0)
    sw, sh = even(cw * s), even(ch * s)
    ox, oy = math.floor(box_4k[0] * k - ix0 * s), math.floor(box_4k[1] * k - iy0 * s)

    # --no-grade means untouched: an HDR clip stays HDR (10-bit, Dolby Vision kept), with no
    # conversion, scaling or color change of any kind. Only the signature and fade are added.
    if a.keep_hdr and not a.no_grade:
        sys.exit("--keep-hdr works with --no-grade only (the looks are built for standard color).")
    hdr_out = info["hdr"] if a.no_grade else None
    native = bool(hdr_out) and not a.preview and (info["w"], info["h"]) == (W, H)
    out_tags = (f"setparams=range=tv:colorspace=bt2020nc:color_primaries=bt2020:color_trc={HDR_TRC[hdr_out]}"
                if hdr_out else TAGS)

    # Grade: primary (measured levels) first, then the creative look, as one perceptual LUT
    # per shot. Everything is measured inside the picture area, so black bars don't count.
    to_rgb = TONEMAP if info["hdr"] else "format=gbrp16le"
    # Framing: fill the 9:16 frame (crop what doesn't fit), or with --fit show the whole picture
    # with black bars. Measurements follow the framing, so they see what the viewer sees.
    if only or a.own_frame:
        sample = (f"scale=480:{even(480 * info['h'] / info['w'])}", 480, even(480 * info["h"] / info["w"]))
        pw, ph = W, H
        place, pad = f"scale={W}:{H}:flags=lanczos", ""
    elif a.fit:
        sample = (f"scale=480:{even(480 * info['h'] / info['w'])}", 480, even(480 * info["h"] / info["w"]))
        ph = min(H, even(W * info["h"] / info["w"]))
        pw = W if ph < H else even(H * info["w"] / info["h"])
        place, pad = f"scale={pw}:{ph}:flags=lanczos", f",pad={W}:{H}:{(W - pw) // 2}:{(H - ph) // 2}:color=black"
    else:
        sample = FILL_SAMPLE
        pw, ph = W, H
        place, pad = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}", ""
        if abs(info["w"] / info["h"] - W / H) > 0.05:
            notes.append(f"the clip is {info['w']}x{info['h']}, not 9:16: filling the frame crops "
                         + ("its sides" if info["w"] / info["h"] > W / H else "its top and bottom")
                         + "; --fit shows the whole picture with black bars instead")
    overview = sample_lumas(bg, [t0 + bg_end * (i + 0.5) / 12 for i in range(12)], to_rgb, sample)
    box = active_box(overview)
    # The picture area in the finished frame (for the QC report): with --fit, inside the added bars.
    fx0, fy0 = (W - pw) / 2 / W, (H - ph) / 2 / H
    qc_box = (fx0 + box[0] * pw / W, fy0 + box[1] * ph / H, fx0 + box[2] * pw / W, fy0 + box[3] * ph / H)
    if box != (0.0, 0.0, 1.0, 1.0):
        notes.append(f"picture sits inside black bars (x {box[0]:.2f}-{box[2]:.2f}, y {box[1]:.2f}-{box[3]:.2f} "
                     "of the frame); bars kept, measurements use the picture only")
    own_look = HERE / "looks" / f"{bg.stem}.json"
    base_look = a.look or (bg.stem if own_look.exists() else "neutral")
    shot_looks = parse_shot_looks(a.shot_looks)
    graded = not (a.legacy_grade or a.no_grade)
    shots = detect_shots(bg, trim_in, box, bg_end, sample) if a.shots and graded else [(0.0, bg_end)]
    segments = [(sa, sb, shot_looks.get(i, base_look)) for i, (sa, sb) in enumerate(shots, 1)]
    multi = len(segments) > 1

    def window(sa, sb):
        """A shot's span on the output timeline (mirrored when the footage is reversed)."""
        return (bg_end - sb, bg_end - sa) if a.reverse else (sa, sb)

    def enable(spans):
        return "enable='" + "+".join(f"gte(t\\,{wa:.4f})*lt(t\\,{wb:.4f})" for wa, wb in spans) + "'"

    shot_lines, noisy = [], []
    look_sharpen = None
    if a.no_grade:
        grade, grade_note = "", "none, original picture" + (", Dolby Vision kept" if info["dovi"] else "")
    elif a.legacy_grade:
        if base_look not in LEGACY_LOOKS:
            sys.exit(f"--legacy-grade only has the old example looks: {', '.join(LEGACY_LOOKS)}")
        grade, grade_note = LEGACY_LOOKS[base_look], "legacy per-channel filters"
    else:
        LUT_DIR.mkdir(parents=True, exist_ok=True)
        luts, region_luts, region_spec, boost_luts, boost_spec = [], [], None, [], None
        for i, (sa, sb, look) in enumerate(segments, 1):
            lumas = (sample_lumas(bg, [t0 + sa + (sb - sa) * f for f in (0.15, 0.4, 0.65, 0.9)], to_rgb, sample)
                     if multi else overview)
            black, white, measured = (0.0, 1.0, None) if a.no_levels else levels_from(lumas, box)
            spec = look_lut.adjust(look_lut.load_look(look), a.hues, a.hue_shift, a.strength)
            look_sharpen = spec.get("sharpen")
            name = re.sub(r"[^A-Za-z0-9_.-]", "_",
                          f"{bg.stem}{tag}" + (f"_s{i:02d}" if multi else "") + f"_{Path(look).stem}.cube")
            if look_lut.has_time_keys(spec):
                if multi or a.reverse:
                    sys.exit("A look with time keys works on a single shot, played forward.")
                steps = exposure_steps(spec, t0, t0 + bg_end)
                jobs = [(LUT_DIR / f"{Path(name).stem}_x{j:03d}.cube", spec, black, white, 65, mid)
                        for j, (_, mid) in enumerate(steps)]
                # A region grade needs a second LUT per moment; where it changes nothing, reuse the first.
                rjobs = []
                if spec.get("region_grade"):
                    for j, (_, mid) in enumerate(steps):
                        if look_lut.region_at(spec, mid) == (0.0, 1.0, 0.0):
                            rjobs.append((jobs[j][0],))
                        else:
                            rjobs.append((LUT_DIR / f"{Path(name).stem}_r{j:03d}.cube", spec, black, white, 65, mid, True))
                if not a.dry_run:                  # a dry run only prints the plan
                    with concurrent.futures.ProcessPoolExecutor() as pool:
                        list(pool.map(write_cube_job, jobs + [r for r in rjobs if len(r) > 1]))
                # One LUT per moment, each switched on only for its own slice of the timeline.
                bounds = [st - t0 for st, _ in steps[1:]] + [None]
                start_s = 0.0
                for j, ((path, *_), end_s) in enumerate(zip(jobs, bounds)):
                    on = f"gte(t\\,{start_s:.4f})" + (f"*lt(t\\,{end_s:.4f})" if end_s is not None else "")
                    luts.append(f"lut3d=file={path.name}:interp=tetrahedral:enable='{on}'")
                    if rjobs:
                        region_luts.append(f"lut3d=file={rjobs[j][0].name}:interp=tetrahedral:enable='{on}'")
                    start_s = end_s
                if spec.get("forest_boost"):
                    fb = spec["forest_boost"]
                    bsteps = boost_steps(spec, t0, t0 + bg_end)
                    bb = [st - t0 for st, _ in bsteps[1:]] + [None]
                    start_b = 0.0
                    for j, ((_, mid), end_b) in enumerate(zip(bsteps, bb)):
                        gain = look_lut.forest_gain_at(spec, mid)
                        bpath = LUT_DIR / f"{Path(name).stem}_b{j:03d}.cube"
                        look_lut.write_boost_cube(bpath, gain, fb.get("green_extra", 1.5), dark_taper=fb.get("dark_taper"))
                        on = f"gte(t\\,{start_b:.4f})" + (f"*lt(t\\,{end_b:.4f})" if end_b is not None else "")
                        boost_luts.append(f"lut3d=file={bpath.name}:interp=tetrahedral:enable='{on}'")
                        start_b = end_b
                    boost_spec = fb
                    notes.append(f"forest brightened through a mask ({len(bsteps)} boost steps)")
                if rjobs:
                    region_spec = spec["region_grade"]
                    notes.append(f"{spec.get('region_grade', {}).get('name', 'region')} graded separately through a mask "
                                 f"({sum(len(r) > 1 for r in rjobs)} extra LUTs)")
                notes.append(f"grade changes over time: {len(steps)} steps between "
                             f"{steps[1][0] - t0 if len(steps) > 1 else 0:.2f} and {steps[-1][0] - t0:.2f} s")
                levels = (f"levels {black:.3f}-{white:.3f}"
                          + (f" (measured {measured[0]:.3f}-{measured[1]:.3f})" if measured else " (off)"))
                shot_lines.append(f"shot {i:2d}  {sa:6.2f}-{sb:6.2f} s  {look:6s}  {levels}")
                if spec.get("denoise"):
                    noisy.append(window(sa, sb))
                continue
            if not a.dry_run:
                look_lut.write_cube(LUT_DIR / name, spec, black, white)
            if spec.get("denoise"):
                noisy.append(window(sa, sb))
            luts.append(f"lut3d=file={name}:interp=tetrahedral" + (":" + enable([window(sa, sb)]) if multi else ""))
            levels = (f"levels {black:.3f}-{white:.3f}"
                      + (f" (measured {measured[0]:.3f}-{measured[1]:.3f})" if measured else " (off)"))
            shot_lines.append(f"shot {i:2d}  {sa:6.2f}-{sb:6.2f} s  {look:6s}  {levels}")
        grade = ",".join(luts)
        grade_note = f"{len(segments)} shots, perceptual LUT per shot" if multi else f"perceptual LUT, {levels}"
        if not multi:
            shot_lines = []
    if not noisy:
        denoise = ""
    elif multi:
        denoise = f"{DENOISE}:{enable(noisy)},"
    else:
        denoise = f"{DENOISE},"
    # Reverse only the footage, before the fade and signature, so the video still opens
    # at full brightness and ends with the signature and fade playing forward.
    rev = "reverse," if a.reverse else ""
    # RGB -> 16-bit YUV with the color space stated (untagged clips otherwise fail with
    # "no path between colorspaces"), then dither down to 8-bit.
    to_yuv = "zscale=pin=bt709:tin=bt709:p=bt709:t=bt709:m=bt709:r=tv"
    if not graded:
        region_luts, boost_luts = [], []
    if hdr_out:
        picture = f"format=yuv420p10le,{out_tags}"
    elif a.no_grade:
        picture = f"{to_rgb},{to_yuv}:dither=error_diffusion,format=yuv420p10le,{TAGS}"
    else:
        head = to_rgb
        if boost_luts:
            # Brighten the forest like an exposure increase, only inside its mask, before the grade.
            dn = f"{boost_spec['denoise']}," if boost_spec.get("denoise") else ""
            head = (f"{to_rgb},split=3[fo][fb][fm];[fb]{dn}{','.join(boost_luts)}[fb2];"
                    f"[fm]{forest_matte(boost_spec.get('matte', {}), pw, ph)}[fm2];[fo][fb2][fm2]maskedmerge")
        if region_luts:
            # Two grades of the same frame, blended through a soft mask of the region.
            graded_rgb = (f"{head},split=3[rga][rgb][rgm];[rga]{grade}[rga2];[rgb]{','.join(region_luts)}[rgb2];"
                          f"[rgm]{region_matte(region_spec.get('matte', {}), pw, ph)}[rgm2];[rga2][rgb2][rgm2]maskedmerge")
        else:
            graded_rgb = f"{head},{grade}"
        sharp = float(look_sharpen) if look_sharpen is not None else 1.0
        sharpen_rgb = f"cas=strength={0.4 * sharp:.3f}" if sharp > 0 else "null"
        sharpen_yuv = (f"unsharp=13:13:{0.3 * sharp:.3f}:13:13:{0.3 * sharp:.3f}" if sharp > 0 else "null")
        picture = (f"{graded_rgb},{sharpen_rgb},{to_yuv},format=yuv444p16le,{sharpen_yuv},"
                   f"zscale=dither=error_diffusion,format=yuv420p10le,{TAGS},{VIGNETTE}")
    # Untouched HDR frames go straight to the fade: no scaling, format change or retagging,
    # which would also strip the per-frame Dolby Vision data.
    prep = "" if native else f"{place},{picture}{pad},"
    if only:
        fc = f"[0:v]{rev}{denoise}{prep}null[vm]"
    else:
        fc = (f"[0:v]{rev}{denoise}{prep}fade=t=out:st={fade_start:.4f}:d={a.fade_dur},"
              f"tpad=stop={pad_frames}:stop_mode=add:color=black[bg];"
              + ink_layer(sinfo, paper, sig_offset, sw, sh, a.ink, a.ink_shadow, k, rot, out_tags,
                          HDR_INK_Y[hdr_out] if hdr_out and a.ink == "white" else None) + ";")
        if a.ink_shadow:
            fc += f"[bg][shadow]overlay={ox}:{oy}:eof_action=repeat[bgs];[bgs]"
        else:
            fc += "[bg]"
        fc += f"[ink]overlay={ox}:{oy}:eof_action=repeat:format=yuv420p10,format=yuv420p10le"
        fc += "[vm]"
    has_audio = bool(info["audio"])
    if has_audio and not keep_audio:
        fc += (f";[{info['audio']}]{'areverse,' if a.reverse else ''}"
               f"afade=t=out:st={fade_start:.4f}:d={a.fade_dur},"
               f"apad=whole_dur={total:.4f}[am]")
    if keep_audio:
        audio_m = ["-map", info["audio"], "-c:a", "copy"]   # untouched, bit for bit
    else:
        audio_m = ["-map", "[am]"] if has_audio else []

    tmp_m = master.with_suffix(".tmp.mp4")
    ff = FF_HDR if hdr_out else FF
    cmd = [ff, "-hide_banner", "-loglevel", "warning", "-stats", "-y",
           *trim_in, "-i", str(bg), *([] if only else ["-i", str(sig)]), "-filter_complex", fc,
           "-map", "[vm]",
           *(hdr_enc(hdr_out, "ultrafast" if a.preview else "medium", 24 if a.preview else 16,
                     dovi=info["dovi"] and native) if hdr_out
             else PREVIEW_ENC if a.preview else master_enc(a.crf, a.lossless)), *audio_m,
           "-t", f"{total:.4f}", str(tmp_m)]

    print(f"\n=== {bg.name}")
    used = f"  (using {trim_in[1]}-{trim_in[3]} s of the clip)" if trim_in else ""
    print(f"background : {info['w']}x{info['h']}  {fps:.3f} fps  {bg_end:.3f} s{used}  "
          f"hdr={info['hdr']}  audio={info['audio']}")
    if only:
        print(f"mode       : grade only, {W}x{H}, whole {'section' if trim_in else 'clip'} ({total:.2f} s), "
              f"no signature or fade, sound {'copied as is' if keep_audio else 'none'}")
    else:
        print(f"signature  : {sig.name}  {sinfo['w']}x{sinfo['h']}  transparent={sinfo['alpha']}  "
              f"draws in {draw:.2f} s  ink box {ix0},{iy0}-{ix1},{iy1}")
        how = " (default)" if a.start is None else " (latest)" if a.start == "latest" else ""
        print(f"timing     : ink appears {start:.2f} s{how}, "
              f"pen stops {pen_done:.2f} s, fade {fade_start:.2f}-{fade_start + a.fade_dur:.2f} s, "
              f"black hold to {total:.2f} s")
        print(f"placement  : signature scaled to {sw}x{sh} at x={ox} y={oy} in {W}x{H}"
              + (f", turned {rot} deg clockwise" if rot else "") + ("  + soft shadow" if a.ink_shadow else "")
              + (f"  (ink box {','.join(map(str, box_4k))})" if tuple(box_4k) != INK_BOX else ""))
    tweaks = "".join(f" {k} {v}" for k, v in (("strength", a.strength), ("hues", a.hues),
                                                ("hue-shift", a.hue_shift)) if v is not None)
    print("look       : " + (grade_note if a.no_grade else f"{base_look}{tweaks}, {grade_note}")
          + (f"; output stays HDR ({hdr_out.upper()}, 10-bit HEVC)" if hdr_out else ""))
    for line in shot_lines:
        print(f"             {line}")
    for n in notes:
        print(f"note       : {n}")
    print(f"output     : {master}")
    if a.dry_run:
        print("\n" + subprocess.list2cmdline(cmd))
        return

    master.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(cmd, check=True, cwd=LUT_DIR if LUT_DIR.exists() else None)
    strip_rotation(tmp_m, master, ff)
    print("done")

    # Quality control: numbers first, eyes second (check the sheet / scopes too).
    check, rate, ck = master, fps, k
    if hdr_out:
        print("\nQC skipped: its targets are for standard-color video, and this output is HDR.")
    else:
        qc_end = total if only else fade_start
        print("\nQC (" + ("whole picture" if only else "picture before the fade")
              + (", inside the black bars" if qc_box != (0.0, 0.0, 1.0, 1.0) else "") + "):")
        for line in qc_report(check, qc_end, qc_box):
            print("  " + line)
    if multi and not hdr_out:
        print("\nPer shot (luma is video range, black 16 / white 235):")
        out_shots = sorted((*window(sa, sb), look) for sa, sb, look in segments)
        for line in shot_table(check, out_shots, total if only else fade_start, qc_box):
            print("  " + line)
    contrast = None if (hdr_out or rot or only) else ink_contrast(check, max(start - 0.1, 0), ck, box_4k, (FW, FH))
    if contrast is not None:
        verdict = "ok  " if contrast >= MIN_INK_CONTRAST or a.ink_shadow else "WARN"
        print(f"  {verdict} signature contrast {contrast:4.1f}:1 against the picture behind it "
              f"(target >= {MIN_INK_CONTRAST:g}:1)"
              + ("" if verdict == "ok  " else "; consider --ink-shadow or a different --start"))

    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    moments = ([total * (i + 0.5) / 6 for i in range(6)] if only else
               [1.0, start + draw / 2, pen_done + 0.02, fade_start + a.fade_dur / 2,
                fade_start + a.fade_dur + 0.5, total - 0.25])
    if a.sheet:
        sheet = PREVIEW_DIR / f"{bg.stem}{tag}_check.jpg"
        sel = "+".join(f"eq(n\\,{round(t * rate)})" for t in moments)
        subprocess.run([FF, "-v", "error", "-y", "-i", str(check), "-vf",
                        f"select='{sel}',scale=-2:640,tile={len(moments)}x1",
                        "-frames:v", "1", "-q:v", "3", str(sheet)], check=True)
        print(f"check sheet: {sheet}  " + ("(six moments spread across the clip)" if only else
                                              "(1 s, mid-writing, pen stopped, mid-fade, black hold, end)"))
    if a.scopes:
        scopes = PREVIEW_DIR / f"{bg.stem}{tag}_scopes.png"
        scope_sheet(check, [1.0, total / 2, max(total - 1, 0)] if only else [1.0, fade_start / 2, max(start - 0.1, 0)], scopes)
        print(f"scopes     : {scopes}  (frame | waveform | vectorscope at 1 s, middle, before the pen)")


def start_arg(text):
    if text.lower() == "latest":
        return "latest"
    try:
        return float(text)
    except ValueError:
        raise argparse.ArgumentTypeError("use a number of seconds or 'latest'")


def ink_box_arg(text):
    try:
        box = tuple(int(v) for v in text.split(","))
    except ValueError:
        box = ()
    if len(box) != 4 or box[2] <= box[0] or box[3] <= box[1]:
        raise argparse.ArgumentTypeError("use four whole numbers x0,y0,x1,y1 with x1 > x0 and y1 > y0")
    return box


def resolve_video(name):
    p = Path(name)
    if p.exists():
        return p.resolve()
    if (BEFORE / name).exists():
        return BEFORE / name
    sys.exit(f"Can't find {name} (looked as given and in {BEFORE}).")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("videos", nargs="*", help="clip names in before\\ (or full paths); none = all not done yet")
    ap.add_argument("--start", type=start_arg,
                    help="seconds into the clip when the signature starts drawing, or 'latest' "
                         "(as late as possible: the fade finishes as the clip ends)")
    ap.add_argument("--fade-after", type=float, default=FADE_AFTER,
                    help=f"seconds after the pen stops that the fade begins (default {FADE_AFTER})")
    ap.add_argument("--fade-dur", type=float, default=FADE_DUR, help=f"fade length (default {FADE_DUR})")
    ap.add_argument("--hold", type=float, default=HOLD_AFTER_FADE,
                    help=f"signature-on-black time after the fade (default {HOLD_AFTER_FADE})")
    ap.add_argument("--signature", default=str(SIGNATURE), help="signature clip (default: the one in this folder)")
    ap.add_argument("--from", dest="trim_from", type=float, help="use the clip only from this second on")
    ap.add_argument("--to", dest="trim_to", type=float, help="use the clip only up to this second")
    ap.add_argument("--crf", type=float, default=MASTER_CRF,
                    help=f"master quality, 10-bit HEVC (default {MASTER_CRF}; lower keeps more detail, bigger file)")
    ap.add_argument("--lossless", action="store_true",
                    help="lossless 10-bit master (huge; a reference for measuring what encoding loses)")
    ap.add_argument("--own-frame", action="store_true",
                    help="keep the clip's own frame: a horizontal clip makes a horizontal 4K video (whole "
                         "picture, no bars, no cropping); the signature's ink is 48.9%% of the short side, centered")
    ap.add_argument("--fit", action="store_true",
                    help="show the whole picture: a horizontal clip sits inside the vertical frame with black "
                         "bars above and below, instead of filling the frame and cropping its sides")
    ap.add_argument("--reverse", action="store_true",
                    help="play the footage backward; the signature and fade still play forward at the end")
    ap.add_argument("--look", help="a look in looks\\ (name), an example in looks\\examples\\, or a .json "
                                   "path. Default: looks\\<video name>.json if it exists, else 'neutral'")
    ap.add_argument("--hues", metavar="LIST",
                    help="make named hues stronger/weaker for this video, e.g. 'orange+20,blue+10,green-15' "
                         "(percent; hues: " + ", ".join(look_lut.HUES) + ")")
    ap.add_argument("--hue-shift", metavar="LIST",
                    help="turn named hues by degrees for this video, e.g. 'yellow-8,green+5' "
                         "(minus = toward red/orange, plus = toward green/blue)")
    ap.add_argument("--strength", type=float, metavar="0-1",
                    help="how much of the look to use, e.g. 0.7 for a subtle grade (default: the "
                         "look's own 'strength', else 1). --hues / --hue-shift are added at full strength")
    ap.add_argument("--shots", action="store_true",
                    help="detect the cuts in an edited clip and grade each shot on its own (levels per shot)")
    ap.add_argument("--shot-looks", metavar="MAP",
                    help="looks for particular shots with --shots, e.g. '10,11,13=sunset;2-4=hills' "
                         "(look names or files); other shots use the video's look")
    ap.add_argument("--legacy-grade", action="store_true",
                    help="use the original per-channel ffmpeg filter grade instead of the perceptual LUT")
    ap.add_argument("--no-levels", action="store_true", help="skip the measured black/white levels")
    ap.add_argument("--sig-rotate", type=int, choices=[0, 90, 180, 270], default=0,
                    help="turn the signature clockwise by this many degrees (e.g. for footage stored sideways)")
    ap.add_argument("--ink-box", type=ink_box_arg, metavar="X0,Y0,X1,Y1",
                    help="where the ink lands in the 4K frame, replacing the standard box")
    ap.add_argument("--keep-hdr", action="store_true",
                    help="no longer needed: --no-grade always keeps an HDR clip HDR, Dolby Vision included")
    ap.add_argument("--keep-audio", action="store_true",
                    help="leave the music untouched: copied as-is with no fade; it stops where the video ends")
    ap.add_argument("--grade-only", action="store_true",
                    help="color grade the clip and nothing else: no signature, fade or black hold; the clip's "
                         "own frame (landscape stays landscape) and sound; output in graded\\")
    ap.add_argument("--no-grade", action="store_true",
                    help="original picture, only the signature and fade added (outputs end in _original)")
    ap.add_argument("--ink", choices=INK_Y, default="white")
    ap.add_argument("--ink-shadow", action="store_true",
                    help="soft dark glow under the signature, for bright backgrounds")
    ap.add_argument("--preview", action="store_true", help="fast 540x960 draft in the temp folder")
    ap.add_argument("--sheet", action="store_true", help="also save a strip of key frames for checking")
    ap.add_argument("--scopes", action="store_true", help="also save waveform + vectorscope images")
    ap.add_argument("--force", action="store_true", help="redo clips in before\\ that already have outputs")
    ap.add_argument("--dry-run", action="store_true", help="print the plan and ffmpeg command, render nothing")
    a = ap.parse_args()

    sig = Path(a.signature).resolve()
    if not sig.exists() and not a.grade_only:
        sys.exit(f"Signature clip not found: {sig}")
    if a.videos:
        clips = [resolve_video(v) for v in a.videos]
    else:
        clips = sorted(p for p in BEFORE.iterdir() if p.suffix.lower() in VIDEO_EXT)
        if not a.force:
            done = (lambda p: (GRADED / f"{p.stem}_graded.mp4").exists()) if a.grade_only else \
                   (lambda p: (OUT_4K / f"{p.stem}_4k.mp4").exists())
            clips = [p for p in clips if not done(p)]
        if not clips:
            sys.exit(f"Nothing to do: no unedited videos in {BEFORE}.")
    if len(clips) > 1 and (a.start is not None or a.trim_from is not None or a.trim_to is not None
                           or a.reverse):
        sys.exit("--start, --from, --to and --reverse apply to one video at a time; name the video.")
    for clip in clips:
        render(clip, sig, a)


if __name__ == "__main__":
    main()

