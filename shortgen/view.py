"""A local player for trimming a brief and previewing the short."""

from __future__ import annotations

import json
import sys
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from shortgen.brief import check_brief, load_brief, moment_bounds, render_brief
from shortgen.captions import cue_rows
from shortgen.cut import cut_video
from shortgen.paths import display_path, find_root, named_paths, resolve_source, slugify, video_paths
from shortgen.segments import load_words
from shortgen.transitions import TRANSITION_NAMES

PAGE = Path(__file__).with_name("static") / "viewer.html"
HOST = "127.0.0.1"


class Viewer:
    def __init__(self, source: Path | None, name: str = "main", *, paths: dict | None = None, source_label: str = "") -> None:
        self.name = name
        self.paths = paths if paths is not None else video_paths(source, name)
        self.source = source if source is not None and source.is_file() else None
        self.source_label = source_label or (display_path(source) if source is not None else "")
        self.brief_path = self.paths["brief_json"]
        if not self.brief_path.is_file():
            raise SystemExit(
                f"No brief at {display_path(self.brief_path)}. Write the brief, then open the viewer."
            )
        self.words = load_words(self.paths["transcript_json"])
        self.duration = _duration(self.source, self.paths["source_json"])
        self.render_lock = threading.Lock()
        self.render_state = {"status": "idle", "error": None}

    def project(self) -> dict:
        brief = load_brief(self.brief_path)
        moments = _moment_rows(brief)
        edit = _load_json(self.paths["edit"])
        return {
            "title": brief.get("title") or self.name,
            "name": self.paths["name"],
            "duration": self.duration,
            "brief": brief,
            "words": _public_words(self.words),
            "cues": cue_rows(moments, self.words, 0.0),
            "transitions": list(TRANSITION_NAMES),
            "short": self.paths["short"].is_file(),
            "subtitles": brief.get("subtitles") is not False,
            "source_missing": "" if self.source is not None else self.source_label,
            "pieces": assembly_pieces(
                (edit or {}).get("clips") or moments,
                _fade(edit, brief),
                (edit or {}).get("package") if isinstance(edit, dict) else None,
            ),
        }

    def cues(self, moments: list) -> list[dict]:
        rows = []
        for moment in moments:
            if not isinstance(moment, dict):
                raise ValueError("Each moment must be an object.")
            rows.append(
                {
                    "id": str(moment.get("id") or ""),
                    "start": float(moment["start"]),
                    "end": float(moment["end"]),
                }
            )
        return cue_rows(rows, self.words, 0.0)

    def save(self, brief: dict) -> dict:
        report = check_brief(brief, duration=self.duration, words=self.words)
        if report.errors:
            return {"ok": False, "errors": report.errors, "warnings": report.warnings}
        text = json.dumps(brief, indent=2, ensure_ascii=False) + "\n"
        _write(self.brief_path, text)
        _write(self.brief_path.with_suffix(".md"), render_brief(brief))
        return {"ok": True, "errors": [], "warnings": report.warnings, "brief": display_path(self.brief_path)}

    def start_render(self) -> dict:
        with self.render_lock:
            if self.source is None:
                self.render_state = {
                    "status": "error",
                    "error": f"Session file {self.source_label} is missing.",
                }
                return dict(self.render_state)
            if self.render_state["status"] == "running":
                return dict(self.render_state)

            def work() -> None:
                try:
                    words = load_words(self.paths["transcript_json"])
                    cut_video(
                        self.source,
                        self.brief_path,
                        self.paths["short"],
                        words=words,
                        package=True,
                    )
                except (SystemExit, OSError, ValueError) as exc:
                    with self.render_lock:
                        self.render_state = {"status": "error", "error": str(exc)}
                    return
                with self.render_lock:
                    self.render_state = {"status": "done", "error": None}

            self.render_state = {"status": "running", "error": None}
            threading.Thread(target=work, daemon=True).start()
            return dict(self.render_state)

    def render_status(self) -> dict:
        with self.render_lock:
            state = dict(self.render_state)
        state["short"] = self.paths["short"].is_file()
        return state


def make_server(viewer: Viewer, port: int = 0) -> ThreadingHTTPServer:
    handler = _handler(viewer)
    last: OSError | None = None
    start = port or 8733
    span = 1 if port == 0 else 20
    # port 0 asks the OS for a free port. Any other port may already be taken.
    if port == 0:
        return ThreadingHTTPServer((HOST, 0), handler)
    for candidate in range(start, start + span):
        try:
            return ThreadingHTTPServer((HOST, candidate), handler)
        except OSError as exc:
            last = exc
    raise SystemExit(f"Could not listen near port {start}. {last}")


def assembly_pieces(clips: list, fade: float, package: dict | None = None) -> list[dict]:
    """Where each merged file sits on the rendered short.

    Opening, then each moment, then the watch card, then the ending.
    Times are seconds from the start of the short.
    """
    durations = [max(0.0, float(clip["end"]) - float(clip["start"])) for clip in clips]
    info = package or {}
    body = float(info.get("body") or 0)
    if body <= 0:
        body = _joined_duration(durations, fade)
    preroll = float(info.get("opening_preroll") or 0)
    watch_post = float(info.get("watch_post") or 0)
    ending = float(info.get("ending") or 0)
    pieces: list[dict] = []
    if preroll >= 0.04:
        pieces.append(_piece("opening", 0.0, preroll, "opening"))
    starts = [_body_start(index, durations, fade) for index in range(len(durations))]
    for index, clip in enumerate(clips):
        start = preroll + starts[index]
        end = preroll + (starts[index + 1] if index + 1 < len(starts) else body)
        if end - start < 0.04:
            continue
        pieces.append(_piece(str(clip.get("id") or index), start, end, "moment"))
    cursor = preroll + body
    if watch_post >= 0.04:
        pieces.append(_piece("watch", cursor, cursor + watch_post, "watch"))
        cursor += watch_post
    if ending >= 0.04:
        pieces.append(_piece("ending", cursor, cursor + ending, "ending"))
    return pieces


def _body_start(index: int, durations: list[float], fade: float) -> float:
    if index <= 0:
        return 0.0
    if fade <= 0:
        return sum(durations[:index])
    # Same overlap the cutter uses when it joins the moments.
    return max(0.0, sum(durations[:index]) - fade * index - 0.02)


def _joined_duration(durations: list[float], fade: float) -> float:
    total = sum(durations)
    if fade > 0 and len(durations) > 1:
        total -= fade * (len(durations) - 1)
    return max(0.0, total)


def _piece(name: str, start: float, end: float, kind: str) -> dict:
    return {"id": name, "start": round(start, 3), "end": round(end, 3), "kind": kind}


def _fade(edit: dict | None, brief: dict) -> float:
    raw = (edit or {}).get("transition") or brief.get("transition") or {}
    if isinstance(raw, str):
        raw = {"type": raw}
    if not isinstance(raw, dict) or str(raw.get("type") or "fade") == "cut":
        return 0.0
    try:
        return max(0.0, float(raw.get("duration") if raw.get("duration") is not None else 0.25))
    except (TypeError, ValueError):
        return 0.25


def _load_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def viewer_for(target: str, name: str = "main") -> Viewer:
    """Open the project for a session file, a rendered short, or a generated folder."""
    root = find_root()
    candidate = _candidate(target, root)
    located = _generated_name(candidate, root)
    if located is None and not candidate.is_file():
        slug = slugify(Path(target).name)
        folder = root / "generated" / slug
        if (folder / "source.json").is_file():
            located = (slug, None)
    if located is not None:
        slug, inferred = located
        paths = named_paths(slug, inferred or name, root=root)
        source, label = _recorded_source(paths["source_json"], root)
        if source is None and not paths["short"].is_file() and not paths["brief_json"].is_file():
            raise FileNotFoundError(
                f"Video not found: {target}. Put the file in videos/ or pass a path to an existing file."
            )
        return Viewer(source, paths["name"], paths=paths, source_label=label)
    source = resolve_source(target, root)
    return Viewer(source, name)


def run_viewer(target: str, name: str = "main", port: int = 8733, open_browser: bool = True) -> None:
    viewer = viewer_for(target, name)
    if viewer.source is None:
        print(
            f"Session file {viewer.source_label} is missing. Opening the rendered short.",
            file=sys.stderr,
        )
    server = make_server(viewer, port)
    url = _url(server)
    print(url, flush=True)
    print("Save writes the brief. Render cuts the short. Ctrl-C closes the player.", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nClosed.", flush=True)
    finally:
        server.server_close()


def _handler(viewer: Viewer) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: object) -> None:
            return

        def do_HEAD(self) -> None:
            self.do_GET()

        def do_GET(self) -> None:
            path = urllib.parse.urlsplit(self.path).path
            if path == "/":
                self._bytes(200, "text/html; charset=utf-8", PAGE.read_bytes(), cache=False)
                return
            if path == "/api/project":
                self._json(viewer.project())
                return
            if path == "/api/render":
                self._json(viewer.render_status())
                return
            if path == "/media/source":
                if viewer.source is None:
                    self._json({"error": f"Session file {viewer.source_label} is missing."}, status=404)
                    return
                self._file(viewer.source)
                return
            if path == "/media/short":
                short = viewer.paths["short"]
                if not short.is_file():
                    self._json({"error": "No render yet."}, status=404)
                    return
                self._file(short)
                return
            if path == "/favicon.ico":
                self._bytes(204, "text/plain", b"")
                return
            self._json({"error": "Not found."}, status=404)

        def do_POST(self) -> None:
            path = urllib.parse.urlsplit(self.path).path
            try:
                payload = self._read_json()
            except (json.JSONDecodeError, UnicodeError, ValueError):
                self._json({"error": "Expected a JSON object."}, status=400)
                return
            try:
                if path == "/api/brief":
                    result = viewer.save(payload)
                    self._json(result, status=200 if result["ok"] else 400)
                    return
                if path == "/api/cues":
                    moments = payload.get("moments")
                    if not isinstance(moments, list):
                        raise ValueError("moments must be a list.")
                    self._json({"cues": viewer.cues(moments)})
                    return
                if path == "/api/render":
                    self._json(viewer.start_render())
                    return
            except (KeyError, TypeError, ValueError) as exc:
                self._json({"error": str(exc)}, status=400)
                return
            self._json({"error": "Not found."}, status=404)

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            data = json.loads(raw.decode("utf-8") or "{}")
            if not isinstance(data, dict):
                raise ValueError("Expected a JSON object.")
            return data

        def _json(self, data: dict, status: int = 200) -> None:
            body = json.dumps(data).encode("utf-8")
            self._bytes(status, "application/json", body, cache=False)

        def _bytes(self, status: int, content_type: str, body: bytes, cache: bool = True) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            if not cache:
                self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _file(self, path: Path) -> None:
            size = path.stat().st_size
            start, end = 0, size - 1
            status = 200
            header = self.headers.get("Range")
            if header and header.startswith("bytes="):
                spec = header.removeprefix("bytes=").split(",", 1)[0].strip()
                left, _, right = spec.partition("-")
                try:
                    if left == "":
                        start = max(0, size - int(right or "0"))
                    else:
                        start = int(left)
                        end = int(right) if right else size - 1
                except ValueError:
                    start, end = 0, size - 1
                end = min(end, size - 1)
                if start < 0 or start > end or start >= size:
                    self.send_error(416)
                    return
                status = 206
            length = end - start + 1
            try:
                self.send_response(status)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(length))
                if status == 206:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.end_headers()
                if self.command == "HEAD":
                    return
                with path.open("rb") as handle:
                    handle.seek(start)
                    remaining = length
                    while remaining:
                        chunk = handle.read(min(1024 * 1024, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                return

    return Handler


def _moment_rows(brief: dict) -> list[dict]:
    rows = []
    for moment in brief.get("moments") or []:
        start, end = moment_bounds(moment)
        rows.append({"id": str(moment.get("id") or ""), "start": start, "end": end})
    return rows


def _public_words(words: list[dict]) -> list[dict]:
    spoken = []
    for word in words:
        if word.get("type") == "spacing" or word.get("start") is None or word.get("end") is None:
            continue
        text = str(word.get("text") or "").strip()
        if not text:
            continue
        spoken.append({"text": text, "start": float(word["start"]), "end": float(word["end"])})
    return spoken


def _candidate(value: str, root: Path) -> Path:
    raw = Path(value).expanduser()
    if raw.is_absolute():
        return raw
    for folder in (Path.cwd(), root):
        candidate = folder / raw
        if candidate.exists():
            return candidate
    return root / raw


def _generated_name(path: Path, root: Path) -> tuple[str, str | None] | None:
    generated = root / "generated"
    try:
        relative = path.resolve().relative_to(generated.resolve())
    except (ValueError, OSError):
        return None
    parts = relative.parts
    if not parts:
        return None
    slug = parts[0]
    if not (generated / slug).is_dir():
        return None
    inferred = None
    if len(parts) >= 3 and parts[1] == "shorts":
        inferred = parts[2]
    elif len(parts) >= 3 and parts[1] == "brief":
        inferred = Path(parts[2]).stem
    return slug, inferred


def _recorded_source(source_json: Path, root: Path) -> tuple[Path | None, str]:
    label = ""
    if source_json.is_file():
        try:
            meta = json.loads(source_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            meta = {}
        label = str(meta.get("source") or "")
    if not label:
        return None, ""
    raw = Path(label).expanduser()
    candidate = raw if raw.is_absolute() else root / raw
    if candidate.is_file():
        return candidate.resolve(), label
    return None, label


def _duration(source: Path | None, source_json: Path) -> float:
    if source_json.is_file():
        try:
            meta = json.loads(source_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            meta = {}
        if meta.get("duration"):
            return float(meta["duration"])
    if source is not None and source.is_file():
        from shortgen.media import probe

        return float(probe(source)["duration"])
    return 0.0


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _url(server: ThreadingHTTPServer) -> str:
    host, port = server.server_address[:2]
    return f"http://{host}:{port}"
