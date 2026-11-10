"""Frame a finished short as 1080x1920 and attach the Trust Valley cards."""

from __future__ import annotations

import math
import shutil
import subprocess
import sys
from pathlib import Path

from shortgen.captions import Cue, caption_top, write_caption
from shortgen.media import probe, require_ffmpeg, run
from shortgen.paths import find_root
from shortgen.timeutil import round_s

FRAME_W = 1080
FRAME_H = 1920
FPS = 30
KEY_COLOR = "0xBCFF6B"
KEY_SIMILARITY = "0.06"
KEY_BLEND = "0.04"
SOLID_GREEN = 0.90
MOTION = 0.045
# How long the watch-card wipe may sit on the end of the talk.
WIPE_LIMIT = 0.9

ASSET_NAMES = {
    "opening": "Opening Transition short Martin Schaffer.mp4",
    "logos": "Logos TVD Replay - short.png",
    "watch": "Watch full session, innovation race v4.mp4",
    "ending": "Ending - short.mp4",
}


def find_package_assets(root: Path | None = None) -> dict[str, Path] | None:
    folder = (root or find_root()) / "assets"
    found: dict[str, Path] = {}
    for key, name in ASSET_NAMES.items():
        path = folder / name
        if not path.is_file():
            return None
        found[key] = path
    return found


def opening_cut(samples: list[tuple[float, float, float]], duration: float) -> tuple[float, float]:
    """Return (preroll_end, keyed_end) for a card that ends in a green hold.

    [0, preroll_end) plays before the talk. [preroll_end, keyed_end) is keyed
    over the start of the talk. The solid green after that is dropped.
    """
    if not samples or samples[-1][1] < SOLID_GREEN:
        return round_s(duration), round_s(duration)
    index = len(samples) - 1
    while index > 0 and samples[index - 1][1] >= SOLID_GREEN:
        index -= 1
    keyed_end = samples[index][0]
    cursor = index
    while cursor > 0 and (samples[cursor][2] >= MOTION or samples[cursor][1] >= 0.05):
        cursor -= 1
    if samples[cursor][2] >= MOTION or samples[cursor][1] >= 0.05:
        preroll = 0.0
    else:
        preroll = samples[cursor][0]
    if keyed_end < preroll:
        keyed_end = preroll
    return round_s(preroll), round_s(keyed_end)


def watch_cut(samples: list[tuple[float, float, float]], duration: float) -> tuple[float, float]:
    """Return (overlap_start, post_start) for a card that begins on green.

    [0, overlap_start) is the solid green hold and is dropped.
    [overlap_start, post_start) is keyed over the end of the talk.
    [post_start, duration) plays after the talk.
    """
    if not samples or samples[0][1] < SOLID_GREEN:
        return 0.0, 0.0
    index = 0
    while index < len(samples) and samples[index][1] >= SOLID_GREEN:
        index += 1
    if index == 0:
        return 0.0, 0.0
    overlap_start = samples[index - 1][0]
    green_gone: float | None = None
    cursor = index
    while cursor < len(samples):
        time, green, delta = samples[cursor]
        if green < 0.05 and green_gone is None:
            green_gone = time
        if green_gone is not None and time - green_gone >= WIPE_LIMIT:
            break
        if green_gone is not None and delta < MOTION and time - green_gone >= 0.3:
            break
        cursor += 1
    if cursor >= len(samples):
        post_start = duration
    else:
        post_start = samples[cursor][0]
    if post_start < overlap_start:
        post_start = overlap_start
    return round_s(overlap_start), round_s(post_start)


def clear_window(alpha: bytes, width: int, height: int) -> tuple[int, int, int, int]:
    """The transparent rectangle of a logo frame, as x, y, width, height."""
    if width <= 0 or height <= 0 or len(alpha) < width * height:
        return 0, 0, _even(width), _even(height)
    step = max(1, width // 32)
    rows: list[int] = []
    for y in range(height):
        clear = 0
        seen = 0
        for x in range(0, width, step):
            seen += 1
            if alpha[y * width + x] < 16:
                clear += 1
        if seen and clear / seen >= 0.9:
            rows.append(y)
    if not rows:
        return 0, 0, _even(width), _even(height)
    start = rows[0]
    best = (start, start)
    run = start
    for previous, y in zip(rows, rows[1:]):
        if y == previous + 1:
            continue
        if previous - run > best[1] - best[0]:
            best = (run, previous)
        run = y
    if rows[-1] - run > best[1] - best[0]:
        best = (run, rows[-1])
    top = best[0] + (best[0] % 2)
    bottom = best[1] - ((best[1] - top + 1) % 2)
    if bottom <= top:
        return 0, 0, _even(width), _even(height)
    return 0, top, _even(width), bottom - top + 1


def cover_crop(src_w: int, src_h: int, dst_w: int, dst_h: int) -> tuple[int, int]:
    """Source crop size that fills dst_w by dst_h."""
    if src_w <= 0 or src_h <= 0 or dst_w <= 0 or dst_h <= 0:
        return _even(max(src_w, 2)), _even(max(src_h, 2))
    if src_w * dst_h >= src_h * dst_w:
        crop_h = src_h - (src_h % 2)
        crop_w = int(round(crop_h * dst_w / dst_h))
    else:
        crop_w = src_w - (src_w % 2)
        crop_h = int(round(crop_w * dst_h / dst_w))
    crop_w = min(_even_up(crop_w), src_w - (src_w % 2))
    crop_h = min(_even_up(crop_h), src_h - (src_h % 2))
    return max(2, crop_w), max(2, crop_h)


def follow_crop(
    centers: list[tuple[float, float]],
    duration: float,
    src_w: int,
    crop_w: int,
    fps: int = FPS,
) -> list[int]:
    """Crop x for each frame, smoothed so the frame follows the speaker."""
    frames = max(1, int(round(duration * fps)))
    limit = max(0, src_w - crop_w)
    if not centers or crop_w >= src_w:
        return [min(limit, _even(max(0, (src_w - crop_w) // 2)))] * frames
    smoothed = _smooth_track(centers, tau=0.4)
    xs: list[int] = []
    index = 0
    for frame in range(frames):
        time = frame / fps
        while index + 1 < len(smoothed) and smoothed[index + 1][0] < time:
            index += 1
        if index + 1 >= len(smoothed):
            center = smoothed[-1][1]
        else:
            t0, x0 = smoothed[index]
            t1, x1 = smoothed[index + 1]
            span = t1 - t0
            mix = 0.0 if span <= 0 else min(1.0, max(0.0, (time - t0) / span))
            center = x0 + (x1 - x0) * mix
        left = int(round(center - crop_w / 2))
        left = min(limit, max(0, left))
        xs.append(left - (left % 2))
    return xs


def package_video(
    body: Path,
    output: Path,
    assets: dict[str, Path],
    *,
    preset: str = "medium",
    crf: int = 18,
    cues: list[Cue] | None = None,
) -> dict:
    require_ffmpeg()
    body_info = probe(body)
    opening_info = probe(assets["opening"])
    watch_info = probe(assets["watch"])
    ending_info = probe(assets["ending"])
    logo_info = probe(assets["logos"])
    window = _logo_window(assets["logos"], logo_info["width"], logo_info["height"])
    opening_samples = sample_frames(assets["opening"])
    watch_samples = sample_frames(assets["watch"])
    preroll, keyed_end = opening_cut(opening_samples, opening_info["duration"])
    overlap_start, post_start = watch_cut(watch_samples, watch_info["duration"])
    open_key = max(0.0, keyed_end - preroll)
    watch_key = max(0.0, post_start - overlap_start)
    if open_key + watch_key >= body_info["duration"] - 0.2:
        watch_key = 0.0
        post_start = overlap_start
    report = {
        "width": FRAME_W,
        "height": FRAME_H,
        "window": {"x": window[0], "y": window[1], "width": window[2], "height": window[3]},
        "opening_preroll": preroll,
        "opening_key": round_s(open_key),
        "watch_key": round_s(watch_key),
        "watch_post": round_s(max(0.0, watch_info["duration"] - post_start)),
        "ending": round_s(ending_info["duration"]),
        "body": round_s(body_info["duration"]),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    rendering = output.with_name(f"{output.stem}.packaging.mp4")
    try:
        _render(
            body,
            assets,
            rendering,
            body_info=body_info,
            opening_info=opening_info,
            watch_info=watch_info,
            ending_info=ending_info,
            window=window,
            preroll=preroll,
            open_key=open_key,
            overlap_start=overlap_start,
            watch_key=watch_key,
            post_start=post_start,
            preset=preset,
            crf=crf,
            cues=cues,
        )
        rendering.replace(output)
    finally:
        if rendering.exists():
            rendering.unlink()
    finished = probe(output)
    report["duration"] = round_s(finished["duration"])
    return report


def sample_frames(path: Path, fps: float = 10.0) -> list[tuple[float, float, float]]:
    """(time, green ratio, motion) sampled from a bumper. Motion is 0 on the first frame."""
    require_ffmpeg()
    width, height = 24, 24
    completed = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-vf",
            f"fps={fps},scale={width}:{height}:flags=neighbor,format=rgb24",
            "-f",
            "rawvideo",
            "-",
        ],
        capture_output=True,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or b"").decode("utf-8", "replace").strip()[-2000:]
        raise SystemExit(f"Could not read frames from {path.name}.\n{detail}")
    frame_bytes = width * height * 3
    raw = completed.stdout
    count = len(raw) // frame_bytes
    samples: list[tuple[float, float, float]] = []
    previous: bytes | None = None
    for index in range(count):
        frame = raw[index * frame_bytes : (index + 1) * frame_bytes]
        green = 0
        motion = 0
        pixels = width * height
        for offset in range(0, frame_bytes, 3):
            red, green_ch, blue = frame[offset], frame[offset + 1], frame[offset + 2]
            if green_ch > 160 and green_ch > red + 30 and green_ch > blue + 30:
                green += 1
            if previous is not None:
                motion += (
                    abs(red - previous[offset])
                    + abs(green_ch - previous[offset + 1])
                    + abs(blue - previous[offset + 2])
                )
        ratio = green / pixels
        delta = 0.0 if previous is None else motion / (pixels * 255 * 3)
        samples.append((round_s(index / fps), ratio, delta))
        previous = frame
    return samples


def _logo_window(path: Path, width: int, height: int) -> tuple[int, int, int, int]:
    completed = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-vf",
            "format=rgba,alphaextract,format=gray",
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-",
        ],
        capture_output=True,
    )
    if completed.returncode != 0 or len(completed.stdout) < width * height:
        return 0, 0, _even(FRAME_W), _even(FRAME_H)
    window = clear_window(completed.stdout, width, height)
    scale_x = FRAME_W / width if width else 1
    scale_y = FRAME_H / height if height else 1
    x, y, w, h = window
    left = _even(int(round(x * scale_x)))
    top = int(round(y * scale_y))
    top = top - (top % 2)
    out_w = _even(int(round(w * scale_x))) or FRAME_W
    out_h = _even(int(round(h * scale_y))) or FRAME_H
    if left + out_w > FRAME_W:
        out_w = FRAME_W - left
    if top + out_h > FRAME_H:
        out_h = FRAME_H - top
    return left, top, _even(out_w), _even(out_h)


def _render(
    body: Path,
    assets: dict[str, Path],
    output: Path,
    *,
    body_info: dict,
    opening_info: dict,
    watch_info: dict,
    ending_info: dict,
    window: tuple[int, int, int, int],
    preroll: float,
    open_key: float,
    overlap_start: float,
    watch_key: float,
    post_start: float,
    preset: str,
    crf: int,
    cues: list[Cue] | None = None,
) -> None:
    body_dur = body_info["duration"]
    watch_dur = watch_info["duration"]
    end_dur = ending_info["duration"]
    post_dur = max(0.0, watch_dur - post_start)
    _x, top, width, height = window
    parts: list[str] = []
    video_labels: list[str] = []
    audio_labels: list[str] = []

    crop_w, crop_h = cover_crop(body_info["width"], body_info["height"], width, height)
    centers = _speaker_centers(body, body_info["width"])
    crop_xs = follow_crop(centers, body_dur, body_info["width"], crop_w, FPS)
    crop_script = output.with_name("crop.cmd")
    _write_crop_script(crop_script, crop_xs, FPS)
    parts.append(
        f"[0:v]sendcmd=f={crop_script},"
        f"crop={crop_w}:{crop_h}:{crop_xs[0]}:0,"
        f"scale={width}:{height}:flags=bicubic,"
        f"pad={FRAME_W}:{FRAME_H}:(ow-iw)/2:{top}:black,"
        f"fps={FPS},setpts=PTS-STARTPTS,format=yuv420p,setsar=1[fitted]"
    )
    parts.append(f"[1:v]scale={FRAME_W}:{FRAME_H},format=rgba[logo]")
    parts.append(
        f"[fitted][logo]overlay=0:0:format=auto,fps={FPS},setpts=PTS-STARTPTS,format=yuv420p,setsar=1[talk]"
    )
    talk_label = "talk"

    if open_key >= 0.04 and preroll >= 0.04:
        parts.append(f"[2:v]fps={FPS},setpts=PTS-STARTPTS,split=2[osrc][oksrc]")
    elif open_key >= 0.04:
        parts.append(f"[2:v]fps={FPS},setpts=PTS-STARTPTS[oksrc]")
    elif preroll >= 0.04:
        parts.append(f"[2:v]fps={FPS},setpts=PTS-STARTPTS[osrc]")
    if open_key >= 0.04:
        key_end = preroll + open_key
        parts.append(
            f"[oksrc]trim=start={preroll:.3f}:end={key_end:.3f},setpts=PTS-STARTPTS,"
            f"scale={FRAME_W}:{FRAME_H},format=rgba,"
            f"chromakey={KEY_COLOR}:{KEY_SIMILARITY}:{KEY_BLEND},format=rgba[owipe]"
        )
        parts.append(f"[{talk_label}][owipe]overlay=0:0:eof_action=pass:repeatlast=0:format=auto[talko]")
        talk_label = "talko"

    if watch_key >= 0.04 and post_dur >= 0.04:
        parts.append(f"[3:v]fps={FPS},setpts=PTS-STARTPTS,split=2[wsrc][wksrc]")
    elif watch_key >= 0.04:
        parts.append(f"[3:v]fps={FPS},setpts=PTS-STARTPTS[wksrc]")
    elif post_dur >= 0.04:
        parts.append(f"[3:v]fps={FPS},setpts=PTS-STARTPTS[wsrc]")
    if watch_key >= 0.04:
        delay = max(0.0, body_dur - watch_key)
        parts.append(
            f"[wksrc]trim=start={overlap_start:.3f}:end={post_start:.3f},setpts=PTS-STARTPTS,"
            f"scale={FRAME_W}:{FRAME_H},format=rgba,"
            f"chromakey={KEY_COLOR}:{KEY_SIMILARITY}:{KEY_BLEND},"
            f"setpts=PTS+{delay:.3f}/TB,format=rgba[wwipe]"
        )
        parts.append(
            f"[{talk_label}][wwipe]overlay=0:0:eof_action=pass:repeatlast=0:format=auto,"
            f"format=yuv420p,setsar=1[talkv]"
        )
    else:
        parts.append(f"[{talk_label}]format=yuv420p,setsar=1[talkv]")

    if preroll >= 0.04:
        parts.append(
            f"[osrc]trim=start=0:end={preroll:.3f},setpts=PTS-STARTPTS,"
            f"scale={FRAME_W}:{FRAME_H},fps={FPS},format=yuv420p,setsar=1[pre]"
        )
    if post_dur >= 0.04:
        parts.append(
            f"[wsrc]trim=start={post_start:.3f}:end={watch_dur:.3f},setpts=PTS-STARTPTS,"
            f"scale={FRAME_W}:{FRAME_H},fps={FPS},format=yuv420p,setsar=1[wpost]"
        )

    video_labels = []
    audio_labels = []
    if preroll >= 0.04:
        video_labels.append("[pre]")
        audio_labels.append("[prea]")
    video_labels.append("[talkv]")
    audio_labels.append("[talka]")
    if post_dur >= 0.04:
        video_labels.append("[wpost]")
        audio_labels.append("[wposta]")

    parts.append(
        f"[4:v]scale={FRAME_W}:{FRAME_H}:force_original_aspect_ratio=increase,"
        f"crop={FRAME_W}:{FRAME_H},fps={FPS},setpts=PTS-STARTPTS,format=yuv420p,setsar=1[endv]"
    )
    video_labels.append("[endv]")
    audio_labels.append("[enda]")

    silence_parts = []
    if preroll >= 0.04:
        silence_parts.append(("prea", preroll))
    if post_dur >= 0.04:
        silence_parts.append(("wposta", post_dur))
    if not ending_info["has_audio"]:
        silence_parts.append(("enda", end_dur))
    if not body_info["has_audio"]:
        silence_parts.append(("talka", body_dur))
    if silence_parts:
        outputs = "".join(f"[sil{index}]" for index in range(len(silence_parts)))
        parts.append(f"[5:a]asplit={len(silence_parts)}{outputs}")
        for index, (label, length) in enumerate(silence_parts):
            parts.append(f"[sil{index}]atrim=0:{length:.3f},asetpts=PTS-STARTPTS[{label}]")

    if body_info["has_audio"]:
        parts.append(
            f"[0:a]aformat=sample_fmts=fltp:channel_layouts=stereo,aresample=48000,"
            f"asetpts=PTS-STARTPTS,atrim=0:{body_dur:.3f}[talka]"
        )
    if ending_info["has_audio"]:
        parts.append(
            f"[4:a]aformat=sample_fmts=fltp:channel_layouts=stereo,aresample=48000,"
            f"asetpts=PTS-STARTPTS,atrim=0:{end_dur:.3f}[enda]"
        )

    cards = _caption_cards(cues or [], output, top, height)
    if cards:
        label = "talkv"
        base_index = 5 + (1 if silence_parts else 0)
        for index, (png, start, duration, y) in enumerate(cards):
            out = "talkc" if index == len(cards) - 1 else f"capv{index}"
            parts.append(f"[{base_index + index}:v]setpts=PTS+{start:.3f}/TB,format=rgba[cap{index}]")
            parts.append(
                f"[{label}][cap{index}]overlay="
                f"x=(main_w-overlay_w)/2:y={y}:eof_action=pass:repeatlast=0:format=auto[{out}]"
            )
            label = out
        video_labels = ["[talkc]" if item == "[talkv]" else item for item in video_labels]
    streams = "".join(video + audio for video, audio in zip(video_labels, audio_labels))
    parts.append(f"{streams}concat=n={len(video_labels)}:v=1:a=1[v][a]")
    graph = ";".join(parts)
    silence_len = max((length for _label, length in silence_parts), default=1.0) + 1.0
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(body),
        "-i",
        str(assets["logos"]),
        "-i",
        str(assets["opening"]),
        "-i",
        str(assets["watch"]),
        "-i",
        str(assets["ending"]),
    ]
    if silence_parts:
        cmd += [
            "-f",
            "lavfi",
            "-t",
            f"{silence_len:.3f}",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=48000",
        ]
    for png, _start, duration, _y in cards:
        cmd += ["-loop", "1", "-framerate", str(FPS), "-t", f"{duration:.3f}", "-i", str(png)]
    cmd += [
        "-filter_complex",
        graph,
        "-map",
        "[v]",
        "-map",
        "[a]",
        "-c:v",
        "libx264",
        "-preset",
        preset,
        "-crf",
        str(crf),
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        str(output),
    ]
    _log(
        "Packaging 1080x1920: opening, logo over the moments, watch card, and ending."
        + (f" Burning {len(cards)} captions." if cards else "")
    )
    try:
        run(cmd)
    finally:
        crop_script.unlink(missing_ok=True)
        shutil.rmtree(output.with_name("captions"), ignore_errors=True)


def _caption_cards(
    cues: list[Cue],
    output: Path,
    window_top: int,
    window_height: int,
) -> list[tuple[Path, float, float, int]]:
    usable = [cue for cue in cues if cue.text.strip() and cue.end - cue.start >= 0.2]
    if not usable:
        return []
    folder = output.with_name("captions")
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    cards = []
    for index, cue in enumerate(usable):
        png = folder / f"{index:03d}.png"
        _width, text_h = write_caption(cue.text, png)
        y = caption_top(window_top, window_height, text_h)
        cards.append((png, cue.start, cue.end - cue.start, y))
    return cards


def _even(value: int) -> int:
    value = max(0, int(value))
    return value if value % 2 == 0 else value - 1


def _even_up(value: int) -> int:
    value = max(0, int(value))
    return value if value % 2 == 0 else value + 1


def _smooth_track(samples: list[tuple[float, float]], tau: float) -> list[tuple[float, float]]:
    forward = _exp_track(samples, tau, reverse=False)
    backward = _exp_track(samples, tau, reverse=True)
    return [(time, (left + right) / 2) for (time, left), (_t, right) in zip(forward, backward)]


def _exp_track(samples: list[tuple[float, float]], tau: float, *, reverse: bool) -> list[tuple[float, float]]:
    seq = list(reversed(samples)) if reverse else list(samples)
    state = seq[0][1]
    previous = seq[0][0]
    out: list[tuple[float, float]] = []
    for time, value in seq:
        gap = abs(time - previous)
        alpha = 1.0 if tau <= 0 else 1 - math.exp(-gap / tau)
        state = state + alpha * (value - state)
        out.append((time, state))
        previous = time
    if reverse:
        out.reverse()
    return out


def _speaker_centers(path: Path, src_w: int) -> list[tuple[float, float]]:
    """(time, horizontal center in source pixels) for the person on stage."""
    require_ffmpeg()
    width, height, fps = 480, 270, 4
    completed = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-vf",
            f"fps={fps},scale={width}:{height},format=rgb24",
            "-f",
            "rawvideo",
            "-",
        ],
        capture_output=True,
    )
    if completed.returncode != 0:
        return []
    frame_bytes = width * height * 3
    raw = completed.stdout
    count = len(raw) // frame_bytes
    centers: list[tuple[float, float]] = []
    held: float | None = None
    scale = src_w / width
    for index in range(count):
        frame = raw[index * frame_bytes : (index + 1) * frame_bytes]
        found = _person_center(frame, width, height)
        if found is None:
            if held is None:
                continue
            found = held
        held = found
        centers.append((index / fps, found * scale))
    return centers


def _person_center(frame: bytes, width: int, height: int) -> float | None:
    histogram = [0] * width
    y0, y1 = int(height * 0.16), int(height * 0.74)
    for y in range(y0, y1):
        row = y * width * 3
        for x in range(width):
            red = frame[row + x * 3]
            green = frame[row + x * 3 + 1]
            blue = frame[row + x * 3 + 2]
            if blue > red + 25 and blue > 70:
                continue
            if red > 115 and green > 95 and red + green + blue > 330:
                histogram[x] += 1
    peak = max(histogram)
    if peak < 4:
        return None
    peak_x = histogram.index(peak)
    threshold = max(2, int(peak * 0.35))
    left = peak_x
    while left > 0 and histogram[left - 1] >= threshold:
        left -= 1
    right = peak_x
    while right < width - 1 and histogram[right + 1] >= threshold:
        right += 1
    return (left + right) / 2


def _write_crop_script(path: Path, xs: list[int], fps: int) -> None:
    lines = []
    for index, x in enumerate(xs):
        start = index / fps
        end = (index + 1) / fps
        lines.append(f"{start:.3f}-{end:.3f} crop x {x};")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _log(message: str) -> None:
    print(message, file=sys.stderr)
