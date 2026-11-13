#!/usr/bin/env python3
"""Download DJI's official D-Log M to Rec.709 conversion (a .cube 3D LUT) into luts/.

Used as the first step of a grade (look key "input_lut"): the camera's own, by-the-numbers log
conversion, with the creative look built on top. DJI's files aren't committed (they're DJI's);
this fetches them from DJI and checks they're the exact file that was tested.
Needs network access to www.dji.com and terra-1-g.djicdn.com (the "clauderemotestuff" cloud
environment allows both).

  python3 get_dji_lut.py            # the DJI Flip file (also covers clips whose encoder tag is "DJI FLIP")
"""
import hashlib
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
LUTS = {
    # DJI Flip D-Log M to Rec.709, v1.0, released 2025-01-14 (www.dji.com/downloads/products/flip).
    # Its header says "Mavic 3 Pro, D-Log M, 2023-03-24": the same D-Log M curve. 33-point, output 0-1.
    "dji_flip_dlogm_to_rec709.cube": (
        "https://terra-1-g.djicdn.com/851d20f7b9f64838a34cd02351370894/141%20LUT/"
        "DJI%20Flip%20D-Log%20M%20to%20Rec.709%20V1_.cube",
        "b18162854ab47702068410c33afa98a8cb6eef159fc5a04ce0e65fad0fd8947e"),
}


def main():
    out_dir = HERE / "luts"
    out_dir.mkdir(exist_ok=True)
    for name, (url, sha) in LUTS.items():
        dest = out_dir / name
        if dest.exists() and hashlib.sha256(dest.read_bytes()).hexdigest() == sha:
            print(f"{dest} already here")
            continue
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        data = urllib.request.urlopen(req, timeout=120).read()
        got = hashlib.sha256(data).hexdigest()
        if got != sha:
            sys.exit(f"{name}: DJI's file changed (sha256 {got}); check it before using it.")
        dest.write_bytes(data)
        print(f"saved {dest} ({len(data) / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
