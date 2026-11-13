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
- `signature_overlay/SIGNATURE_OVERLAY_RECIPE.md`: signature timing, placement, framing (`--own-frame`
  or `--fit` for horizontal clips), HDR handling, and the grading pipeline's internals.

### Each session

1. Bundled ffmpeg, pinned (the environment's setup script installs it; if `signature_overlay/pylib/`
   is missing): `python3 -m pip install --quiet --target signature_overlay/pylib "imageio-ffmpeg==0.6.0"`
   and, for untouched HDR renders only, `... --target signature_overlay/pylib_hdr "imageio-ffmpeg==0.8.1"`.
   Newer imageio-ffmpeg (ffmpeg 9.0.2) breaks graded renders (magenta, too bright).
2. Clips come from Google Drive share links set to "Anyone with the link" (the cloud environment
   "clauderemotestuff" allows `drive.google.com` and `drive.usercontent.google.com`):
   `python3 signature_overlay/fetch_drive.py "<link>"` -> `signature_overlay/before/`. A private
   file asks Google to sign in; the script says so: ask the user to change the sharing.
3. DJI log footage (D-Log M): start the look from DJI's official conversion, `"input_lut"` in the
   look (`python3 signature_overlay/get_dji_lut.py` fetches it from DJI and checks it; the
   environment allows `www.dji.com` and `terra-1-g.djicdn.com`). The user asked for it, and it
   measured better than our own conversion (glow texture 96% of raw vs 87%). The file's encoder
   tag names the drone (`DJI FLIP`); another model needs its own LUT added to `get_dji_lut.py`.
4. Grade with `signature_overlay/grade_tools.py` (analyze, stills, keys, check) and render with
   `make_signature_video.py`; a 26 s 4K60 render with masks takes 15-20 minutes (run it in the
   background). Previews (`--preview`) hide grain: test the darkest seconds at full size.
5. Deliver: the app (chat) takes 400 MiB at most and is the user's main way to get videos, so
   render the deliverable directly at that size: `--max-mb 415 --crf 9 --preset slow` (best
   measured quality that fits; re-compressing a bigger master costs detail). Then a 10-bit phone
   copy under 100 MB (`phone_copy.py`) on the repo's `videos` branch, and give the user its Safari
   link `https://raw.githubusercontent.com/russparry/clauderemotestuff/videos/<name>.mp4` (repo
   is public; the user couldn't find downloads in the GitHub app). A master over 400 MiB can't
   reach the user at all from these sessions. GitHub releases can't be created either.
   Keep earlier versions (`4k/versions/`, `graded/versions/`): renders overwrite. Videos aren't
   committed to the code branches.

### The user's standing preferences

- Every video gets its own look from its own hues; `looks/examples/` are references only.
- Natural over processed: golden light golden with its texture visible, never neon or flat orange.
- Darker and clean beats bright and grainy. Nothing faded or hazy. No sudden changes over time.
  No two-toned trees or objects (no position-based rules that cross an object).
- "Always go by the numbers": measure before and after every change (grade_tools, stills
  measurements, `check`, full-size crops, encode tests against a `--lossless` reference) and show
  the user the numbers, not impressions.
- Standard color (SDR) output; no HDR surprises. Keep 4K. Keep 10-bit color regardless: masters
  are 10-bit HEVC at CRF 10 (about the drone's own data rate and file size; the old 8-bit H.264
  master measurably smeared fine texture and the user noticed), phone copies 10-bit HEVC too.
- Signature videos: ask for the start time if not given ("as late as you can" = `--start latest`);
  horizontal clips: ask once: `--own-frame` (stays horizontal 4K, whole shot, no bars; the lake
  sunset, "whole shot but drop the black bars"), `--fit` (vertical with black bars; the first
  drone clip) or fill the vertical frame (crops the sides). Signature centered, 48.9% of the
  picture's height wide. An end time like "end at 31 s with 2 s after the fade" fixes the start:
  end - hold - fade - 0.5 - draw time (0.73 s). No Instagram copies. No `--ink-shadow` unless asked.
- Do only what was asked; ask at most one question, only when the request can't be done as stated.
