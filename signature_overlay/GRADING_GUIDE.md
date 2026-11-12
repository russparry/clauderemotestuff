# Color grading guide (any video, with or without the signature)

How to grade one of the user's videos so it comes out the way they want the first time. Everything
here was learned on real footage with the user, mostly on the Nov 2026 DJI drone clip
(`looks/dji_fly_20261110_170226_0010_1794355660045_video_d_logm.json`, its `notes` tell the whole
story), which took eleven rounds to get right. Read this before grading anything.
`SIGNATURE_OVERLAY_RECIPE.md` covers the signature, timing and framing; this file covers color.

## What the user wants (in their words where possible)

- **Every video gets its own look**, built from that footage's own hues. No house preset;
  `looks/examples/` (summer, fall, dusk) are references, never defaults.
- **Natural, not processed.** Golden light should look golden with the texture still visible
  ("the mountain is wayyy too orange, you can't see details of the trees").
- **Darker and clean beats bright and grainy.** "Grain very bad. I prefer that the color is darker
  so that it can be cleaner." Lift shadows only as far as they stay clean.
- **Nothing may fade or wash out.** Gray, hazy shadows read as "faded" ("this trees part looks
  weird every time the browns change and look faded").
- **Nothing may change suddenly.** "There is a drastic change in how all the browns look at 12
  seconds in." Any change over time must be gradual and must not touch colors it isn't for.
- **No two-toned objects.** "Now the trees colors are split in half"; "make sure there aren't any
  double colored trees". A tree, rock face or sky must get one even treatment top to bottom.
- **Keep the trees from going black** when the camera exposes for something bright, within the
  limits above.
- **It just needs to look the same** everywhere: standard color (SDR, Rec.709), never HDR output
  from a grade. The user dislikes HDR weirdness and wants to be told plainly what's happening.
- **Keep 4K.** Landscape footage stays landscape in grade-only renders; signature videos are
  2160x3840 (see the recipe for horizontal clips: `--fit`).
- The user often reviews on their phone, sends screenshots with circles, and gets videos from the
  GitHub app (see "Delivering").

## The workflow

1. **Get the clip** (Google Drive link, "Anyone with the link"; `fetch_drive.py`).
2. **Analyze it**: `python3 grade_tools.py analyze before/<clip> --to <end>`. It tells you:
   - size, frame rate, HDR or not, and whether it's **log footage** (D-Log M, S-Log: flat and
     washed out; needs real contrast and color: a stronger curve, saturation 1.3-1.4, vibrance 0.6);
   - the main hues (what to feature: one or two, not everything);
   - over time: overall brightness, the shadows, any **warm sunlit highlights** (brightness, color,
     hue) and the **camera's exposure** (measured from neighbouring frames).
   Read it: shadows falling while exposure holds = the scene (backlight), not the camera; warm
   highlights at 0.8-0.9 with weak color = washed out by the camera; exposure moving = the camera
   re-exposing.
3. **Write `looks/<clip>.json`** (picked up automatically). Start from the neutral base and the
   analysis: featured hues in `chroma_gains`, the curve for the footage, `protect_skin: false` if
   there are no people (golden light sits in the skin hue band and is otherwise held back),
   `sharpen` below 1 for downscaled 4K (0.3 for the drone clip). See the key table below.
4. **Iterate on stills, not renders**: `python3 grade_tools.py stills before/<clip> --times
   8,13,17,21 --look <clip> --vs raw`. Seconds per try, same math as the render minus sharpening
   and denoising. Look at the moments that change (when something enters the frame, when the
   camera re-exposes). Compare against the last version the user approved, not just against raw.
5. **Things that change over time** (only if the analysis shows them; details below):
   - washed-out warm highlights for a few seconds: `grade_tools.py keys --region A-B --settled C-D`
     gives `region_grade` keys;
   - shadows that go dark (backlight) and should stay up a bit: `grade_tools.py keys --forest A-B
     --target 55 --max-gain 1.4` gives `forest_boost` keys.
6. **Short full-resolution test** of the hardest few seconds:
   `python3 make_signature_video.py <clip> --grade-only --from A --to B` (no `--preview`: previews
   are 540 px tall and hide grain). Look at a full-size crop of the darkest area.
7. **Full render**:
   - grade only: `python3 make_signature_video.py <clip> --grade-only [--from A --to B]` ->
     `graded/<clip>_graded.mp4` at the clip's own size, its sound copied untouched;
   - with the signature: the recipe's commands (`--start`, `--to`, `--fit`, ...) -> `4k/`.
   Renders with time keys and masks take 15-20 minutes for 26 s of 4K60; run in the background.
8. **Check it**: `python3 grade_tools.py check <render> --source before/<clip> [--vs <previous>]`:
   grain in dark areas, trees two-toned or not (flags `<0.85`), warm-highlight color over time,
   flicker. Then look at the saved sheet and a full-size crop of anything the user pointed at.
9. **Deliver** (below), and save the render in `4k/versions/` or `graded/versions/` before the next
   one overwrites it.

## Look file keys

| Key | Use |
|---|---|
| `curve`, `chroma_gains`, `hue_shifts`, `vibrance`, `saturation`, `shadows_ab`, `highlights_ab`, `strength` | The look itself (the recipe has the details). Log footage: stronger curve; keep the shadow end of the curve modest if anything else lifts shadows. |
| `protect_skin` | `false` for footage with no people. |
| `sharpen` | 0-1, default 1. Lower for downscaled 4K or noisy shadows (0.3 on the drone clip). |
| `region_grade` | A second grade for one area (a washed-out sunlit peak) through a soft mask built from each frame: even exposure cut, color and hue turn for that area, so its texture is kept. Keys from `grade_tools keys --region`. |
| `forest_boost` | Brighten darker areas like an exposure increase (a gain, keeps color and contrast) through a soft mask of dark areas, with sunlit warm highlights and sky cut out. Keys from `grade_tools keys --forest`. Keep the gain at or below about 1.4, `green_extra` 1, add `denoise` (`vaguedenoiser`), no `top` band. |
| `exposure_offsets`, `shadow_lift_keys`, `warm_highlights` | Earlier tools, superseded on the drone clip: they caused the faded browns, the sudden change and the over-orange peak below. Prefer `forest_boost` / `region_grade`. |
| `denoise` | `true`: light noise reduction on the whole picture first (pushed low light). |

## What went wrong, and the fix (drone clip, Nov 2026)

| What the user saw | Cause | Fix |
|---|---|---|
| Purple, too-bright first preview | ffmpeg 9.0.2's 16-bit `unsharp` is broken | Pin imageio-ffmpeg 0.6.0 (ffmpeg 7.0.2); the 9.0.2 build only for untouched HDR (Dolby Vision) |
| Trees go black when the peak comes into view | Camera exposes for the bright peak; trees backlit | Lift the shadows, but see the next rows for how not to |
| Peak "almost white before it turns gold" | Camera washed the peak out for a few seconds | `region_grade` on the peak for those seconds (not a per-pixel rule) |
| "Drastic change in how all the browns look at 12 s" | A time-varying move that pushed shadows up and highlights down squeezed the midtones | Time-varying changes must be even (exposure) or confined to their area; never brightness-dependent squeezes |
| "Mountain wayyy too orange, can't see details" | A per-pixel rule eased every bright warm pixel (flattened texture) and turned hue redder | Leave the settled part exactly as approved; treat only the washed-out moment, through a mask, evenly |
| Aspens/browns "faded" | Shadow lift added the same amount to R, G, B (gray) and squeezed contrast | `forest_boost`: a gain (multiply), which keeps color and contrast |
| "Tree colors split in half" | Forest mask faded in from the top of the picture | No position-based rules on objects that cross them; the mask depends on darkness only |
| "Unnatural dark top" on a narrow tree | Backlit treetops are tinted warm, not green, so a greens-only rule skipped them | Don't select by "green"; darkness only |
| "Whole top left really grainy" | A gain of 2-2.5 on very dark log shadows amplifies the camera's noise; sharpening adds to it | Denoise the boosted area (`vaguedenoiser`), lower `sharpen`, and above all a smaller gain: the user chose darker and clean |
| Diagonal streaks in foliage (caught in testing) | `hqdn3d` at useful strength, and a steep "less gain for the darkest" taper | `vaguedenoiser`; no taper |

## Checks that would have caught these

- `grade_tools.py check`: grain in dark areas (compare with the approved version; lower is the
  goal), trees top vs lower (`<0.85` = dark tops / two-toned), warm-highlight hue and color at
  several moments (the same gold throughout), flicker (camera motion gives up to about 1-2 levels).
- Stills at the moment something changes, compared with the approved version, not only raw.
- A full-size crop of the darkest corner and of any thin dark object against something bright.
- Measure texture where the user cares about detail (mean difference of each pixel's lightness from
  its neighbours); a rule that eases brightness flattens it.

## Delivering

- Send the full render in the app (it's large, 160-220 MB) and a before/after crop of what changed.
- Phone copy for the GitHub app: `python3 phone_copy.py <render> <videos checkout>/<name>.mp4`
  (4K HEVC under 100 MB), committed to the repo's `videos` branch (public is fine for the user's
  videos). The user opens the repo in the GitHub app, switches to `videos`, downloads, Save Video.
  GitHub releases can't be created from these sessions.
- Explain what they'll see, not the mechanics; one question at most, only if the request can't be
  done as stated.
