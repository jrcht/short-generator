import json
import shutil
import tempfile
import unittest
from pathlib import Path

from shortgen.cut import cut_video
from shortgen.media import probe, run


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg required")
class RenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.source = Path(cls.tmp.name) / "source.mp4"
        run(
            [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=0x224488:s=320x180:d=8:r=25",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=8",
                "-shortest",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-preset",
                "ultrafast",
                "-c:a",
                "aac",
                "-b:a",
                "96k",
                str(cls.source),
            ]
        )

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_dry_run_does_not_write_a_video(self):
        output = Path(self.tmp.name) / "dry.mp4"
        plan = self._cut(output, "fade", 0.3, dry_run=True)
        self.assertFalse(output.exists())
        self.assertAlmostEqual(plan["clips"][0]["start"], 0.38)
        self.assertAlmostEqual(plan["clips"][0]["end"], 2.62)

    def test_fade_join_duration(self):
        output = Path(self.tmp.name) / "fade.mp4"
        plan = self._cut(output, "fade", 0.3, dry_run=False)
        spans = [clip["end"] - clip["start"] for clip in plan["clips"]]
        expected = sum(spans) - 0.3
        self.assertTrue(output.exists())
        self.assertAlmostEqual(plan["output_duration"], expected, delta=0.35)
        info = probe(output)
        self.assertTrue(info["has_audio"])
        self.assertGreater(info["width"], 0)

    def test_hard_cut_keeps_both_spans(self):
        output = Path(self.tmp.name) / "cut.mp4"
        plan = self._cut(output, "cut", 0.0, dry_run=False)
        spans = [clip["end"] - clip["start"] for clip in plan["clips"]]
        self.assertAlmostEqual(plan["output_duration"], sum(spans), delta=0.35)

    def _cut(self, output: Path, transition: str, fade: float, dry_run: bool) -> dict:
        brief_path = Path(self.tmp.name) / f"{transition}.json"
        brief = {
            "title": "Tone",
            "hook": "Two tones joined.",
            "transition": {"type": transition, "duration": fade},
            "moments": [
                {
                    "id": "open",
                    "start": 0.5,
                    "end": 2.5,
                    "speaker": "speaker_0",
                    "quote": "hello there",
                    "why": "Open.",
                },
                {
                    "id": "close",
                    "start": 4.0,
                    "end": 6.5,
                    "speaker": "speaker_0",
                    "quote": "hello there",
                    "why": "Close.",
                },
            ],
        }
        brief_path.write_text(json.dumps(brief), encoding="utf-8")
        return cut_video(
            self.source,
            brief_path,
            output,
            pad=0.12,
            preset="ultrafast",
            crf=30,
            dry_run=dry_run,
        )
