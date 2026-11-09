"""Paths for one source video and the shorts cut from it."""

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from pathlib import Path


def find_root() -> Path:
    env = os.environ.get("SHORTGEN_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    here = Path(__file__).resolve().parent.parent
    if (here / "skills").is_dir() or (here / "videos").is_dir():
        return here
    return Path.cwd().resolve()


def slug_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = normalized.encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")


def slugify(filename: str) -> str:
    return slug_text(Path(filename).stem) or "video"


def display_path(path: Path, root: Path | None = None) -> str:
    root = root or find_root()
    resolved = path.expanduser().resolve()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError:
        return resolved.as_posix()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def same_source(stored: str, source: Path, root: Path | None = None) -> bool:
    if not stored:
        return False
    root = root or find_root()
    raw = Path(stored)
    candidate = raw if raw.is_absolute() else root / stored
    try:
        return candidate.resolve() == source.resolve()
    except OSError:
        return False


def resolve_source(value: str, root: Path | None = None) -> Path:
    root = root or find_root()
    raw = Path(value).expanduser()
    candidates = [raw] if raw.is_absolute() else [Path.cwd() / raw, root / raw, root / "videos" / raw]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError(
        f"Video not found: {value}. Put the file in videos/ or pass a path to an existing file."
    )


def allocate_slug(source: Path, root: Path | None = None) -> str:
    root = root or find_root()
    base = slugify(source.name)
    if _slug_is_free(base, source, root):
        return base
    candidate = f"{base}-{file_sha256(source)[:8]}"
    if _slug_is_free(candidate, source, root):
        return candidate
    raise SystemExit(
        f"generated/{base} already belongs to a different video. Rename one of the files."
    )


def _slug_is_free(slug: str, source: Path, root: Path) -> bool:
    meta_path = root / "generated" / slug / "source.json"
    if not meta_path.is_file():
        return True
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    return same_source(str(meta.get("source", "")), source, root)


def video_paths(source: Path, name: str = "main", root: Path | None = None) -> dict[str, Path | str]:
    root = root or find_root()
    short_name = slug_text(name)
    if not short_name:
        raise SystemExit("Short name is empty. Pass something like --name payoff.")
    slug = allocate_slug(source, root)
    base = root / "generated" / slug
    return {
        "root": root,
        "slug": slug,
        "name": short_name,
        "source": source,
        "dir": base,
        "source_json": base / "source.json",
        "speakers": base / "speakers.json",
        "transcript_dir": base / "transcript",
        "transcript_json": base / "transcript" / "transcript.json",
        "transcript_md": base / "transcript" / "transcript.md",
        "transcript_srt": base / "transcript" / "transcript.srt",
        "brief_json": base / "brief" / f"{short_name}.json",
        "brief_md": base / "brief" / f"{short_name}.md",
        "short_dir": base / "shorts" / short_name,
        "short": base / "shorts" / short_name / "short.mp4",
        "edit": base / "shorts" / short_name / "edit.json",
        "work": base / "work",
    }


def resolve_existing(value: str, root: Path | None = None) -> Path:
    root = root or find_root()
    raw = Path(value).expanduser()
    candidates = [raw] if raw.is_absolute() else [Path.cwd() / raw, root / raw]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError(f"File not found: {value}")
