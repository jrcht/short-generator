# Short generator

Cut a short out of a long event-session video. Transcription and ffmpeg are commands. Choosing the moments is a skill, so you can re-run any step without repeating the others.

```
generate a short for video videos/session.mp4
```

## Layout

```
skills/                  agent instructions for each step
videos/                  the session recordings
generated/<slug>/
  source.json            file hash, duration, frame size
  speakers.json          optional map of speaker_0 to a name
  transcript/
    transcript.json      word timings from ElevenLabs
    transcript.md        speaker turns with timecodes
    transcript.srt
  brief/
    main.json            the cut contract, safe to edit by hand
    main.md              the same brief, for reading
  shorts/
    main/
      short.mp4
      edit.json          the in and out points actually used
      history/           previous edit.json files
    <another-name>/      a second short from the same session
shortgen/                the commands
```

Cursor loads the skills through `.cursor/skills`, which points at `skills/`.

The session file stays in `videos/` or wherever it already is. Working files go under `generated/<slug>/`, so a re-cut does not rewrite the transcript and a second angle does not overwrite the first short.

## Setup

Python 3.10 or newer, and `ffmpeg` / `ffprobe` on `PATH`.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Put `ELEVENLABS_API_KEY` in `.env`. If the key belongs to an EU residency workspace, set `ELEVENLABS_API_BASE=https://api.eu.residency.elevenlabs.io`.

Drop a session file in `videos/`, or pass a path to a file that already lives somewhere else.

## Make a short

From the repository root, an agent following `skills/generate-short` will:

1. Transcribe. ElevenLabs Scribe returns word timings and speaker labels. The picture is not uploaded. A 16 kHz mono extract is.
2. Read `transcript.md` and write a brief: the moments, the timecodes, the quote, and why each moment is there.
3. Cut those moments with ffmpeg and return `generated/<slug>/shorts/<name>/short.mp4`.

Run the same steps yourself:

```bash
python -m shortgen transcribe videos/session.mp4
python -m shortgen brief check videos/session.mp4
python -m shortgen brief render videos/session.mp4
python -m shortgen cut videos/session.mp4
```

`brief check` prints `ok` when the brief can be cut. The short's path is the stdout of `cut`.

## Run one step again

| You want to | Command | What stays |
| --- | --- | --- |
| New transcript | `python -m shortgen transcribe <video> --force` | Existing briefs and shorts |
| Rename speakers | Edit `speakers.json`, then `python -m shortgen transcript render <video>` | Word timings, briefs, shorts |
| Different moments | Edit `brief/<name>.json`, then `brief check` and `cut` | Transcript |
| Same moments, other join | `python -m shortgen cut <video> --transition wipeleft --transition-duration 0.4` | The brief file |
| Trim, voice, subtitles | `python -m shortgen view <video>` | The transcript. See [Timeline editor](#timeline-editor). |
| A second short | Write `brief/<name>.json` and `cut --name <name>` | The `main` short |

`transcribe` without `--force` returns the current transcript when the file size and modification time match, and it does not call ElevenLabs. `cut` overwrites that short name. The previous decision log is kept in `shorts/<name>/history/`.

`python -m shortgen path <video>` prints the slug, the transcript, the brief, and the short, and whether each file exists.

## Timeline editor

From the repository root, open the player on the session, or on a short that is already cut:

```bash
python -m shortgen view videos/session.mp4
python -m shortgen view generated/<slug>/shorts/main/short.mp4
```

Either command prints a local URL and opens it in the browser. Pass `--no-open` to print the URL only, and `--port` when 8733 is taken. `--name` picks a short other than `main`.

The session path is for trimming. The rendered-short path plays the finished file, and its timeline lists each piece that was joined: the opening, each moment, the watch card, and the ending. If the session file is not in `videos/`, that second command still opens the short. Putting the session file back is what unlocks trimming.

Drag either edge of a moment to trim it, split it, or remove it. The transition, the voice level of the selected moment, and the subtitle lines sit under the picture. Save writes the brief. Render runs the same cut as `python -m shortgen cut`, including the opening, logo, watch card, and ending.

The player previews the cuts from the session file. The transition and the burned subtitles are in the rendered file. Voice is a level from silent to 150%, stored on the moment as `gain`. Clearing a subtitle line drops it. `subtitles: false` in the brief leaves the lines off the render.

## Brief

`generated/<slug>/brief/main.json` is the contract between the selection step and the cutter. Times may be seconds or `HH:MM:SS.mmm`. A sample is in `examples/brief.example.json`.

```json
{
  "title": "The deploy was not the cause",
  "hook": "The outage started before the release.",
  "target_seconds": 35,
  "transition": {"type": "fade", "duration": 0.25},
  "notes": "Cold open on the later line, then the proof.",
  "avoid": ["welcome"],
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

`quote` has to be the words spoken in that range. `brief check` compares it with the transcript and warns when it was paraphrased. `why` is there so a later re-cut still knows the job of the moment.

## Transitions

Set `transition.type` in the brief, or pass `--transition` for one run.

`cut`, `fade`, `dissolve`, `fadeblack`, `fadewhite`, `wipeleft`, `wiperight`, `slideleft`, `smoothleft`.

`cut` is a hard join. The others are ffmpeg crossfades. The default when a brief omits the field is a 0.25s `fade`. Use `cut` when the two moments are one continuous thought.

## How a cut is made

For each moment the cutter seeks to ten seconds before the in-point, then trims on decoded frames. Seeking with `-ss` before `-i` alone stops on a keyframe and clips the first word.

If a transcript exists, the in-point moves back to the start of the word it landed in, and the out-point moves forward to the end of its word. `--pad` (default 0.12s) then adds a little air on both sides.

Joins are re-encoded. A hard cut of separately encoded pieces can still glitch at the boundary, and a crossfade cannot be stream-copied. The short is H.264, AAC, and starts playing before the whole file is downloaded (`+faststart`).

## Defaults

- The short keeps the session's frame size and aspect ratio.
- Speaker diarization is on. Names are `speaker_0` until you map them in `speakers.json`.
- Language is detected. Pass `--language` when you know it.
- With no length given, the brief skill aims for 30–45 seconds and 2–5 moments.
- One session can produce several shorts. Each name has its own brief and its own mp4.

## Later

These are the parts worth adding once the three steps are in use:

- A music bed under the join.
- ElevenLabs' speaker library, so a known voice comes back with a name.

## Tests

```bash
python -m unittest discover -s tests -v
```

The ffmpeg test builds a short synthetic video and checks that a fade join has the expected duration.
