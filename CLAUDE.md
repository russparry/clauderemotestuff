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

1. Bundled ffmpeg (the stock one is too old for the HDR / Dolby Vision options):
   `python3 -m pip install --quiet --target signature_overlay/pylib imageio-ffmpeg`
   (skip if `signature_overlay/pylib/` already exists).
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
- No Instagram exports. No `--ink-shadow` unless asked. Do only what was asked, and ask at
  most one question, only when the request can't be done as stated.
