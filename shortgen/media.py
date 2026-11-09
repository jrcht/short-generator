"""ffprobe and ffmpeg helpers."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


def require_ffmpeg() -> None:
    missing = [tool for tool in ("ffmpeg", "ffprobe") if shutil.which(tool) is None]
    if missing:
        raise SystemExit(f"{', '.join(missing)} must be on PATH.")


def run(cmd: list[str]) -> None:
    completed = subprocess.run(cmd, text=True, capture_output=True)
    if completed.returncode == 0:
        return
    detail = (completed.stderr or completed.stdout or "").strip()
    tail = detail[-4000:]
    raise SystemExit(f"Command failed ({completed.returncode}): {' '.join(cmd[:8])}\n{tail}")


def probe(path: Path) -> dict:
    require_ffmpeg()
    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or "").strip()[-2000:]
        raise SystemExit(f"ffprobe failed for {path}\n{detail}")
    data = json.loads(completed.stdout or "{}")
    videos = [stream for stream in data.get("streams", []) if stream.get("codec_type") == "video"]
    if not videos:
        raise SystemExit(f"No video stream in {path}")
    video = videos[0]
    audio = next((stream for stream in data.get("streams", []) if stream.get("codec_type") == "audio"), None)
    raw_duration = data.get("format", {}).get("duration") or video.get("duration") or 0
    fps = str(video.get("r_frame_rate") or video.get("avg_frame_rate") or "")
    if not _fps_ok(fps):
        fps = ""
    return {
        "duration": float(raw_duration),
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "fps": fps,
        "has_audio": audio is not None,
    }


def _fps_ok(fps: str) -> bool:
    if not fps or fps in {"0/0", "N/A"}:
        return False
    if "/" in fps:
        numerator, denominator = fps.split("/", 1)
        try:
            return float(denominator) != 0 and float(numerator) > 0
        except ValueError:
            return False
    try:
        return float(fps) > 0
    except ValueError:
        return False
