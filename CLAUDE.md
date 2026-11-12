# clauderemotestuff

The user works here from Claude Code cloud sessions, often from their phone, and does not
use their PC for this. Explain choices by what the user will see, not by jargon.

## Signature videos (`signature_overlay/`)

The user's repeatable video edit: a hand-drawn white signature writes on over their clip, the
scene fades to black, the signature holds alone on black for 2 s, then the video cuts.
Output is 2160x3840 (vertical 4K) at the clip's own frame rate. Full process, defaults and
the reasoning behind every number: `signature_overlay/SIGNATURE_OVERLAY_RECIPE.md`. Read it
before editing a video.

### Each session

1. Bundled ffmpeg, pinned (skip if `signature_overlay/pylib/` already exists):
   `python3 -m pip install --quiet --target signature_overlay/pylib "imageio-ffmpeg==0.6.0"`
   Newer imageio-ffmpeg (0.8.x, ffmpeg 9.0.2) breaks the grade: its 16-bit `unsharp` tints
   the picture magenta and brightens it. Untouched HDR renders (`--no-grade` on iPhone HDR)
   also need libx265's Dolby Vision option, which only the newer build has:
   `python3 -m pip install --quiet --target signature_overlay/pylib_hdr "imageio-ffmpeg==0.8.1"`
   (the script uses it automatically for those renders only).
2. Clips come from Google Drive share links ("Anyone with the link"). The session's cloud
   environment ("clauderemotestuff") allows `drive.google.com` and
   `drive.usercontent.google.com`:
   `python3 signature_overlay/fetch_drive.py "<link>"` saves into `signature_overlay/before/`.
   A folder link fetches every file in it. iCloud links can't be downloaded from a script.
3. Render with `signature_overlay/make_signature_video.py` (see the recipe for options).
   Use `--preview --sheet` first when the timing is uncertain, and look at the sheet.
4. Send the finished `signature_overlay/4k/<clip>_4k.mp4` (and the check sheet) to the user
   as files in the app. Videos are not committed to git.

### The user's standing preferences

- Default is an untouched render: `--no-grade --keep-audio` (HDR and Dolby Vision kept, 4K,
  normal signature size). Grade only when they ask for color work.
- When grading, every video gets its own look (`looks/<clip>.json` built from that footage's
  hues, or `--hues` / `--hue-shift` / `--strength`). `looks/examples/` (summer, fall, dusk)
  are past projects, reference only, never defaults.
- Ask for the signature start time if it isn't given ("as late as you can" = `--start latest`).
- Horizontal (16:9) clips, such as DJI drone footage: ask once whether to fill the vertical frame
  (crops the sides) or show the whole shot with black bars (`--fit`). For the first one
  (Nov 2026) they chose the whole shot with bars, "as long as we keep the 4k": the file stays
  2160x3840.
- No Instagram exports. No `--ink-shadow` unless asked. Do only what was asked, and ask at
  most one question, only when the request can't be done as stated.
