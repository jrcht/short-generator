---
name: cut-short
description: >-
  Cuts a session video at the brief's timecodes and joins the moments with
  ffmpeg. Use when the user asks to cut the short, re-cut, render the short,
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

If check or cut warns that a quote does not match the transcript, fix the brief and cut again. A cold open may stay out of source order when `notes` says why.
