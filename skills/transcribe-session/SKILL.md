---
name: transcribe-session
description: >-
  Transcribes one long session video with the ElevenLabs speech-to-text API
  and writes a timecoded transcript. Use when the user asks to transcribe a
  video, regenerate a transcript, re-transcribe, or run only the transcript
  step of a short.
---

# Transcribe a session

Run from the repository root. This step does not write a brief and does not cut.

```bash
python -m shortgen transcribe "videos/<file>.mp4"
```

The command extracts 16 kHz mono audio, sends that audio to ElevenLabs Scribe, and writes:

- `generated/<slug>/transcript/transcript.json` — word timings, the source of truth
- `generated/<slug>/transcript/transcript.md` — speaker turns with timecodes, the file to read
- `generated/<slug>/transcript/transcript.srt`
- `generated/<slug>/source.json`

Stdout is the Markdown path. Read that file.

When the video bytes are unchanged the command skips ElevenLabs and prints the existing Markdown path. Re-transcribe only when the user asks:

```bash
python -m shortgen transcribe "videos/<file>.mp4" --force
```

Pass `--language en` or `--num-speakers 3` only when the user supplies them.

Speaker names are optional. Edit `generated/<slug>/speakers.json` as `{"speaker_0": "Ada"}` and run:

```bash
python -m shortgen transcript render "videos/<file>.mp4"
```

That rewrites the Markdown and SRT from the saved words. It does not call ElevenLabs.

Requires `ELEVENLABS_API_KEY` in the environment or `.env`, and `ffmpeg` on PATH. Do not call the ElevenLabs HTTP API yourself.
