#!/usr/bin/env python3
"""A phone-friendly copy of a finished video that fits under GitHub's 100 MB file limit.

Same 4K frame and frame rate, re-encoded as HEVC (H.265, what iPhones record) in two passes at a
data rate picked from the video's length so the file lands near --target MB (default 90). Used to put
finished videos on the repo's `videos` branch, where the user downloads them from the GitHub app.

  python3 phone_copy.py 4k/<clip>_4k.mp4 /path/to/videos-branch/<name>.mp4
"""
import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_signature_video import FF  # noqa: E402


def duration(path):
    err = subprocess.run([FF, "-hide_banner", "-i", str(path)], capture_output=True, text=True).stderr
    h, m, s = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err).groups()
    return int(h) * 3600 + int(m) * 60 + float(s)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--target", type=float, default=90.0, help="target size in MB (default 90; GitHub's limit is 100)")
    a = ap.parse_args()
    secs = duration(a.src)
    kbps = int(min(40000, a.target * 8000 * 0.95 / secs))       # 5% headroom for the container
    print(f"{secs:.2f} s -> {kbps / 1000:.1f} Mbps HEVC, two passes")
    # Two passes: the first measures how much detail every frame has, the second spends the size
    # where it's needed. A single pass starts cautious and leaves the first ~0.5 s visibly soft
    # (seen on the Nov 2026 dusk clip), and can't save bits on easy shots for hard ones.
    with tempfile.TemporaryDirectory() as tmp:
        stats = str(Path(tmp) / "x265.log")
        base = [FF, "-hide_banner", "-loglevel", "error", "-stats", "-y", "-i", a.src,
                "-c:v", "libx265", "-preset", "fast", "-b:v", f"{kbps}k", "-maxrate", f"{kbps * 2}k",
                "-bufsize", f"{kbps * 3}k", "-pix_fmt", "yuv420p10le", "-tag:v", "hvc1"]   # 10-bit, like the masters
        params = "log-level=error:colorprim=bt709:transfer=bt709:colormatrix=bt709:range=limited"
        tags = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv"]
        print("pass 1 of 2")
        subprocess.run(base + ["-x265-params", f"{params}:pass=1:stats={stats}"] + tags
                       + ["-an", "-f", "null", "-"], check=True)
        print("pass 2 of 2")
        subprocess.run(base + ["-map", "0", "-x265-params", f"{params}:pass=2:stats={stats}"] + tags
                       + ["-c:a", "copy", "-movflags", "+faststart", a.dst], check=True)
    size = Path(a.dst).stat().st_size / 1e6
    print(f"{a.dst}: {size:.1f} MB")
    if size >= 100:
        sys.exit("Over GitHub's 100 MB limit: run again with a lower --target.")


if __name__ == "__main__":
    main()
