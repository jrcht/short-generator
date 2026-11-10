---
name: cut-short
description: >-
  Cuts a session video at the brief's timecodes, joins the moments, and frames
  the short at 1080x1920 with the opening, logo, large captions, watch card,
  and ending in assets. Use when the user asks to cut the short, re-cut, render the short,
  or change the transition of an existing brief.
---

# Cut the short

Run from the repository root. This step reads a brief and writes a video. It does not transcribe and it does not invent moments.

The brief must already be at `generated/<slug>/brief/<name>.json`. Default name is `main`.

```bash
python -m shortgen brief check "<video>" --name <name>
python -m shortgen cut "<video>" --name <name>
```

Stdout of `cut` is the short: `generated/<slug>/shorts/<name>/short.mp4`. The resolved in and out points, after word snapping, are in `edit.json` beside it. A previous `edit.json` is copied to `history/` before it is replaced. The previous mp4 is overwritten. To keep that file, cut again under a new `--name`.

A transition flag overrides the brief for this run and does not rewrite it:

```bash
python -m shortgen cut "<video>" --name <name> --transition fade --transition-duration 0.35
```

`--dry-run` prints the snapped plan and writes no video.

The cutter seeks near each moment, then trims on decoded frames, snaps the edges onto word timings, and adds `--pad` seconds (default 0.12) so a word is not clipped. Do not write an ffmpeg command yourself.

## Frame

`cut` delivers a 1080×1920 picture when these four files are in `assets/`:

| Role | File |
|---|---|
| Opening, before the moments | `Opening Transition short Martin Schaffer.mp4` |
| Logo frame, over the moments only | `Logos TVD Replay - short.png` |
| Watch card, after the moments | `Watch full session, innovation race v4.mp4` |
| Ending, last | `Ending - short.mp4` |

The session picture is cropped to fill the clear window of the logo frame. The crop follows the speaker across a wide stage, so the frame stays vertical and he stays in view. The logo sits on top of that window for the moments only.

The opening and the watch card use green as a key. Solid green is dropped, so it is not shown. The wipe at the end of the opening plays over the first moments. The wipe at the start of the watch card plays over the last moments. The session-sized join is kept as `body.mp4` next to `short.mp4`.

## Subtitles

`cut` burns large captions on the moments only, timed to the words in that cut. They sit just above the logo bar, in a black band, two short lines at a time. The opening, the watch card, and the ending stay clear. Fillers such as "uh" are left out. A cut with no transcript words gets no captions.

`--no-package` skips the cards and keeps the session frame. If one of the four files is missing, `cut` does the same and says so.

If check or cut warns that a quote does not match the transcript, fix the brief and cut again. A cold open may stay out of source order when `notes` says why.

## Adjust

`python -m shortgen view "<video>" --name <name>` opens a player for this brief. Trim the moments, set the transition, the voice level, and the subtitle lines, then save. Render in the player is this same cut. Voice is `gain` on a moment. `subtitles: false` leaves captions off the packaged short. Edited lines are `captions` on the moment.
