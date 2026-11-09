---
name: brief-session
description: >-
  Selects the moments for a short from a session transcript and writes a video
  brief with timecodes. Use when the user asks for a brief, the key moments,
  what to cut, or to rewrite the brief without transcribing or cutting again.
---

# Brief a session

Run from the repository root. This step reads the transcript and writes a brief. It does not transcribe and it does not cut.

1. Run `python -m shortgen path "<video>" --name <name>`. The default name is `main`. A second short gets its own name, such as `payoff`.
2. Read the file at `transcript_md`. Read the index, then the turns you might use. On a long file, read it in parts.
3. Write the brief JSON to the `brief` path from that report.
4. Check, then render:

```bash
python -m shortgen brief check "<video>" --name <name>
python -m shortgen brief render "<video>" --name <name>
```

Fix every error and run check again. A warning that the quote does not match means the quote was paraphrased: copy the spoken words and check again. Other warnings are judgment. `ok` on stdout means the brief can be cut.

## What to select

- One claim or one story. A short is not a tour of the hour.
- Open on the claim, a number, or a concrete scene. A greeting, an agenda, or thanks is not a hook.
- Close on the line that lands the point. Leave Q&A housekeeping out.
- Use 2 to 5 moments. If the user gave a length, hit it. Otherwise aim for 30–45 seconds and set `target_seconds` to that aim.
- Start and end each moment on a complete sentence. Take the timecode from `transcript.md`.
- `quote` is the words spoken in that range, copied from the transcript.
- `why` says what the moment does: hook, proof, turn, or payoff.
- Keep source order. A later line may open the short; say so in `notes`.
- `avoid` lists nearby lines that would bore or give away the ending.
- Use `cut` when the join is the same thought. Use `fade` at 0.2–0.4s when the join is a new beat.

## Brief shape

```json
{
  "title": "The deploy was not the cause",
  "hook": "The outage started before the release.",
  "audience": "Engineers who ship on Fridays",
  "target_seconds": 35,
  "transition": {"type": "fade", "duration": 0.25},
  "notes": "Cold open on the later line, then the proof.",
  "avoid": ["welcome", "please follow us"],
  "moments": [
    {
      "id": "hook",
      "start": "00:18:12.400",
      "end": "00:18:20.100",
      "speaker": "speaker_1",
      "quote": "The deploy was not the cause.",
      "why": "Cold open on the claim."
    }
  ]
}
```

`start` and `end` may be `HH:MM:SS.mmm` or seconds. `id` is a lowercase slug. Transition `type` is one of `cut`, `fade`, `dissolve`, `fadeblack`, `fadewhite`, `wipeleft`, `wiperight`, `slideleft`, `smoothleft`.

A full sample lives in [brief.example.json](../../examples/brief.example.json).
