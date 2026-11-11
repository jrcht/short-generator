"""Command line for the three steps: transcribe, brief, cut."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from shortgen.brief import check_brief, load_brief, render_brief
from shortgen.cut import cut_video, default_brief_path
from shortgen.media import extract_frame
from shortgen.paths import display_path, find_root, resolve_existing, resolve_source, slug_text, video_paths
from shortgen.segments import load_words
from shortgen.timeutil import parse_timecode
from shortgen.transcribe import render_transcript, transcribe
from shortgen.view import run_viewer


def main(argv: list[str] | None = None) -> int:
    _load_dotenv(find_root() / ".env")
    parser = argparse.ArgumentParser(
        prog="shortgen",
        description="Cut a short from a long event-session video. Each step can be run on its own.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    path_cmd = commands.add_parser("path", help="Print the artifact paths for a video.")
    path_cmd.add_argument("video")
    path_cmd.add_argument("--name", default="main", help="Short name. Default: main.")
    path_cmd.set_defaults(func=_cmd_path)

    transcribe_cmd = commands.add_parser("transcribe", help="Transcribe a video with ElevenLabs.")
    transcribe_cmd.add_argument("video")
    transcribe_cmd.add_argument("--language", help="ISO language code, for example en or fr.")
    transcribe_cmd.add_argument("--num-speakers", type=int, help="Hint for diarization, up to 32.")
    transcribe_cmd.add_argument("--force", action="store_true", help="Call ElevenLabs even if the transcript is current.")
    transcribe_cmd.set_defaults(func=_cmd_transcribe)

    transcript_cmd = commands.add_parser("transcript", help="Rewrite the readable transcript from saved word timings.")
    transcript_commands = transcript_cmd.add_subparsers(dest="transcript_command", required=True)
    render_cmd = transcript_commands.add_parser("render", help="Regenerate transcript.md and transcript.srt.")
    render_cmd.add_argument("video")
    render_cmd.set_defaults(func=_cmd_transcript_render)

    brief_cmd = commands.add_parser("brief", help="Check or render a video brief.")
    brief_commands = brief_cmd.add_subparsers(dest="brief_command", required=True)
    for name, func, help_text in (
        ("check", _cmd_brief_check, "Validate a brief. Prints ok, or the errors."),
        ("render", _cmd_brief_render, "Write the readable Markdown next to the brief JSON."),
    ):
        sub = brief_commands.add_parser(name, help=help_text)
        sub.add_argument("target", help="A video path, or a path to a brief JSON file.")
        sub.add_argument("--name", default="main", help="Brief name when target is a video. Default: main.")
        sub.set_defaults(func=func)

    cut_cmd = commands.add_parser("cut", help="Cut the briefed moments and write a short.")
    cut_cmd.add_argument("video")
    cut_cmd.add_argument("--name", default="main", help="Short name. Selects the brief and the output folder.")
    cut_cmd.add_argument("--brief", help="Brief JSON. Defaults to generated/<slug>/brief/<name>.json.")
    cut_cmd.add_argument("--transition", help="Overrides the brief for this run. Does not rewrite the brief.")
    cut_cmd.add_argument("--transition-duration", type=float, help="Transition length in seconds.")
    cut_cmd.add_argument("--pad", type=float, default=0.12, help="Seconds of air added on each side of a moment.")
    cut_cmd.add_argument("--preset", default="medium", help="libx264 preset.")
    cut_cmd.add_argument("--crf", type=int, default=18)
    cut_cmd.add_argument("--dry-run", action="store_true", help="Print the cut plan without writing a video.")
    cut_cmd.add_argument(
        "--no-package",
        action="store_true",
        help="Keep the session frame. Skip the opening, logo, watch card, and ending.",
    )
    cut_cmd.set_defaults(func=_cmd_cut)

    frame_cmd = commands.add_parser("frame", help="Save a still from the session for the web article.")
    frame_cmd.add_argument("video")
    frame_cmd.add_argument("--at", required=True, help="Time in seconds or HH:MM:SS.mmm.")
    frame_cmd.add_argument("--name", required=True, help="File name slug, for example hero. Written as <name>.jpg.")
    frame_cmd.set_defaults(func=_cmd_frame)

    view_cmd = commands.add_parser("view", help="Open a player to trim, set the transition, voice, and subtitles.")
    view_cmd.add_argument("video")
    view_cmd.add_argument("--name", default="main", help="Short name. Default: main.")
    view_cmd.add_argument("--port", type=int, default=8733, help="Port for the local player. Default: 8733.")
    view_cmd.add_argument("--no-open", action="store_true", help="Do not open a browser.")
    view_cmd.set_defaults(func=_cmd_view)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1


def _cmd_path(args: argparse.Namespace) -> int:
    source = resolve_source(args.video)
    paths = video_paths(source, args.name)
    report = {
        "slug": paths["slug"],
        "name": paths["name"],
        "source": display_path(source),
        "transcript": display_path(paths["transcript_json"]),
        "transcript_md": display_path(paths["transcript_md"]),
        "transcript_exists": paths["transcript_json"].is_file(),
        "brief": display_path(paths["brief_json"]),
        "brief_md": display_path(paths["brief_md"]),
        "brief_exists": paths["brief_json"].is_file(),
        "short": display_path(paths["short"]),
        "article": display_path(paths["article"]),
        "article_exists": paths["article"].is_file(),
        "short_exists": paths["short"].is_file(),
        "speakers": display_path(paths["speakers"]),
    }
    print(json.dumps(report, indent=2))
    return 0


def _cmd_frame(args: argparse.Namespace) -> int:
    source = resolve_source(args.video)
    paths = video_paths(source)
    name = slug_text(args.name)
    if not name:
        print("--name is empty. Pass something like hero.", file=sys.stderr)
        return 1
    seconds = parse_timecode(args.at)
    out = paths["article_dir"] / f"{name}.jpg"
    extract_frame(source, seconds, out)
    print(display_path(out))
    return 0


def _cmd_transcribe(args: argparse.Namespace) -> int:
    if args.num_speakers is not None and not 1 <= args.num_speakers <= 32:
        print("--num-speakers must be from 1 to 32.", file=sys.stderr)
        return 1
    source = resolve_source(args.video)
    markdown = transcribe(
        source,
        language=args.language,
        num_speakers=args.num_speakers,
        force=args.force,
    )
    print(display_path(markdown))
    return 0


def _cmd_transcript_render(args: argparse.Namespace) -> int:
    markdown = render_transcript(resolve_source(args.video))
    print(display_path(markdown))
    return 0


def _cmd_brief_check(args: argparse.Namespace) -> int:
    path = _resolve_brief(args.target, args.name, must_exist=True)
    data, duration, words = _brief_inputs(path)
    report = check_brief(data, duration=duration, words=words)
    for warning in report.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    if report.errors:
        print("\n".join(report.errors), file=sys.stderr)
        return 1
    print("ok")
    return 0


def _cmd_brief_render(args: argparse.Namespace) -> int:
    path = _resolve_brief(args.target, args.name, must_exist=True)
    data, duration, words = _brief_inputs(path)
    report = check_brief(data, duration=duration, words=words)
    for warning in report.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    if report.errors:
        print("\n".join(report.errors), file=sys.stderr)
        return 1
    markdown_path = path.with_suffix(".md")
    markdown_path.write_text(render_brief(data), encoding="utf-8")
    print(display_path(markdown_path))
    return 0


def _cmd_view(args: argparse.Namespace) -> int:
    run_viewer(args.video, name=args.name, port=args.port, open_browser=not args.no_open)
    return 0


def _cmd_cut(args: argparse.Namespace) -> int:
    source = resolve_source(args.video)
    brief_path = Path(args.brief) if args.brief else default_brief_path(source, args.name)
    if args.brief:
        brief_path = resolve_existing(args.brief)
    if not brief_path.is_file():
        print(
            f"No brief at {display_path(brief_path)}. Write the brief, run brief check, then cut again.",
            file=sys.stderr,
        )
        return 1
    paths = video_paths(source, args.name)
    words = load_words(paths["transcript_json"])
    if not words:
        print("No transcript words found. Times are used as written, with pad.", file=sys.stderr)
    plan = cut_video(
        source,
        brief_path,
        paths["short"],
        words=words,
        pad=args.pad,
        transition=args.transition,
        transition_duration=args.transition_duration,
        preset=args.preset,
        crf=args.crf,
        dry_run=args.dry_run,
        package=not args.no_package,
    )
    if args.dry_run:
        print(json.dumps(plan, indent=2))
        return 0
    print(display_path(paths["short"]))
    return 0


def _resolve_brief(target: str, name: str, *, must_exist: bool) -> Path:
    if target.endswith(".json"):
        return resolve_existing(target)
    source = resolve_source(target)
    path = video_paths(source, name)["brief_json"]
    if must_exist and not path.is_file():
        raise SystemExit(f"No brief at {display_path(path)}. Write it, then run this command again.")
    return path


def _brief_inputs(path: Path) -> tuple[dict, float | None, list]:
    data = load_brief(path)
    duration = None
    words: list = []
    if path.parent.name == "brief":
        video_dir = path.parent.parent
        source_json = video_dir / "source.json"
        if source_json.is_file():
            meta = json.loads(source_json.read_text(encoding="utf-8"))
            if meta.get("duration"):
                duration = float(meta["duration"])
        words = load_words(video_dir / "transcript" / "transcript.json")
    return data, duration, words


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


if __name__ == "__main__":
    raise SystemExit(main())
