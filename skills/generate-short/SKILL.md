---
name: generate-short
description: >-
  Turns a long event-session video into a short. Use when the user asks to
  generate a short for a video, cut a short from a session recording, or run
  the full transcribe, brief, and ffmpeg workflow. For one step only, use
  transcribe-session, brief-session, or cut-short.
---

# Generate a short

Run from the repository root. Do the three steps in order. If the user named one step, do that step and stop.

The picture stays on disk. ElevenLabs receives audio only, and only during transcribe.

- [ ] Transcript
- [ ] Brief checked
- [ ] Short written

## 1. Transcript

Follow [transcribe-session](../transcribe-session/SKILL.md).

Run the command even when a transcript may already exist. Without `--force` it returns the current transcript when the file is unchanged.

## 2. Brief

Follow [brief-session](../brief-session/SKILL.md).

Read `transcript.md`. Leave `transcript.json` alone unless a timecode looks wrong.

## 3. Cut

Follow [cut-short](../cut-short/SKILL.md).

## Reply

Return the short path, the title, each moment with its timecode and why, and the transition. Do not paste the transcript.
