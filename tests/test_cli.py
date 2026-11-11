import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from shortgen.cli import main
from shortgen.paths import find_root
from shortgen.transcribe import render_transcript


class CliTests(unittest.TestCase):
    def test_path_reports_a_video_before_any_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "videos" / "My Session.mp4"
            video.parent.mkdir()
            video.write_bytes(b"not a video")
            with patch.dict(os.environ, {"SHORTGEN_ROOT": str(root)}):
                stdout = io.StringIO()
                with redirect_stdout(stdout):
                    code = main(["path", str(video)])
            report = json.loads(stdout.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(report["slug"], "my-session")
            self.assertEqual(report["source"], "videos/My Session.mp4")
            self.assertFalse(report["transcript_exists"])
            self.assertFalse(report["brief_exists"])
            self.assertFalse(report["short_exists"])

    def test_brief_check_accepts_the_example(self):
        example = find_root() / "examples" / "brief.example.json"
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(["brief", "check", str(example)])
        self.assertEqual(code, 0)
        self.assertEqual(stdout.getvalue().strip(), "ok")
        self.assertIn("source order", stderr.getvalue())

    def test_missing_brief_stops_before_a_cut(self):
        with tempfile.NamedTemporaryFile(suffix=".mp4") as handle:
            with self.assertRaises(SystemExit) as caught:
                main(["brief", "check", handle.name])
        self.assertIn("No brief", str(caught.exception))

    def test_transcript_render_uses_speaker_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "videos" / "talk.mp4"
            video.parent.mkdir()
            video.write_bytes(b"video")
            transcript_dir = root / "generated" / "talk" / "transcript"
            transcript_dir.mkdir(parents=True)
            document = {
                "source": "videos/talk.mp4",
                "language_code": "en",
                "language_probability": 0.99,
                "audio_duration_secs": 2,
                "text": "Hello there.",
                "words": [
                    {"text": "Hello", "start": 0.1, "end": 0.5, "type": "word", "speaker_id": "speaker_0"},
                    {"text": " ", "start": 0.5, "end": 0.52, "type": "spacing"},
                    {"text": "there.", "start": 0.52, "end": 1.0, "type": "word", "speaker_id": "speaker_0"},
                ],
                "segments": [],
            }
            (transcript_dir / "transcript.json").write_text(json.dumps(document), encoding="utf-8")
            (root / "generated" / "talk" / "speakers.json").write_text(
                json.dumps({"speaker_0": "Ada"}),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"SHORTGEN_ROOT": str(root)}):
                markdown = render_transcript(video)
            text = Path(markdown).read_text(encoding="utf-8")
            self.assertIn("Ada", text)
            self.assertIn("Hello there.", text)


class FrameTests(unittest.TestCase):
    def test_frame_writes_a_jpg_in_the_article_folder(self):
        import shutil
        import subprocess
        if shutil.which("ffmpeg") is None:
            self.skipTest("ffmpeg not installed")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "videos" / "s.mp4"
            video.parent.mkdir()
            subprocess.run(
                ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=320x180:d=2:r=25",
                 "-pix_fmt", "yuv420p", str(video)],
                check=True,
            )
            with patch.dict(os.environ, {"SHORTGEN_ROOT": str(root)}):
                stdout = io.StringIO()
                with redirect_stdout(stdout):
                    code = main(["frame", str(video), "--at", "1", "--name", "Hero"])
            self.assertEqual(code, 0)
            self.assertEqual(stdout.getvalue().strip(), "generated/s/article/hero.jpg")
            self.assertTrue((root / "generated/s/article/hero.jpg").is_file())
