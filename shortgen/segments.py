"""Group word timings into speaker turns a person can read."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from shortgen.timeutil import format_srt_time, format_timecode, round_s

PAUSE_SECONDS = 0.8
MAX_CHARS = 320
MAX_DURATION = 20.0


@dataclass
class Segment:
    start: float
    end: float
    speaker: str | None
    text: str

    def as_dict(self) -> dict:
        return {
            "start": round_s(self.start),
            "end": round_s(self.end),
            "speaker": self.speaker,
            "text": self.text,
        }


def group_segments(words: list[dict]) -> list[Segment]:
    groups: list[list[dict]] = []
    current: list[dict] = []
    for word in words:
        if current and _should_break(current, word):
            groups.append(current)
            current = []
        if word.get("type") == "spacing" and not current:
            continue
        current.append(word)
    if _spoken(current):
        groups.append(current)

    segments: list[Segment] = []
    for items in groups:
        spoken = _spoken(items)
        if not spoken:
            continue
        start = float(spoken[0].get("start") or 0)
        end = float(spoken[-1].get("end") or start)
        speaker = next((item.get("speaker_id") for item in spoken if item.get("speaker_id")), None)
        text = "".join(item.get("text", "") for item in items).strip()
        if not text:
            continue
        segments.append(Segment(start=start, end=end, speaker=speaker, text=text))
    return segments


def load_words(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    words = data.get("words") or []
    return words if isinstance(words, list) else []


def load_speakers(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"{path} must be an object of speaker id to name.")
    return {str(key): str(value) for key, value in data.items()}


def render_markdown(meta: dict, segments: list[Segment], speakers: dict[str, str]) -> str:
    language = meta.get("language_code") or "unknown"
    probability = meta.get("language_probability")
    language_line = language if probability is None else f"{language} ({float(probability):.2f})"
    duration = meta.get("duration")
    duration_line = format_timecode(float(duration)) if duration else "unknown"
    lines = [
        "# Transcript",
        "",
        f"Source: {meta.get('source', '')}",
        f"Language: {language_line}",
        f"Duration: {duration_line}",
        "",
        "## Index",
        "",
    ]
    index = _index_lines(segments, speakers)
    lines.extend(index or ["- (empty)"])
    lines.extend(["", "## Transcript", ""])
    if not segments:
        lines.append("_No speech detected._")
        lines.append("")
        return "\n".join(lines)
    for segment in segments:
        label = _speaker_label(segment.speaker, speakers)
        span = f"{format_timecode(segment.start)}–{format_timecode(segment.end)}"
        lines.extend([f"### {span} · {label}", "", segment.text, ""])
    return "\n".join(lines)


def render_srt(segments: list[Segment]) -> str:
    blocks = []
    for index, segment in enumerate(segments, start=1):
        blocks.append(
            f"{index}\n{format_srt_time(segment.start)} --> {format_srt_time(segment.end)}\n{segment.text}\n"
        )
    return "\n".join(blocks) + ("\n" if blocks else "")


def _spoken(items: list[dict]) -> list[dict]:
    return [item for item in items if item.get("type") != "spacing"]


def _should_break(items: list[dict], word: dict) -> bool:
    if word.get("type") == "spacing":
        return False
    previous = _spoken(items)
    if not previous:
        return False
    last = previous[-1]
    previous_speaker = last.get("speaker_id")
    next_speaker = word.get("speaker_id")
    if previous_speaker and next_speaker and previous_speaker != next_speaker:
        return True
    last_end = last.get("end")
    next_start = word.get("start")
    if last_end is not None and next_start is not None and float(next_start) - float(last_end) > PAUSE_SECONDS:
        return True
    text = "".join(item.get("text", "") for item in items)
    first_start = previous[0].get("start")
    duration = 0.0
    if first_start is not None and last_end is not None:
        duration = float(last_end) - float(first_start)
    if _ends_sentence(text) and (len(text) > MAX_CHARS or duration > MAX_DURATION):
        return True
    return False


def _ends_sentence(text: str) -> bool:
    stripped = text.rstrip().rstrip("\"'”’")
    return stripped.endswith((".", "?", "!", "…"))


def _speaker_label(speaker: str | None, speakers: dict[str, str]) -> str:
    if not speaker:
        return "speaker"
    return speakers.get(speaker, speaker)


def _index_lines(segments: list[Segment], speakers: dict[str, str], step: float = 300.0) -> list[str]:
    lines = []
    next_mark = 0.0
    for segment in segments:
        if segment.start + 0.01 < next_mark:
            continue
        label = _speaker_label(segment.speaker, speakers)
        preview = segment.text.replace("\n", " ")
        if len(preview) > 80:
            preview = preview[:77].rstrip() + "..."
        lines.append(f"- {format_timecode(segment.start)} {label} — {preview}")
        next_mark = (int(segment.start // step) + 1) * step
    return lines
