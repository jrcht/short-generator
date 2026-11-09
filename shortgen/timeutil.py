"""Timecode parsing and formatting.

Briefs may use seconds (`372.4`) or a clock (`01:02:03.400`).
"""

from __future__ import annotations


def parse_timecode(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"timecode must be seconds or HH:MM:SS, got {value!r}")
    if isinstance(value, (int, float)):
        if value != value or value < 0:
            raise ValueError(f"timecode must be a positive number, got {value!r}")
        return float(value)

    text = value.strip().replace(",", ".")
    if not text:
        raise ValueError("timecode is empty")
    parts = text.split(":")
    if len(parts) > 3:
        raise ValueError(f"timecode has too many parts: {value}")
    try:
        numbers = [float(part) for part in parts]
    except ValueError as exc:
        raise ValueError(f"timecode is not a number: {value}") from exc
    if any(number < 0 for number in numbers):
        raise ValueError(f"timecode must be positive: {value}")
    seconds = 0.0
    for number in numbers:
        seconds = seconds * 60 + number
    return seconds


def round_s(value: float) -> float:
    return float(f"{value:.3f}")


def format_timecode(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    total_ms = int(round(seconds * 1000))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def format_srt_time(seconds: float) -> str:
    return format_timecode(seconds).replace(".", ",")
