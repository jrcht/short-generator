"""Large captions for the moments of a short, timed to the spoken words."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

FONT = Path("/System/Library/Fonts/Supplemental/Arial Black.ttf")
FONT_SIZE = 84
STROKE = 10
MAX_TEXT_WIDTH = 960

# Matches the xfade offset trim in the cutter.
OFFSET_SAFETY = 0.02
LINE_CHARS = 18
FILLERS = {"uh", "um", "er", "ah", "eh"}


@dataclass(frozen=True)
class Cue:
    start: float
    end: float
    text: str


def cues_for_clips(
    clips: list[dict],
    words: list[dict],
    fade: float,
    texts: list[list[str] | None] | None = None,
) -> list[Cue]:
    """One readable line at a time, placed on the joined short.

    `texts` is an optional line list per clip. A list is used only when it has
    one entry for every line that clip would have generated.
    """
    return [Cue(start=row["start"], end=row["end"], text=row["text"]) for row in cue_rows(clips, words, fade, texts)]


def cue_rows(
    clips: list[dict],
    words: list[dict],
    fade: float,
    texts: list[list[str] | None] | None = None,
) -> list[dict]:
    """The same lines as `cues_for_clips`, with the clip id attached."""
    durations = [max(0.0, float(clip["end"]) - float(clip["start"])) for clip in clips]
    rows: list[dict] = []
    for index, clip in enumerate(clips):
        origin = _clip_start(index, durations, fade)
        clip_end = origin + durations[index]
        if index + 1 < len(clips):
            clip_end = min(clip_end, _clip_start(index + 1, durations, fade))
        spoken = [
            item
            for item in _clip_words(words, float(clip["start"]), float(clip["end"]), origin)
            if item[0] < clip_end - 0.05
        ]
        if not spoken:
            continue
        phrases = _phrases(spoken, clip_end)
        custom = _matching_lines(texts, index, len(phrases))
        for phrase_index, phrase in enumerate(phrases):
            text = _wrap(_flat(custom[phrase_index])) if custom else phrase.text
            rows.append(
                {
                    "clip": str(clip.get("id") or index),
                    "start": phrase.start,
                    "end": phrase.end,
                    "text": text,
                }
            )
    return rows


def _matching_lines(
    texts: list[list[str] | None] | None,
    index: int,
    count: int,
) -> list[str] | None:
    if not texts or index >= len(texts):
        return None
    custom = texts[index]
    if not custom or len(custom) != count or any(not isinstance(line, str) for line in custom):
        return None
    return custom


def _flat(text: str) -> str:
    return " ".join(text.split())


def _clip_start(index: int, durations: list[float], fade: float) -> float:
    if index <= 0:
        return 0.0
    if fade <= 0:
        return sum(durations[:index])
    return max(0.0, sum(durations[:index]) - fade * index - OFFSET_SAFETY)


def _clip_words(
    words: list[dict],
    clip_start: float,
    clip_end: float,
    origin: float,
) -> list[tuple[float, float, str]]:
    spoken: list[tuple[float, float, str]] = []
    for word in words:
        if word.get("type") == "spacing" or word.get("start") is None or word.get("end") is None:
            continue
        start = float(word["start"])
        end = float(word["end"])
        # A word that began before the cut, or that only just starts as the cut ends,
        # belongs to the neighbouring sentence.
        if start < clip_start or start >= clip_end - 0.15:
            continue
        shown = _display(str(word.get("text") or ""))
        if shown is None:
            continue
        if spoken and _stem(spoken[-1][2]) == _stem(shown):
            previous = spoken[-1][2].rstrip(",;:")
            if shown[-1] in ".?!" and previous[-1] not in ".?!":
                previous += shown[-1]
            spoken[-1] = (
                spoken[-1][0],
                max(spoken[-1][1], end - clip_start + origin),
                previous,
            )
            continue
        spoken.append((start - clip_start + origin, end - clip_start + origin, shown))
    return spoken


def _phrases(words: list[tuple[float, float, str]], clip_end: float) -> list[Cue]:
    groups: list[list[tuple[float, float, str]]] = []
    current: list[tuple[float, float, str]] = []
    for word in words:
        if current and current[-1][2][-1] in ".?!":
            groups.append(current)
            current = []
        if current and _lines(current + [word]) > 2:
            groups.append(current)
            current = [word]
        else:
            current.append(word)
    if current:
        groups.append(current)

    cues: list[Cue] = []
    for index, group in enumerate(groups):
        start = group[0][0]
        if index + 1 < len(groups):
            end = groups[index + 1][0][0]
        else:
            end = min(clip_end, group[-1][1] + 0.2)
        if end < start + 0.25:
            end = min(clip_end, start + 0.25)
        text = _wrap(" ".join(item[2] for item in group))
        cues.append(Cue(start=round(start, 3), end=round(end, 3), text=text))
    return cues


def _lines(words: list[tuple[float, float, str]]) -> int:
    return _wrap(" ".join(item[2] for item in words)).count("\n") + 1


def _wrap(text: str) -> str:
    lines: list[str] = []
    current = ""
    for word in text.split():
        trial = word if not current else f"{current} {word}"
        if current and len(trial) > LINE_CHARS:
            lines.append(current)
            current = word
        else:
            current = trial
    if current:
        lines.append(current)
    return "\n".join(lines)


def _display(token: str) -> str | None:
    core = token.strip().strip("\"'")
    if not core:
        return None
    punct = ""
    while core and core[-1] in ".,?!;:":
        punct = core[-1] + punct
        core = core[:-1]
    if not core or (core.endswith("-") and len(core) <= 4):
        return None
    if "-" in core:
        left, right = core.split("-", 1)
        if left and right and len(left) <= 3 and right.lower().startswith(left.lower()[: min(2, len(left))]):
            core = right
    if core.lower() in FILLERS:
        return None
    return core + punct


def _stem(text: str) -> str:
    return text.lower().strip(".,?!;:")


def write_caption(text: str, dest: Path) -> tuple[int, int]:
    """A transparent card with the line in large type. Returns width, height."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise SystemExit("Install Pillow to burn captions: pip install pillow") from exc
    if not FONT.is_file():
        raise SystemExit(f"Caption font not found at {FONT}.")
    font = ImageFont.truetype(str(FONT), FONT_SIZE)
    lines = [line for line in text.split("\n") if line]
    if not lines:
        raise SystemExit("A caption cue was empty.")
    measure = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
    boxes = [measure.textbbox((0, 0), line, font=font, stroke_width=STROKE) for line in lines]
    line_h = max(box[3] - box[1] for box in boxes)
    text_w = max(box[2] - box[0] for box in boxes)
    gap = 8
    pad_x, pad_y = 36, 22
    width = _even_px(min(MAX_TEXT_WIDTH + pad_x * 2, text_w + pad_x * 2))
    height = _even_px(line_h * len(lines) + gap * (len(lines) - 1) + pad_y * 2)
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=22, fill=(0, 0, 0, 178))
    y = pad_y
    for line, box in zip(lines, boxes):
        line_w = box[2] - box[0]
        x = (width - line_w) / 2 - box[0]
        draw.text((x, y - box[1]), line, font=font, fill="white", stroke_width=STROKE, stroke_fill="black")
        y += line_h + gap
    dest.parent.mkdir(parents=True, exist_ok=True)
    image.save(dest)
    return width, height


def caption_top(window_top: int, window_height: int, text_height: int, pad: int = 48) -> int:
    return max(0, window_top + window_height - pad - text_height)


def _even_px(value: int) -> int:
    value = max(2, int(value))
    return value if value % 2 == 0 else value + 1
