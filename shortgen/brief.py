"""Validate a brief and render it for a person to read."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from shortgen.timeutil import format_timecode, parse_timecode, round_s
from shortgen.transitions import DEFAULT_FADE, DEFAULT_TRANSITION, TRANSITION_NAMES


@dataclass
class BriefReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def check_brief(data: object, *, duration: float | None = None, words: list[dict] | None = None) -> BriefReport:
    report = BriefReport()
    if not isinstance(data, dict):
        report.errors.append("Brief must be a JSON object.")
        return report

    title = data.get("title")
    if not isinstance(title, str) or not title.strip():
        report.errors.append("title is required.")
    hook = data.get("hook")
    if not isinstance(hook, str) or not hook.strip():
        report.errors.append("hook is required. One sentence on what the short is about.")

    _check_optional_text(data, "notes", report)
    _check_optional_text(data, "audience", report)
    _check_avoid(data.get("avoid"), report)
    transition, fade = _check_transition(data.get("transition"), report)
    if "subtitles" in data and not isinstance(data.get("subtitles"), bool):
        report.errors.append("subtitles must be true or false.")
    moments = data.get("moments")
    if not isinstance(moments, list) or not moments:
        report.errors.append("moments must be a non-empty list.")
        return report

    parsed: list[tuple[float, float, str]] = []
    seen: set[str] = set()
    for index, moment in enumerate(moments, start=1):
        label = f"moments[{index}]"
        if not isinstance(moment, dict):
            report.errors.append(f"{label} must be an object.")
            continue
        moment_id = str(moment.get("id") or "").strip()
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,40}", moment_id):
            report.errors.append(f"{label}.id must be a short lowercase slug.")
        elif moment_id in seen:
            report.errors.append(f"Duplicate moment id {moment_id}.")
        else:
            seen.add(moment_id)
        start, end = _check_range(moment, label, report)
        if start is None or end is None:
            continue
        if duration is not None and end > duration + 0.25:
            report.errors.append(f"{label} ends at {end:.3f}s, past the video duration {duration:.3f}s.")
        if end - start < 0.4:
            report.errors.append(f"{label} is shorter than 0.4s.")
        elif end - start < 1.5:
            report.warnings.append(f"{moment_id or label} is under 1.5s. Keep it only if the line is complete.")
        if end - start > 20:
            report.warnings.append(f"{moment_id or label} is longer than 20s. A short usually wants a tighter line.")
        quote = moment.get("quote")
        if not isinstance(quote, str) or not quote.strip():
            report.errors.append(f"{label}.quote must be the words spoken in the range.")
        elif words:
            warning = quote_mismatch(quote, words, start, end)
            if warning:
                report.warnings.append(f"{moment_id or label}: {warning}")
        why = moment.get("why")
        if not isinstance(why, str) or not why.strip():
            report.errors.append(f"{label}.why must say what this moment does in the short.")
        if not str(moment.get("speaker") or "").strip():
            report.warnings.append(f"{moment_id or label} has no speaker.")
        _check_gain(moment, label, report)
        _check_captions(moment, label, report)
        parsed.append((start, end, moment_id or label))

    if len(moments) > 6:
        report.warnings.append("More than 6 moments. The short will feel chopped.")
    if transition != "cut" and parsed:
        shortest = min(end - start for start, end, _moment_id in parsed)
        if fade >= shortest:
            report.errors.append(
                f"transition {fade:.2f}s is at least as long as the shortest moment ({shortest:.2f}s)."
            )
    _warn_order_and_overlap(parsed, report)
    _warn_target(data.get("target_seconds"), parsed, fade if transition != "cut" else 0.0, report)
    return report


def quote_mismatch(quote: str, words: list[dict], start: float, end: float) -> str | None:
    spoken = []
    for word in words:
        if word.get("type") == "spacing" or word.get("start") is None or word.get("end") is None:
            continue
        if float(word["end"]) < start - 0.35 or float(word["start"]) > end + 0.35:
            continue
        spoken.append(str(word.get("text", "")))
    asked = _tokens(quote)
    if not asked:
        return None
    if not spoken:
        return "that range has no transcript words. Check the timecode."
    said = set(_tokens(" ".join(spoken)))
    hits = sum(1 for token in asked if token in said)
    if hits / len(asked) < 0.6:
        return "quote does not match the transcript in that range. Copy the spoken words."
    return None


def transition_of(data: dict) -> tuple[str, float]:
    raw = data.get("transition")
    if isinstance(raw, str):
        raw = {"type": raw}
    if not isinstance(raw, dict):
        raw = {}
    name = str(raw.get("type") or DEFAULT_TRANSITION)
    if name == "cut":
        return name, 0.0
    duration = raw.get("duration", DEFAULT_FADE)
    return name, float(duration)


def render_brief(data: dict) -> str:
    name, fade = transition_of(data)
    moments = data.get("moments") or []
    ranges = []
    for moment in moments:
        ranges.append((parse_timecode(moment["start"]), parse_timecode(moment["end"])))
    planned = sum(end - start for start, end in ranges)
    if name != "cut" and len(ranges) > 1:
        planned -= fade * (len(ranges) - 1)
    transition_line = "cut" if name == "cut" else f"{name} {fade:.2f}s"
    if data.get("subtitles") is False:
        transition_line += " · subtitles off"
    target = data.get("target_seconds")
    target_line = f"{target}s" if target else "unset"
    lines = [
        f"# {data.get('title', '').strip()}",
        "",
        str(data.get("hook") or "").strip(),
        "",
        f"Target: {target_line} · Transition: {transition_line} · Planned: {planned:.1f}s",
        "",
    ]
    audience = str(data.get("audience") or "").strip()
    if audience:
        lines.extend([f"Audience: {audience}", ""])
    notes = str(data.get("notes") or "").strip()
    if notes:
        lines.extend([notes, ""])
    avoid = data.get("avoid") or []
    if avoid:
        lines.append("Leave out: " + "; ".join(str(item) for item in avoid))
        lines.append("")
    for index, moment in enumerate(moments, start=1):
        start = parse_timecode(moment["start"])
        end = parse_timecode(moment["end"])
        speaker = str(moment.get("speaker") or "speaker")
        voice = ""
        gain = moment.get("gain")
        if isinstance(gain, (int, float)) and not isinstance(gain, bool) and abs(float(gain) - 1) > 0.001:
            voice = f" · voice {float(gain):.0%}"
        lines.extend(
            [
                f"## {index}. {moment.get('id')} · {format_timecode(start)}–{format_timecode(end)} · {speaker}{voice}",
                "",
                f"> {str(moment.get('quote') or '').strip()}",
                "",
                str(moment.get("why") or "").strip(),
                "",
            ]
        )
    return "\n".join(lines)


def load_brief(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"{path} must contain a JSON object.")
    return data


def moment_bounds(moment: dict) -> tuple[float, float]:
    return round_s(parse_timecode(moment["start"])), round_s(parse_timecode(moment["end"]))


def _check_gain(moment: dict, label: str, report: BriefReport) -> None:
    if "gain" not in moment or moment.get("gain") is None:
        return
    raw = moment.get("gain")
    if isinstance(raw, bool):
        report.errors.append(f"{label}.gain must be a number from 0 to 2.")
        return
    try:
        value = float(raw)
    except (TypeError, ValueError):
        report.errors.append(f"{label}.gain must be a number from 0 to 2.")
        return
    if value < 0 or value > 2:
        report.errors.append(f"{label}.gain must be a number from 0 to 2.")


def _check_captions(moment: dict, label: str, report: BriefReport) -> None:
    captions = moment.get("captions")
    if captions is None:
        return
    if not isinstance(captions, list) or any(not isinstance(line, str) for line in captions):
        report.errors.append(f"{label}.captions must be a list of lines.")


def _check_optional_text(data: dict, key: str, report: BriefReport) -> None:
    value = data.get(key)
    if value is not None and not isinstance(value, str):
        report.errors.append(f"{key} must be a string.")


def _check_avoid(value: object, report: BriefReport) -> None:
    if value is None:
        return
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        report.errors.append("avoid must be a list of strings.")


def _check_transition(value: object, report: BriefReport) -> tuple[str, float]:
    if value is None:
        return DEFAULT_TRANSITION, DEFAULT_FADE
    if isinstance(value, str):
        value = {"type": value}
    if not isinstance(value, dict):
        report.errors.append("transition must be a name or an object with type and duration.")
        return DEFAULT_TRANSITION, DEFAULT_FADE
    name = str(value.get("type") or DEFAULT_TRANSITION)
    if name not in TRANSITION_NAMES:
        report.errors.append(
            f"Unknown transition {name!r}. Use one of: {', '.join(TRANSITION_NAMES)}."
        )
        name = DEFAULT_TRANSITION
    if name == "cut":
        return name, 0.0
    raw_duration = value.get("duration", DEFAULT_FADE)
    try:
        duration = float(raw_duration)
    except (TypeError, ValueError):
        report.errors.append("transition.duration must be a number of seconds.")
        return name, DEFAULT_FADE
    if duration <= 0 or duration > 2:
        report.errors.append("transition.duration must be between 0 and 2 seconds.")
    return name, duration


def _check_range(moment: dict, label: str, report: BriefReport) -> tuple[float | None, float | None]:
    try:
        start = parse_timecode(moment.get("start"))
        end = parse_timecode(moment.get("end"))
    except ValueError as exc:
        report.errors.append(f"{label}: {exc}")
        return None, None
    if end <= start:
        report.errors.append(f"{label} end must be after start.")
        return None, None
    return start, end


def _warn_order_and_overlap(parsed: list[tuple[float, float, str]], report: BriefReport) -> None:
    starts = [start for start, _end, _moment_id in parsed]
    if starts != sorted(starts):
        report.warnings.append("Moments are not in source order. Keep that only for a deliberate cold open.")
    ordered = sorted(parsed, key=lambda item: item[0])
    for previous, current in zip(ordered, ordered[1:]):
        if current[0] < previous[1]:
            report.warnings.append(f"{previous[2]} and {current[2]} overlap. Each line would play twice.")


def _warn_target(target: object, parsed: list[tuple[float, float, str]], fade: float, report: BriefReport) -> None:
    if target is None:
        return
    try:
        seconds = float(target)
    except (TypeError, ValueError):
        report.errors.append("target_seconds must be a number.")
        return
    if seconds <= 0:
        report.errors.append("target_seconds must be greater than 0.")
        return
    if seconds < 15 or seconds > 90:
        report.warnings.append("target_seconds is outside the usual 15–90s range.")
    if not parsed:
        return
    planned = sum(end - start for start, end, _moment_id in parsed)
    if len(parsed) > 1:
        planned -= fade * (len(parsed) - 1)
    if planned < seconds * 0.5 or planned > seconds * 1.5:
        report.warnings.append(
            f"Planned picture is {planned:.1f}s and the target is {seconds:.0f}s."
        )


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())
