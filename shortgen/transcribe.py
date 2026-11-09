"""Transcribe a session video with ElevenLabs and write the transcript."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from shortgen.media import probe, require_ffmpeg, run
from shortgen.paths import display_path, file_sha256, same_source, video_paths
from shortgen.segments import Segment, group_segments, load_speakers, render_markdown, render_srt

API_PATH = "/v1/speech-to-text"
MAX_UPLOAD_BYTES = int(4.5 * 1024 * 1024 * 1024)


def transcribe(
    source: Path,
    *,
    language: str | None = None,
    num_speakers: int | None = None,
    force: bool = False,
) -> Path:
    require_ffmpeg()
    paths = video_paths(source)
    info = probe(source)
    if not info["has_audio"]:
        raise SystemExit("This video has no audio stream, so there is nothing to transcribe.")
    if not force and _is_current(paths, source):
        _log(f"Transcript is current for {display_path(source)}. Skipping ElevenLabs.")
        return paths["transcript_md"]

    digest = file_sha256(source)
    audio = _extract_audio(paths["work"], source, digest)
    payload = _call_elevenlabs(audio, language=language, num_speakers=num_speakers)
    document = _document(payload, source, info)
    _write_transcript(paths, document, info)
    _write_source(paths, source, info, digest, transcribed=True)
    _log(
        f"Language {document.get('language_code') or 'unknown'}, "
        f"{len(document['segments'])} turns, {display_path(paths['transcript_json'])}"
    )
    return paths["transcript_md"]


def render_transcript(source: Path) -> Path:
    paths = video_paths(source)
    json_path = paths["transcript_json"]
    if not json_path.is_file():
        raise SystemExit(f"No transcript at {display_path(json_path)}. Run transcribe first.")
    document = json.loads(json_path.read_text(encoding="utf-8"))
    words = document.get("words") or []
    segments = group_segments(words)
    document["segments"] = [segment.as_dict() for segment in segments]
    _write_views(paths, document, segments)
    json_path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return paths["transcript_md"]


def _is_current(paths: dict, source: Path) -> bool:
    json_path = paths["transcript_json"]
    meta_path = paths["source_json"]
    if not json_path.is_file() or not meta_path.is_file():
        return False
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    root = paths["root"]
    if not same_source(str(meta.get("source", "")), source, root):
        return False
    stat = source.stat()
    if meta.get("size") != stat.st_size or not meta.get("sha256"):
        return False
    if str(meta.get("mtime_ns")) == str(stat.st_mtime_ns):
        return True
    if file_sha256(source) == meta["sha256"]:
        meta["mtime_ns"] = str(stat.st_mtime_ns)
        meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        return True
    return False


def _extract_audio(work: Path, source: Path, digest: str) -> Path:
    work.mkdir(parents=True, exist_ok=True)
    audio = work / "audio.m4a"
    marker = work / "audio.json"
    if audio.is_file() and marker.is_file():
        try:
            saved = json.loads(marker.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            saved = {}
        if saved.get("sha256") == digest and audio.stat().st_size > 0:
            _log(f"Reusing extracted audio {display_path(audio)}")
            return audio
    _log("Extracting mono audio for transcription.")
    run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "aac",
            "-b:a",
            "64k",
            str(audio),
        ]
    )
    size = audio.stat().st_size
    if size > MAX_UPLOAD_BYTES:
        raise SystemExit("Extracted audio is over the ElevenLabs upload limit.")
    marker.write_text(json.dumps({"sha256": digest}) + "\n", encoding="utf-8")
    return audio


def _call_elevenlabs(audio: Path, *, language: str | None, num_speakers: int | None) -> dict:
    try:
        import requests
    except ImportError as exc:
        raise SystemExit("Install dependencies with: pip install -r requirements.txt") from exc

    key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not key:
        raise SystemExit("Set ELEVENLABS_API_KEY in the environment or in .env.")
    base = os.environ.get("ELEVENLABS_API_BASE", "https://api.elevenlabs.io").rstrip("/")
    model = os.environ.get("ELEVENLABS_STT_MODEL", "scribe_v2")
    data = {
        "model_id": model,
        "timestamps_granularity": "word",
        "diarize": "true",
        "tag_audio_events": "true",
    }
    if language:
        data["language_code"] = language
    if num_speakers:
        data["num_speakers"] = str(num_speakers)
    _log(f"Uploading audio to ElevenLabs ({model}).")
    with audio.open("rb") as handle:
        response = requests.post(
            f"{base}{API_PATH}",
            headers={"xi-api-key": key},
            data=data,
            files={"file": (audio.name, handle, "audio/mp4")},
            timeout=(120, 6 * 60 * 60),
        )
    if response.status_code >= 400:
        raise SystemExit(f"ElevenLabs returned {response.status_code}: {response.text[:2000]}")
    payload = response.json()
    if "words" not in payload:
        raise SystemExit(
            "ElevenLabs returned no word timings. This tool expects a single-channel transcript."
        )
    return payload


def _document(payload: dict, source: Path, info: dict) -> dict:
    words = []
    for word in payload.get("words") or []:
        words.append(
            {
                "text": word.get("text", ""),
                "start": word.get("start"),
                "end": word.get("end"),
                "type": word.get("type", "word"),
                "speaker_id": word.get("speaker_id"),
                "logprob": word.get("logprob"),
            }
        )
    segments = group_segments(words)
    return {
        "source": display_path(source),
        "model_id": os.environ.get("ELEVENLABS_STT_MODEL", "scribe_v2"),
        "language_code": payload.get("language_code"),
        "language_probability": payload.get("language_probability"),
        "audio_duration_secs": payload.get("audio_duration_secs") or info["duration"],
        "text": payload.get("text", ""),
        "words": words,
        "segments": [segment.as_dict() for segment in segments],
    }


def _write_transcript(paths: dict, document: dict, info: dict) -> None:
    paths["transcript_dir"].mkdir(parents=True, exist_ok=True)
    segments = [
        Segment(
            start=float(item["start"]),
            end=float(item["end"]),
            speaker=item.get("speaker"),
            text=item["text"],
        )
        for item in document["segments"]
    ]
    paths["transcript_json"].write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    _write_views(paths, document, segments, info)


def _write_views(paths: dict, document: dict, segments: list[Segment], info: dict | None = None) -> None:
    speakers = load_speakers(paths["speakers"])
    duration = document.get("audio_duration_secs")
    if duration is None and info is not None:
        duration = info["duration"]
    markdown = render_markdown(
        {
            "source": document.get("source") or display_path(paths["source"]),
            "language_code": document.get("language_code"),
            "language_probability": document.get("language_probability"),
            "duration": duration,
        },
        segments,
        speakers,
    )
    paths["transcript_dir"].mkdir(parents=True, exist_ok=True)
    paths["transcript_md"].write_text(markdown, encoding="utf-8")
    paths["transcript_srt"].write_text(render_srt(segments), encoding="utf-8")


def _write_source(paths: dict, source: Path, info: dict, digest: str, *, transcribed: bool) -> None:
    paths["dir"].mkdir(parents=True, exist_ok=True)
    stat = source.stat()
    meta = {
        "slug": paths["slug"],
        "source": display_path(source),
        "sha256": digest,
        "size": stat.st_size,
        "mtime_ns": str(stat.st_mtime_ns),
        "duration": info["duration"],
        "width": info["width"],
        "height": info["height"],
        "fps": info["fps"],
        "has_audio": info["has_audio"],
        "transcribed_at": datetime.now(timezone.utc).isoformat(timespec="seconds") if transcribed else None,
    }
    paths["source_json"].write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")


def _log(message: str) -> None:
    print(message, file=sys.stderr)
