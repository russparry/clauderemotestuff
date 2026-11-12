# Signature overlay recipe

This is the approved, repeatable process for one kind of video:

1. A clip, normally left exactly as the user exported it (graded only when they ask).
2. A hand-drawn signature writes itself on over the scene.
3. The scene fades to black while the signature stays bright white.
4. The signature holds alone on black for 2 s, then the video cuts.

**For a future Claude session:** read this whole file, then run the script below. Don't change the look or placement unless the user asks.

**Cloud version (since Nov 12 2026).** The user now runs this in Claude Code cloud sessions from the `clauderemotestuff` repo, with no PC involved. The Windows paths below (`C:\Users\russp\Videos\signature_overlay\`, `py`, `%TEMP%`) map to this folder (`signature_overlay/` in the repo), `python3`, and `/tmp`. Clips arrive as Google Drive share links (`fetch_drive.py`), and finished videos go back to the user as files in the Claude app. The repo's `CLAUDE.md` has the step-by-step version. The scripts were recovered from the PC session transcripts; `look_lut.py`'s color-math helpers (sRGB, OKLab, PCHIP, hue bands, gamut edge) were rewritten because those lines were never captured, and they pass the identity (lossless) check.

**Do only what's asked (Nov 2026 feedback).** When the user says "put the signature on this", they mean only the signature plus the fade to black 0.5 s after the pen finishes. Leave the picture and music untouched: `--no-grade --keep-audio`, 4K only, normal signature size.
- Grade only when they ask for color work.
- Don't convert HDR, color-match, or resize the signature unless asked.
- **No Instagram exports, ever** (Nov 11 2026: "i dont need instagram exports any more"). The option has been removed from the script.
- Don't ask HDR/SDR questions. Ask only if the request can't be done as stated.
- **Every graded video gets its own look.** No preset (summer, fall, or any other) is a default. See "Grading (only when asked)".

The user was frustrated when I did more than this. Every number here was matched to the user's reference video; treat them as the user's house style. The user sets the signature's timing per video.

## Folders

Everything lives in `C:\Users\russp\Videos\signature_overlay\`:

| Folder / file | What it's for |
|---|---|
| `before\` | The user drops clips here that need editing |
| `4k\` | Finished videos: `<clip name>_4k.mp4` (graded) or `<clip name>_original_4k.mp4` (`--no-grade`). This is the only output. |
| `looks\` | One look file per graded video: `<clip name>.json`, used automatically when that clip is graded. `looks\examples\` holds the looks from past projects (`summer`, `fall`, `dusk`) as reference only. |
| `insta\` | Old Instagram copies. Nothing writes here anymore. |
| `signature_white_transparent_2x_1.mov` | The signature, used automatically. White ink on a transparent background (ProRes 4444), 1248×832, 60 fps, 3.17 s. The pen draws from 0.45 s to 1.18 s of the clip. |
| `make_signature_video.py` | Does everything in one command |
| `look_lut.py` | Builds each look as a 3D LUT (`.cube`), used by the script. See "Grading (only when asked)" |
| `pylib\` | Bundled ffmpeg. If missing: `py -m pip install --target "C:\Users\russp\Videos\signature_overlay\pylib" imageio-ffmpeg` |

## Running it

Use `py`, not `python`, on this PC; bare `python` is the Microsoft Store stub.

```bash
py "C:\Users\russp\Videos\signature_overlay\make_signature_video.py" IMG_1234.MOV --start 12.5
```

- **Name a clip** from `before\` with `--start`, the second in the clip when the signature starts drawing. Ask the user for this if they haven't given it. It always renders and overwrites that clip's outputs.
- **`--start latest`:** places the signature as late as possible. The 1 s fade finishes on the clip's last frame, with the usual 0.5 s pause after the pen stops. Use it when the user says "as late as you can".
- **`--from 15 --to 45`:** uses only that section of the clip. All timing then counts from the start of the section, not the original clip. A clip can also be named by full path if it isn't in `before\`.
- **`--shots`:** for an edited clip made of several shots, especially at different times of day. It finds the cuts (scene-change detection on the picture area only) and gives each shot its own measured levels, so they cut together consistently. The QC report adds a per-shot table.
- **`--shot-looks "10,11,13=sunset"`:** with `--shots`, sets the look for particular shots (ranges and groups work: `2-4=hills;9=sunset`, using look names from `looks\` or `.json` paths); other shots use the video's look. Run a `--dry-run` first: it prints the shot list with times, then look at a frame from each shot before choosing.
- **`--fit`:** shows the whole picture of a clip that isn't 9:16. A horizontal clip is scaled to the frame's width and sits in the middle of the 2160×3840 frame with black bars above and below (a 3840×2160 shot becomes 2160×1216 inside the frame). Without it, the clip fills the frame and its sides are cropped; the script prints a note when that happens. Levels and QC are measured on the picture only, not the added bars. Outputs get `_letterbox` in the name. The signature shrinks with the picture: its ink keeps the usual width relative to the picture's short side (48.9%, like 1056 of 2160 px on a vertical video) and sits at the exact center of the picture. For a 3840×2160 shot that's ink box 783,1752–1377,2088 (594×336). The user asked for this ("shrink it to fit the 3840x2160 and keep it centered") after the first preview used the full vertical-video size. `--ink-box` still overrides it. First used Nov 12 2026 on a DJI drone clip; the user chose it over cropping, "as long as we keep the 4k".
- **`--reverse`:** plays the footage backward, while the signature and fade still play forward at the end. The video opens at full brightness, never fading in from black. Outputs get `_reversed` in the name, so the forward version isn't overwritten. Reversing holds the whole section in memory: about 5.5 GB for 30 s of the DJI clip, and about 25 MB per frame for 4K iPhone HDR (roughly 1.5 GB per second at 60 fps). The script stops with a clear message if there isn't enough free memory; use a shorter `--from`/`--to` section if that happens. Expect about twice the normal render time: 15 minutes versus 7.5 for the 30 s DJI section.
- **Look options** (only when grading; see "Grading (only when asked)"):
  - With no options, a graded clip uses `looks\<clip name>.json` if it exists, otherwise the plain `neutral` base.
  - `--look <name or file>` picks another look.
  - `--hues "orange+20,blue+10,green-15"` makes hues stronger or weaker for this video, in percent.
  - `--hue-shift "yellow-8,green+5"` turns hues by degrees.
  - `--strength 0.7` uses 70% of the look.
- **No clip named:** processes every clip in `before\` that doesn't have an output yet, using the default timing (below). `--force` redoes finished ones too.
- **Timing options:**
  - `--fade-after 0.5`: seconds between the pen stopping and the fade starting.
  - `--fade-dur 1`: how long the fade to black takes.
  - `--hold 2`: how long the signature stays alone on black before the cut. Set to 2 s on Nov 11 2026 (it was 8.72 s, which "holds for way too long").
- **Checking options:**
  - `--preview`: a fast 540×960 draft in about 30 s, saved to `%TEMP%\signature_previews\`. Use it to check timing before a full render.
  - `--sheet`: also saves six still frames to `%TEMP%\signature_previews\<clip>_check.jpg`: 1 s, mid-writing, pen stopped, mid-fade, black hold, end. Look at it after rendering.
  - `--dry-run`: prints the measured timing, placement, and exact ffmpeg command without rendering.
- **Grade options:**
  - `--legacy-grade`: the original per-channel ffmpeg filter grade, for side-by-side comparisons with the new LUT grade.
  - `--no-levels`: skip the measured black and white points.
  - `--keep-audio`: "don't touch the music". The clip's audio is copied bit for bit: no fade, no silence, no re-encoding. It simply stops where the video ends, after the 2 s hold. The hold is never stretched to fit the music. Until Nov 11 2026 it was: `B8083B9E` and `33DA341A` held 3.48 s to reach the music's end, and the user found that too long. Use `--keep-audio` whenever the clip has music the user cut to, rather than camera sound.
  - `--no-grade` (the usual choice): the clip exactly as the user exported it, with only the signature, fade, and hold added. No levels, look, sharpening, vignette, or noise reduction. HDR stays HDR (below). Outputs end in `_original`, so a graded version isn't overwritten. The user asks for this as "the original version, just with the script overlay and fade".
  - `--scopes`: also saves frame, waveform, and vectorscope images to `%TEMP%\signature_previews\<clip>_scopes.png`.
  - **HDR with `--no-grade`** (automatic; the old `--keep-hdr` flag is no longer needed): an HDR (HLG or PQ) clip stays HDR. Its frames go straight to the fade and overlay, with no scaling, format change, or retagging, and are encoded as 10-bit HEVC (x265).
    - **Dolby Vision is carried over** (`-dolbyvision 1`, with the VBV limits it requires, plus `-strict unofficial` so the MP4 gets the DV box). iPhone exports carry Dolby Vision profile 8.4. A render without it looked "different" to the user even though the pixels matched.
    - The white ink sits at HDR reference white (75% HLG).
    - The QC report is skipped, because its targets are SDR.
- **Signature orientation and position:**
  - `--sig-rotate 90|180|270` turns the signature clockwise.
  - `--ink-box X0,Y0,X1,Y1` places the ink anywhere in the 4K frame instead of the standard box.
  - Both are for sideways exports (see "Sideways exports" below).
- **`--ink-shadow`:** a soft dark glow under the signature strokes. Off by default (the house style is plain ink). Suggest it when the QC report warns about signature contrast. Outputs end in `_shadow`, so the plain version isn't overwritten. First used on `BE72EB5E-….MP4` (Nov 2026): over a near-white post-sunset sky (Y≈232 behind the ink), the plain signature was invisible while drawing, and the shadow made it clearly readable without looking heavy.
- **Other:** `--ink black` for black ink (white is the house style). `--signature <file>` to use a different signature recording. A black-on-white recording also works; the paper is cut out by brightness.
- A full render takes several minutes (about 11 minutes for a 21 s clip when graded), longer if the PC sleeps. Run it in the background.

## Timing

All times are in seconds from the start of the background clip.

| Event | Rule |
|---|---|
| Signature starts drawing | `--start`, set by the user for each video |
| Pen stops | Start + 0.73 s (the signature's drawing time, measured from the file) |
| Fade to black starts | **0.5 s after the pen stops** (`--fade-after`) |
| Fade length | 1 s, scenery only. The signature never dims. |
| Black hold with signature | **2 s** after the fade completes (`--hold`), then cut. Never stretched for the music. |
| Video ends | End of the black hold. If that's before the clip ends, the rest of the clip is trimmed. If it's after, black frames are added. |
| No `--start` given | The signature is placed so the fade still begins 3 s before the clip ends |

The script warns if the clip ends before the fade finishes, because it would cut to black early.

Examples (the older ones were made with the old 8.72 s hold; today the same commands end 2 s after the fade):
- `IMG_4406 (1).MOV` (21.32 s) with `--start 12.5`: drawing 12.50–13.23 s, fade 13.73–14.73 s, black hold to 16.73 s.
- `IMG_5167.MOV` (7.5 s fall aspens) with `--start latest --hold 12 --look fall`, the user's request "as late as you can, signature on for 12 seconds": drawing 5.25–5.99 s, fade 6.49–7.49 s, black hold to 19.49 s.
- DJI Flip dusk clip (65.5 s) with `--from 15 --to 45 --start latest --look dusk`, the user's request "run from 15 to 45 seconds, fade out at the end". The output's clock starts at the 15 s mark: drawing 27.75–28.48 s, fade 28.98–29.98 s (finishing at the original 45 s mark), black hold to 31.98 s.
- `B8083B9E-….MP4` (48.7 s, 13-shot edit, letterboxed landscape footage) with `--start 43 --shots --shot-looks "10,11,13=dusk"`, the user's request "start the signature at 43 s, fade 0.5 s after it finishes, hold the black 2 s before cutting": drawing 43.00–43.73 s, fade 44.23–45.23 s, end at 47.23 s.

- DJI drone clip `dji_fly_20261110_170226_0010_…_video_d_logm.mp4` (Nov 12 2026, the first cloud render): 3840×2160 at 59.94 fps, 10-bit **D-Log M** (DJI's flat log profile; average chroma about a third of normal footage), no audio. Request: "run the color grading and the signature and fade to black, .5 seconds from the signature to start of fade, end the video at 26 seconds, start signature at 23 second mark". First request: signature at 23 s, end at 26 s. Second (what was delivered): "start the fade to black at 23 seconds this time, still let it run to 26 seconds", keeping the 0.5 s gap after the pen, so `--to 26 --start 21.766667 --fit`: drawing 21.77–22.50 s, fade 23.00–24.00 s, standard 2 s hold to 26.00 s. Its own look file (`looks/<clip>.json`: stronger curve, saturation ×1.35, vibrance 0.6, gold +35% for the sunlit peak, sky +25%, evergreens +15%, no warm highlight tint so the snow stays white, `protect_skin: false`). When a user's end time and the 2 s hold disagree, the end time wins (`--hold` set to fit).

The `fall` and `dusk` looks in these examples were made for those particular clips. They now live in `looks\examples\`, and a new clip shouldn't reuse them by habit.

"Extend the signature to 12 seconds" meant the signature-alone-on-black hold (`--hold 12`; at the time the hold was normally 8.72 s), so it reads as about 4 s longer.

## Output format

One file per render, in `4k\`:

| | Standard-color clips, or any graded clip | HDR clips with `--no-grade` |
|---|---|---|
| Frame | 2160×3840, the same frame the user exported | Same |
| Frame rate | Same as the source (e.g. 59.94) | Same |
| Video | H.264 High 5.2, CRF 17, 8-bit 4:2:0, BT.709 video range | 10-bit HEVC (x265), CRF 16, HLG or PQ as exported, Dolby Vision kept |
| Audio | `--keep-audio`: copied untouched, stops at the cut. Otherwise the clip's stereo track at 192k AAC, faded with the picture and silent during the black hold. | Same |
| Container | MP4 with faststart, rotation tag removed | Same |

Clips that aren't 9:16 are scaled to fill the frame and center-cropped. A horizontal clip loses its sides, so warn the user before running one.

**Letterboxed clips:** the user exports landscape edits inside a vertical 2160×3840 frame with black bars above and below, and calls these "landscape videos".
- Keep that frame as is: same size, bars kept, nothing reframed. The script detects the picture area and measures only inside it.
- The standard signature box lands inside the picture.
- "Make sure the 4K stays 4K" means render at the full 2160×3840 with no downscaling. The picture inside such an export is only about 2160×1215, because the editor already fit the landscape footage to the frame's width. True 3840-wide landscape 4K would need a landscape export from their editor.

**Sideways exports:** the user can also export a true 4K landscape edit stored sideways: 2160×3840 with no rotation tag, the picture turned 90° clockwise so the sky is on the right edge. `33DA341A-….MP4` (Nov 2026, HDR HLG) was the same edit as `B8083B9E`, exported this way.
- Keep the file sideways as exported. Don't rotate the video unless asked.
- Turn the signature 90° clockwise so it reads correctly once the video is upright. The user first said "180°"; asked, they chose 90° clockwise to match the footage.
- Use the normal signature size: the standard 1056×598 ink box turned in place around the same center, `--ink-box 772,1445,1370,2501`. A first attempt that scaled it up to match the letterboxed version's proportion (`452,965,1515,2842`) was "too much".
- General mapping: a scene point at (X, Y) on a 3840×2160 upright picture lands at frame x = 2159 − Y, y = X.
- Command used: `--start 43 --no-grade --keep-audio --sig-rotate 90 --ink-box 772,1445,1370,2501`.
- Check it by extracting a frame with `transpose=2` (turns it upright) plus the HDR-to-SDR tonemap chain, and looking at it.

Clips smaller than 4K (like the DJI Flip's 1512×2688) are upscaled for the `4k\` file, which adds no real detail.

## Signature placement

The placement is defined by where the **ink** lands, not the signature clip's frame. The script measures the ink in the signature file and scales and places it to hit this box.

| | 4K frame (2160×3840) | As % of the frame |
|---|---|---|
| Ink left–right | x 543 → 1599 | 25.1% → 74.0% of width |
| Ink top–bottom | y 1675 → 2272 | 43.6% → 59.2% of height |
| Ink size | 1056 × 598 px | 48.9% of frame width |
| Ink center | (1071, 1973) | 49.6% across, 51.4% down: centered, a touch below middle |

- **With the current signature file:** scaled ×0.923 to 1152×768 and placed at x=504, y=1536 on the 4K frame.
- **Ink color:** pure white (video-range Y=235; HDR reference white in HDR files). The stroke is about 7 px at 4K, matching the reference.

## Grading (only when asked)

Most videos aren't graded (`--no-grade`). When the user does ask for color work, the goal is bright, punchy, and true to life, never neon or over-processed. **Every video gets its own look,** built for the hues in that footage. In the user's words: "Every single color grading project will be different… each video will want their own hue." There's no seasonal or house preset.

### Making a video's look

1. Look at frames from the clip (`--dry-run`, then a few stills or `--scopes`). Name the hues that matter, such as the sky, water, foliage, rock, or sunset, and decide which to push, calm, or turn.
2. Write `looks\<clip name>.json`, which is picked up automatically for that clip. Any key left out comes from the neutral base. Hues can be names or OKLCH degrees:
   ```json
   {
     "notes": "what this look is for, in a sentence",
     "hue_shifts":   [["yellow", 30, -6]],
     "chroma_gains": [["orange", 30, 1.20], ["blue", 40, 1.10], ["green", 35, 0.92]],
     "shadows_ab": [-0.003, -0.008], "highlights_ab": [0.004, 0.010],
     "vibrance": 0.25, "saturation": 1.10,
     "strength": 1.0,
     "denoise": false
   }
   ```
   | Key | What it does |
   |---|---|
   | `hue_shifts` | `[hue, half-width °, degrees]`. Turns that hue band. Minus is toward red/orange, plus toward green/blue. |
   | `chroma_gains` | `[hue, half-width °, gain]`. 1.20 makes that band 20% more colorful, 0.90 makes it 10% calmer. |
   | `curve` | Contrast curve `[[in, out], …]` on gray values 0–1. Default: the gentle S-curve `0/0 .06/.045 .25/.25 .5/.56 .75/.82 .92/.95 1/1`. |
   | `shadows_ab`, `highlights_ab` | Split tone as OKLab a/b offsets. Negative b is cooler, positive b warmer. Default 0, so no tint. |
   | `vibrance` / `saturation` | Boost for dull colors / overall color strength. Defaults 0.25 / ×1.10. |
   | `strength` | How much of the look to use: 0.7 is subtle, 1 is full. |
   | `denoise` | Light noise reduction first, for low-light footage pushed hard. |
   | `protect_skin` | `false` turns off the skin guard, for footage with no people. Golden light on rock or foliage sits in the skin hue band (about 38–86°) and is otherwise held to +5%. |
3. For quick changes without a file, use `--hues "orange+20,blue+10,green-15"` (percent), `--hue-shift "yellow-8"` (degrees), and `--strength 0.7`. These add to whatever look is in use. The plan printout shows them on the `look` line.
4. With no look file and no `--look`, the **neutral** base is used: the gentle S-curve, vibrance 0.25, saturation ×1.10, and no hue preference at all.

Hue names (OKLCH degrees): red 29, orange 55, gold 80, yellow 105, lime 125, green 145, teal 175, cyan 200, sky 230, blue 260, purple 295, magenta 330, pink 350. Each band fades smoothly over ±30° (or the half-width you give).

What a hue boost can't do, by design:
- It can't push a color that's already at the edge of what the screen can show. A vivid sunset orange stays as it is.
- It barely moves skin-like oranges at moderate saturation (the skin guard).

`look_lut.py` builds the grade as a **perceptual 3D LUT**. The original ffmpeg filter chain is kept as `--legacy-grade` for comparisons, but only for the three example looks.

### Pipeline, in order (16-bit until the final dither)

1. **Noise reduction** (looks with `"denoise": true` only): `hqdn3d=4:3:6:5`. Pushing low-light footage hard shows a mottled grid in the sky without it.
2. **HDR to standard color:** `zscale=t=linear:npl=100 … tonemap=hable:desat=0 … zscale=t=bt709`. `desat=0` keeps the sky blue. Skipped automatically for clips that aren't HDR.
3. **Measured levels, the primary correction.** The script samples 12 frames across the section (or 4 per shot with `--shots`), inside the picture area only, and reads the luma at the 0.3% and 99.7% points. Those become the black and white points, with limits: it removes at most 0.12 of black lift and stretches highlights by at most 1.25×, so it corrects without restyling. Very hazy shots keep some haze. The limit was raised from 0.06 after the first real test footage had blacks lifted to 0.13–0.23. Values are printed as `levels … (measured …)`. Skip with `--no-levels`.
4. **The look, as one LUT** (`lut3d=…:interp=tetrahedral`, 65-point). It's built fresh for each render in `%TEMP%\signature_luts\`, with that clip's levels baked in. Inside the LUT, all in OKLab/OKLCH:
   - **Tone:** the look's contrast curve applied to perceptual lightness only. Each color is mapped the way a gray of the same lightness would be, so grays match the old curve exactly, but colors keep their hue.
   - **Chroma:**
     - Contrast-linked: a steeper part of the curve adds a matching bit of color, the way an RGB curve would, but without the hue skew.
     - Per-hue gains.
     - Vibrance: boosts dull colors more, measured against how far each color can go before it leaves the displayable range.
     - An overall saturation trim.
   - **Skin guard:** moderately saturated colors in the skin-hue band (OKLCH hue about 38–86°) get at most +5% chroma and almost no hue shift. Vivid sunset oranges in that band are more saturated than skin, so they still get the full boost.
   - **Hue:** small rotations per hue band. Near-grays are left alone, because their hue is just noise.
   - **Split tone:** cool shadows and warm highlights, as small OKLab a/b offsets.
   - **Gamut:** a color that doesn't fit Rec.709 loses chroma at the same lightness and hue until it fits. Channels are never clipped.
5. **Sharpening:** `cas=strength=0.4` in RGB, then an explicit conversion to 16-bit YUV and `unsharp=13:13:0.3`.
6. **Back to 8-bit with error-diffusion dithering:** prevents banding in sky gradients.
7. **Vignette (angle 0.4):** subtle edge darkening toward the center and the signature.

### Example looks from past projects (`looks\examples\`, not defaults)

These were made for particular clips. They're starting points to copy from when a new clip's footage is similar, not choices to pick from. Use them with `--look summer` and so on.

| | summer | fall | dusk |
|---|---|---|---|
| For | Daylight greenery | Golden or orange foliage | Flat low-light, dusk, sunsets |
| Made for | `IMG_4406` | `IMG_5167` aspens | DJI Flip dusk clip; shots 10, 11, 13 of `B8083B9E` |
| Contrast curve | `0/0 .06/.045 .25/.25 .5/.56 .75/.82 .92/.95 1/1` | same as summer | `0/0 .08/.055 .25/.24 .45/.51 .65/.77 .85/.94 1/1` |
| Hue shift | yellow-greens (110°) +6° toward green | yellows (95°) −6° toward gold | yellows (85°) −8° toward orange |
| Chroma gains | greens +8%, pinks +15%, reds +12%, blues −6% | golds +10%, greens +5%, pinks/reds +12%, blues −6% | reds +25%, oranges +20%, magentas +20%, blues +15% |
| Vibrance / overall saturation | 0.25 / ×1.10 | 0.25 / ×1.10 | 0.65 / ×1.22 |
| Split tone (shadows / highlights, a,b) | −.003,−.008 / +.004,+.010 | same as summer | −.004,−.010 / +.008,+.016 |
| Noise reduction | no | no | yes |

### How the LUT grade was validated (synthetic patterns only)

The color-expert method: rings of 72 hues at equal lightness and equal chroma, at four brightness levels, plus a gray ramp and a bright-orange ramp, run through ffmpeg with the legacy filters and with the LUT.

- **Unintended hue drift in midtones:** under 1° with the LUT, versus 3–8° (summer) and 6–14° (dusk) for the legacy filters. With the warm/cool split tone switched off, the LUT's drift is 0.0° at every brightness.
- **Bright saturated orange (hue 55°) getting brighter:**
  - LUT: holds at 55°, 55°, then 59°.
  - Legacy: summer drifts 51° → 75°, dusk 43° → 92°. That's the "sunset turns lemon yellow" problem.
- **Grays:** the tone curve matches the legacy curve within 0.005.
- **Colorfulness:**
  - Summer and fall match the legacy grade within a few percent (×1.47 / ×1.32 / ×1.03 versus ×1.45 / ×1.34 / ×1.10 across brightness levels).
  - Dusk is a little calmer in bright colors (×0.98 versus ×1.37). That's on purpose: the legacy dusk got its extra highlight color by clipping 65% of the test colors at the gamut edge.
- **Identity LUT:** reproduces its input to about 10⁻¹³, so the LUT machinery itself is lossless.

**First real-footage test (Nov 2026, `B8083B9E-….MP4`).** A 13-shot, very flat edit (source saturation 5–23, blacks lifted to 35–60 of 255): daylight desert lake, low sun backlighting the water, and dusk sunsets. The new grade was compared side by side with `--legacy-grade`:

- **Daylight shots:** cleaner, brighter sky, richer blue water, real contrast. The legacy grade left the blacks milky (QC black Y=35 legacy versus 19 new).
- **Dusk and sunset shots:** much more vivid. The hills glow orange-red, the water goes deep blue, and the sunset stays orange-pink where the legacy grade turned it lemon-yellow.
- **Consistency:** per-shot levels brought the shots to black 17–26 and white 211–233 (the source ranged 14–61 and 177–254). The exception is one hazy shot, at black 46.
- **Saturation:** the QC report still shows low overall saturation (about 15) because the scenes are naturally muted, mostly water, sky, and backlight. Don't force it up; the shots look right.
- **Signature legibility:** the user said it's fine if the signature blends in a little with the background. Don't add `--ink-shadow` unless asked, even when the contrast check warns.

## Grading principles (from the research the user asked for, Nov 2026)

Sources, read in full:
- **perbhat/agentic-color-grader:** README, both workflow skills, and the analysis, correction, scopes, matching, and ffmpeg code.
- **SkillDB "color-grading-skills":** all 10 skills (color science, workflow, Resolve, HDR, look development, LUT management, primary, scene matching, secondary, skin tone).
- **meodai/skill.color-expert:** CLAUDE.md, SKILL.md, the reference index, and the references on tone-mapping hue collapse, perceptual color effects, color proportion, chroma vs saturation, LUT grading, and skin-tone chroma.
- **clawic "color" skill:** all 10 files.
- **aicodedecode "video-color-grading"** (skillsdirectory.com, added Nov 11 2026): the full SKILL.md. It's a short ffmpeg filter reference written for another tool (OpenMontage). It has no scope targets, HSL, split toning, or HDR steps, and most of its points are already covered below.
  - We took **look strength** (the `strength` key and `--strength`). It recommends 0.6–0.85 for subtle grades.
  - It confirmed the saturation limit for people: never above about ×1.2 with skin in frame, and check a frame with visible skin.
  - Not taken:
    - Its fixed mood recipes and seven named profiles, because each video gets its own look.
    - Per-frame `normalize` auto-levels, because they flicker. Our levels are measured once per clip or shot.

The Reddit thread on S-Log3 → Rec.709 with Claude Code (r/ColorGrading) **could not be read**: reddit.com is blocked for both the browser and the web fetcher. Ask the user to paste it if it matters.

What we took from them, and where it lives:

1. **Measure, then look.** Your eyes adapt to a cast within seconds; scopes don't. A vision model names colors less reliably than code that samples pixels. → measured levels, the QC report, `--scopes`. Treat a visual impression as a hypothesis and confirm it with numbers.
2. **Primary correction before the creative look**, and never use auto-balance as the final step: a sunset auto-neutralized loses its mood. → bounded levels first, then the look. White balance is reported, never auto-corrected.
3. **"Brighter" is not hue-safe.** Per-channel curves and clipping push bright colors toward the "notorious six" (R/Y/G/C/B/M). → all tone and color work happens on OKLab lightness, chroma, and hue.
4. **Gamut by chroma, not clipping.** Reduce chroma at the same lightness and hue; clipping channels shifts hue. → the LUT's gamut step.
5. **Restraint beats saturation.** Vivid color works because it's rare, and "if everything is special, nothing is special." Natural-looking images use less saturation than you'd think. → vibrance favors dull colors, pick one or two hues per video to feature rather than boosting everything, use `strength` below 1 when a look feels heavy, and the QC warns above about 90 saturation.
6. **Protect skin.** All skin sits on one narrow hue axis, and a global push wrecks it first. → the skin guard.
7. **Look at the result in context and in motion,** not just one hero frame: several moments, the cut points, and the real background behind any text. → the six-frame sheet, QC over 10 frames, and the signature contrast check (white text "collapses fast on bright fills").
8. **LUT management:**
   - Document what each LUT's input and output are. Ours: Rec.709 full-range RGB in, Rec.709 full-range RGB out, written in the `.cube` title.
   - Use tetrahedral interpolation and 65 points for cross-channel transforms.
   - Keep technical transforms (the HDR conversion) separate from creative ones (the look).
   - Never stack LUTs.
9. **Series consistency** (when the user posts several clips as a set): grade the best clip first, then match the others to it. Matched clips should agree within YAVG ±5, U/V ±3, saturation ±5. Compare QC reports across clips.
10. **Check subtle casts losslessly:** JPEG smears chroma shifts of about ±5. Use the numbers or a modest-size PNG for that; use JPEG only for overview strips.

Things from the sources that don't apply here: ACES and Resolve pipelines, Dolby Vision trims, print, UI tokens, and data visualization.

## Gotchas (each of these happened)

- **Sideways output:** iPhone clips carry a -90° rotation tag. ffmpeg turns the pixels upright but copies the tag anyway, so players rotate the video again. The script re-wraps each file with `-display_rotation:v:0 0`.
- **Faint gray box around the signature on black, cause 1:** the background is tagged video-range BT.709 and an untagged signature layer gets silently converted. Both layers must be tagged, with the alpha plane tagged `range=pc`.
- **Faint gray box around the signature on black, cause 2:** shrinking the 12-bit ProRes transparency to 8-bit dithers empty areas to 1/255. The script snaps transparency at or below 4/255 to exactly 0.
- **ffmpeg 9.0.2 (imageio-ffmpeg 0.8.x) breaks the grade:** its `unsharp` at 16-bit brightens the picture (average Y 79 → 98) and tints it magenta (U/V 127 → 165). The first cloud preview came out purple. The script uses imageio-ffmpeg **0.6.0** (ffmpeg 7.0.2 on Linux, 7.1 on Windows) and only uses the newer build (`pylib_hdr/`) for untouched HDR renders, which need its `-dolbyvision` option and don't sharpen.
- **iPhone spatial-audio track:** shows up as codec "none" and can't be decoded. The script uses the first normal audio track.
- **Viewing check images:** large PNG contact sheets look posterized in Claude's image viewer, which is easy to mistake for banding. Use JPEG for overview strips. For judging subtle color casts, use the numbers (QC report) or a modest-size PNG, because JPEG smears small chroma shifts.
- **`unsharp` can't work on 16-bit RGB:** ffmpeg silently converts to an untagged YUV format before it, and the next `zscale` then fails with "no path between colorspaces" on clips without color tags. The chain converts to 16-bit YUV explicitly first.
- **Untagged clips** (no color tags at all): `zscale` is told the input is Rec.709 (`pin=bt709:tin=bt709`) so they convert instead of failing.
- **"My HDR video looks washed out on my computer":** iPhone HDR clips look flat on a regular (non-HDR) screen, such as when viewed from iCloud on Windows. The color isn't lost. The script's HDR-to-standard conversion plus the grade brings the pop back, and the output looks the same on every screen.
- **Which file is which:** the user has sometimes mixed up which file is the "script" (signature) and which is the "landscape". The signature is the short clip in this folder; the clips in `before\` are landscapes.

## Checking a finished render

Every render prints a QC report, measured on 10 frames of the picture before the fade:

| Line | Target | Meaning |
|---|---|---|
| black (0.5%) | Y ≤ 24 | The darkest 0.5% of pixels; video black is 16. Higher means lifted, milky blacks. |
| white (99.5%) | Y 190–235 | Scene dependent: dusk and night can sit lower on purpose. |
| clipped | < 1% | Pixels at peak white, where highlight detail is gone. |
| crushed | < 5% | Pixels at black, where shadow detail is gone. |
| midtones (median) | 80–140 | Typical for daylight; dusk and night run lower. |
| saturation | 40–80 | Average color strength (as ffmpeg's signalstats reports it). Above about 90 starts to look neon. |
| color cast | within ±12 | U and V offsets from neutral. A strong cast is fine when intended, like a sunset. |
| signature contrast | ≥ 3:1 | White ink against the brightest quarter of the picture under it, just before the pen starts. Below 3:1, suggest `--ink-shadow` or a different `--start`. |

With `--shots`, a per-shot table follows (black, median, white, saturation, and cast per shot). Shots meant to cut together should agree within about ±10 on black and white levels. Medians can differ when the lighting really differs, such as day versus dusk.

Treat WARN lines as prompts to look, not automatic failures. Then check the sheet (`--sheet`) and, for color questions, the scopes (`--scopes`).

Other expected numbers:

- **Ink position on the last frame:** x 543–1599, y 1675–2272 (or the `--ink-box` given). Peak luma about 235–245, i.e. full white.
- **Black around the signature:** Y = 16 both inside and outside the signature's rectangle on the black hold, so no visible box.
- **File info:** no `displaymatrix` line in `ffmpeg -i` output. The duration is the fade end plus 2 s.
- **On the check sheet:** the signature is partly drawn in frame 2 and complete in frame 3. Frames 5–6 are black with only the white signature.

