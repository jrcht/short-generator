"""Cut briefed moments out of a session video and join them."""

from __future__ import annotations

import json
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from shortgen.brief import check_brief, load_brief, moment_bounds, transition_of
from shortgen.media import probe, require_ffmpeg, run
from shortgen.paths import display_path, video_paths
from shortgen.segments import load_words
from shortgen.timeutil import round_s
from shortgen.transitions import TRANSITION_NAMES

# Fast-seek this far before the moment, then trim on decoded frames.
# -ss before -i alone lands on a keyframe and clips the first word.
PREROLL = 10.0
OFFSET_SAFETY = 0.02


@dataclass
class PlannedClip:
    id: str
    start: float
    end: float
    requested_start: float
    requested_end: float
    speaker: str | None
    quote: str
    why: str

    @property
    def duration(self) -> float:
        return self.end - self.start

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "requested_start": self.requested_start,
            "requested_end": self.requested_end,
            "start": self.start,
            "end": self.end,
            "speaker": self.speaker,
            "quote": self.quote,
            "why": self.why,
        }


def build_plan(
    brief: dict,
    *,
    words: list[dict] | None = None,
    duration: float | None = None,
    pad: float = 0.12,
) -> tuple[list[PlannedClip], list[str]]:
    report = check_brief(brief, duration=duration, words=words)
    if report.errors:
        raise SystemExit("Brief is not cuttable:\n- " + "\n- ".join(report.errors))
    clips = []
    for moment in brief["moments"]:
        requested_start, requested_end = moment_bounds(moment)
        start, end = snap_range(requested_start, requested_end, words or [], pad, duration)
        clips.append(
            PlannedClip(
                id=str(moment["id"]),
                start=start,
                end=end,
                requested_start=requested_start,
                requested_end=requested_end,
                speaker=moment.get("speaker"),
                quote=str(moment.get("quote") or ""),
                why=str(moment.get("why") or ""),
            )
        )
    return clips, list(report.warnings)


def snap_range(
    start: float,
    end: float,
    words: list[dict],
    pad: float,
    duration: float | None,
) -> tuple[float, float]:
    spoken = [
        word
        for word in words
        if word.get("type") != "spacing" and word.get("start") is not None and word.get("end") is not None
    ]
    if spoken:
        start = _snap_edge(start, spoken, "start")
        end = _snap_edge(end, spoken, "end")
    if end < start:
        end = start
    start = max(0.0, start - pad)
    end = end + pad
    if duration is not None:
        end = min(duration, end)
    if end - start < 0.3:
        raise SystemExit(f"Moment from {start:.3f} to {end:.3f} is too short to cut.")
    return round_s(start), round_s(end)


def transition_offsets(durations: list[float], fade: float) -> list[float]:
    offsets = []
    elapsed = 0.0
    for index, duration in enumerate(durations[:-1]):
        elapsed += duration
        offsets.append(elapsed - fade * (index + 1))
    return offsets


def build_filter(durations: list[float], transition: str, fade: float, has_audio: bool) -> str:
    if transition == "cut":
        return _concat_filter(len(durations), has_audio)
    xfade = TRANSITION_NAMES[transition]
    offsets = transition_offsets(durations, fade)
    parts = []
    previous = "[0:v]"
    for index, offset in enumerate(offsets, start=1):
        output = "[v]" if index == len(offsets) else f"[vx{index}]"
        safe_offset = max(0.0, offset - OFFSET_SAFETY)
        parts.append(
            f"{previous}[{index}:v]xfade=transition={xfade}:duration={fade:.3f}:offset={safe_offset:.3f}{output}"
        )
        previous = output
    if has_audio:
        previous_audio = "[0:a]"
        for index in range(1, len(durations)):
            output = "[a]" if index == len(durations) - 1 else f"[ax{index}]"
            parts.append(f"{previous_audio}[{index}:a]acrossfade=d={fade:.3f}:c1=tri:c2=tri{output}")
            previous_audio = output
    return ";".join(parts)


def cut_video(
    source: Path,
    brief_path: Path,
    output: Path,
    *,
    words: list[dict] | None = None,
    pad: float = 0.12,
    transition: str | None = None,
    transition_duration: float | None = None,
    preset: str = "medium",
    crf: int = 18,
    dry_run: bool = False,
) -> dict:
    brief = load_brief(brief_path)
    if transition or transition_duration is not None:
        brief = _override_transition(brief, transition, transition_duration)
    info = probe(source)
    clips, warnings = build_plan(brief, words=words, duration=info["duration"], pad=pad)
    name, fade = transition_of(brief)
    plan = {
        "source": display_path(source),
        "brief": display_path(brief_path),
        "title": brief.get("title"),
        "transition": {"type": name, "duration": fade},
        "pad": pad,
        "clips": [clip.as_dict() for clip in clips],
        "output": display_path(output),
    }
    for warning in warnings:
        _log(f"warning: {warning}")
    if dry_run:
        return plan
    require_ffmpeg()
    work = output.parent / "work"
    rendering = output.with_name("short.rendering.mp4")
    try:
        _render(source, clips, rendering, transition=name, fade=fade, info=info, preset=preset, crf=crf, work=work)
        output.parent.mkdir(parents=True, exist_ok=True)
        rendering.replace(output)
        finished = probe(output)
        plan["output_duration"] = round_s(finished["duration"])
        plan["created_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        edit_path = output.parent / "edit.json"
        _archive_edit(edit_path)
        edit_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    finally:
        if rendering.exists():
            rendering.unlink()
    return plan


def default_brief_path(source: Path, name: str) -> Path:
    return video_paths(source, name)["brief_json"]


def _render(
    source: Path,
    clips: list[PlannedClip],
    output: Path,
    *,
    transition: str,
    fade: float,
    info: dict,
    preset: str,
    crf: int,
    work: Path,
) -> None:
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    clip_paths: list[Path] = []
    try:
        for index, clip in enumerate(clips):
            clip_path = work / f"clip-{index:02d}.mp4"
            _extract_clip(source, clip, clip_path, has_audio=info["has_audio"], fps=info["fps"], preset=preset, crf=crf)
            clip_paths.append(clip_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        if len(clip_paths) == 1:
            run(
                [
                    "ffmpeg",
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    str(clip_paths[0]),
                    "-c",
                    "copy",
                    "-movflags",
                    "+faststart",
                    str(output),
                ]
            )
            return
        joined = probe(clip_paths[0])
        durations = [probe(path)["duration"] for path in clip_paths]
        if transition != "cut":
            shortest = min(durations)
            if fade >= shortest - 0.04:
                raise SystemExit(
                    f"Transition {fade:.2f}s does not fit the shortest cut ({shortest:.2f}s). "
                    "Lengthen the moment or shorten the transition."
                )
        _join(clip_paths, output, transition, fade, durations, joined["has_audio"], preset, crf)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _extract_clip(
    source: Path,
    clip: PlannedClip,
    output: Path,
    *,
    has_audio: bool,
    fps: str,
    preset: str,
    crf: int,
) -> None:
    fast = max(0.0, clip.start - PREROLL) if clip.start > 0.05 else None
    accurate = clip.start if fast is None else clip.start - fast
    filters = ["scale=trunc(iw/2)*2:trunc(ih/2)*2", "format=yuv420p", "setpts=PTS-STARTPTS"]
    if fps:
        filters.insert(0, f"fps={fps}")
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    if fast:
        cmd += ["-ss", f"{fast:.3f}"]
    cmd += ["-i", str(source)]
    if accurate > 0:
        cmd += ["-ss", f"{accurate:.3f}"]
    cmd += ["-t", f"{clip.duration:.3f}", "-map", "0:v:0"]
    if has_audio:
        cmd += ["-map", "0:a:0"]
    cmd += ["-vf", ",".join(filters)]
    if has_audio:
        cmd += ["-af", "asetpts=PTS-STARTPTS,aformat=sample_fmts=fltp:channel_layouts=stereo,aresample=48000"]
    cmd += ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p"]
    if has_audio:
        cmd += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]
    else:
        cmd += ["-an"]
    cmd += [str(output)]
    run(cmd)


def _join(
    clips: list[Path],
    output: Path,
    transition: str,
    fade: float,
    durations: list[float],
    has_audio: bool,
    preset: str,
    crf: int,
) -> None:
    graph = build_filter(durations, transition, fade, has_audio)
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    for clip in clips:
        cmd += ["-i", str(clip)]
    cmd += ["-filter_complex", graph, "-map", "[v]"]
    if has_audio:
        cmd += ["-map", "[a]"]
    cmd += ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p"]
    if has_audio:
        cmd += ["-c:a", "aac", "-b:a", "192k"]
    else:
        cmd += ["-an"]
    cmd += ["-movflags", "+faststart", str(output)]
    run(cmd)


def _concat_filter(count: int, has_audio: bool) -> str:
    if has_audio:
        streams = "".join(f"[{index}:v][{index}:a]" for index in range(count))
        return f"{streams}concat=n={count}:v=1:a=1[v][a]"
    streams = "".join(f"[{index}:v]" for index in range(count))
    return f"{streams}concat=n={count}:v=1:a=0[v]"


def _snap_edge(time: float, words: list[dict], edge: str) -> float:
    for word in words:
        if float(word["start"]) - 0.02 <= time <= float(word["end"]) + 0.02:
            return float(word[edge])
    return time


def _override_transition(brief: dict, name: str | None, duration: float | None) -> dict:
    raw = brief.get("transition")
    if isinstance(raw, str):
        raw = {"type": raw}
    if not isinstance(raw, dict):
        raw = {}
    updated = dict(raw)
    if name:
        updated["type"] = name
    if duration is not None:
        updated["duration"] = duration
    return {**brief, "transition": updated}


def _archive_edit(path: Path) -> None:
    if not path.is_file():
        return
    history = path.parent / "history"
    history.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    shutil.copy2(path, history / f"{stamp}.json")


def _log(message: str) -> None:
    print(message, file=sys.stderr)
