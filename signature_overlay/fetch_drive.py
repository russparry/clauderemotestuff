#!/usr/bin/env python3
"""Download videos from Google Drive share links into before/ (or another folder).

The files (or a folder) must be shared as "Anyone with the link". Works with links like
  https://drive.google.com/file/d/<id>/view?usp=sharing
  https://drive.google.com/open?id=<id>
  https://drive.google.com/drive/folders/<id>          (every file in the folder)

Examples:
  python3 fetch_drive.py "https://drive.google.com/file/d/abc/view?usp=sharing"
  python3 fetch_drive.py "https://drive.google.com/drive/folders/xyz" --to before
  python3 fetch_drive.py <link> --to . --name signature_white_transparent_2x_1.mov

Needs network access to drive.google.com and drive.usercontent.google.com.
"""
import argparse
import html
import os
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
UA = "Mozilla/5.0 (X11; Linux x86_64) signature-overlay-fetch/1.0"
CHUNK = 1 << 20


def _context():
    cafile = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
    return ssl.create_default_context(cafile=cafile) if cafile else ssl.create_default_context()


CTX = _context()


def _open(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    return urllib.request.urlopen(req, context=CTX, timeout=60)


def parse_link(link):
    """('file', id) or ('folder', id) from any common Drive link, or a bare id."""
    m = re.search(r"/folders/([\w-]{10,})", link)
    if m:
        return "folder", m.group(1)
    m = re.search(r"/file/d/([\w-]{10,})", link) or re.search(r"[?&]id=([\w-]{10,})", link)
    if m:
        return "file", m.group(1)
    if re.fullmatch(r"[\w-]{20,}", link.strip()):
        return "file", link.strip()
    sys.exit(f"Not a Google Drive file or folder link: {link}")


def folder_files(folder_id):
    """(file id, name) for each file in a shared folder (one level, no subfolders)."""
    with _open(f"https://drive.google.com/embeddedfolderview?id={folder_id}") as r:
        page = r.read().decode("utf-8", "replace")
    entries = re.findall(r'<a href="https://drive\.google\.com/file/d/([\w-]+)/[^"]*"[^>]*>.*?'
                         r'class="flip-entry-title">(.*?)</div>', page, flags=re.S)
    if not entries:
        sys.exit("No files found in that folder. Is it shared as 'Anyone with the link'?")
    return [(fid, html.unescape(name).strip()) for fid, name in entries]


def _filename(headers):
    cd = headers.get("Content-Disposition", "")
    m = re.search(r"filename\*=UTF-8''([^;]+)", cd)
    if m:
        return urllib.parse.unquote(m.group(1))
    m = re.search(r'filename="([^"]+)"', cd)
    return m.group(1) if m else None


def download(file_id, dest_dir, name=None):
    url = f"https://drive.usercontent.google.com/download?id={file_id}&export=download&confirm=t"
    r = _open(url)
    if r.headers.get_content_type() == "text/html":
        # Large-file or virus-scan warning page: submit its form to get the file itself.
        page = r.read().decode("utf-8", "replace")
        r.close()
        form = re.search(r'<form[^>]+action="([^"]+)"', page)
        fields = dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)"', page))
        if not form or not fields:
            msg = re.sub(r"<[^>]+>", " ", page)
            msg = re.sub(r"\s+", " ", html.unescape(msg)).strip()[:300]
            sys.exit(f"Drive didn't send file {file_id}. Is it shared as 'Anyone with the link'?\n  {msg}")
        r = _open(html.unescape(form.group(1)) + "?" + urllib.parse.urlencode(fields))
    fname = name or _filename(r.headers) or f"{file_id}.mp4"
    fname = Path(fname).name                     # never write outside dest_dir
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / fname
    part = out.with_name(out.name + ".part")
    total = int(r.headers.get("Content-Length") or 0)
    done, t0, last = 0, time.time(), 0.0
    with r, open(part, "wb") as f:
        while True:
            chunk = r.read(CHUNK)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if time.time() - last > 5:
                last = time.time()
                pct = f" {100 * done / total:5.1f}%" if total else ""
                print(f"  {fname}: {done / 1e6:8.1f} MB{pct}", flush=True)
    if total and done != total:
        part.unlink()
        sys.exit(f"Download of {fname} stopped early ({done} of {total} bytes). Try again.")
    part.replace(out)
    rate = done / 1e6 / max(time.time() - t0, 1e-6)
    print(f"saved {out}  ({done / 1e6:.1f} MB, {rate:.1f} MB/s)")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("links", nargs="+", help="Drive share links (files or folders)")
    ap.add_argument("--to", default=str(HERE / "before"), help="folder to save into (default: before/)")
    ap.add_argument("--name", help="save under this file name (single file only)")
    a = ap.parse_args()
    dest = Path(a.to)
    if not dest.is_absolute():
        dest = (Path.cwd() / dest).resolve()
    for link in a.links:
        kind, fid = parse_link(link)
        if kind == "file":
            download(fid, dest, a.name)
        else:
            for file_id, fname in folder_files(fid):
                download(file_id, dest, fname)


if __name__ == "__main__":
    main()
