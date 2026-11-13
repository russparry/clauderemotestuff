# clauderemotestuff

The user works here from Claude Code cloud sessions, often from their phone, and doesn't use their
PC for this. Explain choices by what the user will see, not by jargon. They review on their phone
and often send screenshots with circles: act on exactly what's circled.

## Color grading and signature videos (`signature_overlay/`)

Two kinds of jobs:
- **Color grade only** (most videos): `make_signature_video.py <clip> --grade-only` -> `graded/`,
  the clip's own frame and sound, no signature or fade.
- **Signature video**: a hand-drawn white signature writes on, the scene fades to black, the
  signature holds on black for 2 s; 2160x3840 vertical 4K. Untouched picture unless they ask for
  color (`--no-grade --keep-audio`).

Read before working:
- `signature_overlay/GRADING_GUIDE.md`: how to grade any video, the user's preferences, the
  workflow with `grade_tools.py`, and every mistake made on the Nov 2026 drone clip with its fix.
- `signature_overlay/SIGNATURE_OVERLAY_RECIPE.md`: signature timing, placement, framing (`--fit`
  for horizontal clips), HDR handling, and the grading pipeline's internals.

### Each session

1. Bundled ffmpeg, pinned (the environment's setup script installs it; if `signature_overlay/pylib/`
   is missing): `python3 -m pip install --quiet --target signature_overlay/pylib "imageio-ffmpeg==0.6.0"`
   and, for untouched HDR renders only, `... --target signature_overlay/pylib_hdr "imageio-ffmpeg==0.8.1"`.
   Newer imageio-ffmpeg (ffmpeg 9.0.2) breaks graded renders (magenta, too bright).
2. Clips come from Google Drive share links set to "Anyone with the link" (the cloud environment
   "clauderemotestuff" allows `drive.google.com` and `drive.usercontent.google.com`):
   `python3 signature_overlay/fetch_drive.py "<link>"` -> `signature_overlay/before/`. A private
   file asks Google to sign in; the script says so: ask the user to change the sharing.
3. Grade with `signature_overlay/grade_tools.py` (analyze, stills, keys, check) and render with
   `make_signature_video.py`; a 26 s 4K60 render with masks takes 15-20 minutes (run it in the
   background). Previews (`--preview`) hide grain: test the darkest seconds at full size.
4. Deliver: send the render in the app, and push a phone copy to the repo's `videos` branch
   (`phone_copy.py`: 4K HEVC under 100 MB; public is fine, the user said so for all their videos).
   The user downloads from the GitHub app. GitHub releases can't be created from these sessions.
   Keep earlier versions (`4k/versions/`, `graded/versions/`): renders overwrite. Videos aren't
   committed to the code branches.

### The user's standing preferences

- Every video gets its own look from its own hues; `looks/examples/` are references only.
- Natural over processed: golden light golden with its texture visible, never neon or flat orange.
- Darker and clean beats bright and grainy. Nothing faded or hazy. No sudden changes over time.
  No two-toned trees or objects (no position-based rules that cross an object).
- Standard color (SDR) output; no HDR surprises. Keep 4K.
- Signature videos: ask for the start time if not given ("as late as you can" = `--start latest`);
  horizontal clips: ask once, fill the frame, `--fit` (the Nov 10 drone clip: signature shrunk to
  the picture and centered, black bars) or `--keep-frame` (the Nov 12 sunset clip: stays 3840x2160,
  no bars, same signature rule). No Instagram copies. No `--ink-shadow` unless asked.
- Always go by the numbers (user, Nov 12 2026): decide with measurements (detail, error against the
  ideal grade, color, grain, size), not by eyeballing downscaled stills; show the numbers.
- Masters keep 10-bit color, whatever the source ("keep the 10 bit color regardless").
- Do only what was asked; ask at most one question, only when the request can't be done as stated.
