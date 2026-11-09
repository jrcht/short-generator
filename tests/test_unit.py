import json
import tempfile
import unittest
from pathlib import Path

from shortgen.brief import check_brief, quote_mismatch
from shortgen.cut import build_filter, build_plan, snap_range, transition_offsets
from shortgen.paths import allocate_slug, find_root, slug_text, slugify
from shortgen.segments import group_segments, render_markdown
from shortgen.timeutil import format_srt_time, format_timecode, parse_timecode
from shortgen.transcribe import _is_current


def word(text, start, end, speaker="speaker_0", kind="word"):
    return {
        "text": text,
        "start": start,
        "end": end,
        "type": kind,
        "speaker_id": speaker,
    }


class TimecodeTests(unittest.TestCase):
    def test_parses_clock_and_seconds(self):
        self.assertEqual(parse_timecode(90), 90)
        self.assertEqual(parse_timecode("1:30"), 90)
        self.assertEqual(parse_timecode("1:02:03.5"), 3723.5)
        self.assertEqual(parse_timecode("01:02:03,250"), 3723.25)

    def test_rejects_bad_values(self):
        for value in ("", "-1", "1:2:3:4", True, None):
            with self.assertRaises(ValueError):
                parse_timecode(value)

    def test_format_round_trip(self):
        self.assertEqual(format_timecode(3723.25), "01:02:03.250")
        self.assertEqual(format_srt_time(0), "00:00:00,000")


class SegmentTests(unittest.TestCase):
    def test_breaks_on_pause_and_speaker(self):
        segments = group_segments(
            [
                word("Hello", 0.0, 0.4),
                {"text": " ", "start": 0.4, "end": 0.41, "type": "spacing"},
                word("there.", 0.42, 0.9),
                word("Next.", 2.0, 2.4, "speaker_1"),
            ]
        )
        self.assertEqual([segment.text for segment in segments], ["Hello there.", "Next."])
        self.assertEqual(segments[1].speaker, "speaker_1")

    def test_index_marks_five_minute_steps(self):
        segments = group_segments(
            [
                word("Welcome.", 0.0, 1.0),
                word("Later.", 301.0, 302.0),
            ]
        )
        markdown = render_markdown(
            {"source": "videos/talk.mp4", "language_code": "en", "language_probability": 1, "duration": 400},
            segments,
            {"speaker_0": "Ada"},
        )
        self.assertIn("00:00:00.000 Ada — Welcome.", markdown)
        self.assertIn("00:05:01.000 Ada — Later.", markdown)


class BriefTests(unittest.TestCase):
    def test_example_is_cuttable_and_warns_about_order(self):
        example = find_root() / "examples" / "brief.example.json"
        report = check_brief(json.loads(example.read_text(encoding="utf-8")))
        self.assertEqual(report.errors, [])
        self.assertTrue(any("source order" in warning for warning in report.warnings))

    def test_quote_must_match_the_words_in_range(self):
        words = [word("Hello", 1.0, 1.4), word("world", 1.4, 1.8)]
        self.assertIsNone(quote_mismatch("Hello world", words, 1.0, 1.8))
        self.assertIn("does not match", quote_mismatch("a different sentence", words, 1.0, 1.8))

    def test_inverted_range_and_unknown_transition_fail(self):
        report = check_brief(
            {
                "title": "Title",
                "hook": "Hook",
                "transition": {"type": "spin"},
                "moments": [
                    {
                        "id": "hook",
                        "start": 5,
                        "end": 4,
                        "speaker": "speaker_0",
                        "quote": "Hello",
                        "why": "Open",
                    }
                ],
            }
        )
        self.assertTrue(any("end must be after start" in error for error in report.errors))
        self.assertTrue(any("Unknown transition" in error for error in report.errors))


class PlanTests(unittest.TestCase):
    def test_snap_includes_the_whole_word_then_pads(self):
        words = [word("Hello", 1.0, 1.4), word("there", 1.42, 1.9)]
        self.assertEqual(snap_range(1.2, 1.6, words, 0, 10), (1.0, 1.9))
        self.assertEqual(snap_range(1.2, 1.6, words, 0.1, 10), (0.9, 2.0))

    def test_offsets_account_for_every_overlap(self):
        offsets = transition_offsets([1.5, 2.0, 2.0], 0.3)
        self.assertAlmostEqual(offsets[0], 1.2)
        self.assertAlmostEqual(offsets[1], 2.9)

    def test_filter_uses_probed_duration_margin(self):
        fade = build_filter([2.0, 2.0], "fade", 0.25, True)
        self.assertIn("xfade=transition=fade:duration=0.250:offset=1.730", fade)
        self.assertIn("acrossfade=d=0.250", fade)
        self.assertIn("concat=", build_filter([2.0, 2.0], "cut", 0, True))

    def test_bad_brief_is_refused_before_ffmpeg(self):
        with self.assertRaises(SystemExit):
            build_plan({"title": "", "moments": []})


class PathTests(unittest.TestCase):
    def test_slug_strips_accents(self):
        self.assertEqual(slugify("Événement Final.mp4"), "evenement-final")
        self.assertEqual(slug_text("Payoff 2"), "payoff-2")

    def test_collision_gets_its_own_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "talk.mp4"
            other = root / "nested"
            other.mkdir()
            second = other / "talk.mp4"
            first.write_bytes(b"one")
            second.write_bytes(b"two")
            folder = root / "generated" / "talk"
            folder.mkdir(parents=True)
            (folder / "source.json").write_text(
                json.dumps({"source": str(first.resolve())}),
                encoding="utf-8",
            )
            self.assertEqual(allocate_slug(first, root), "talk")
            self.assertTrue(allocate_slug(second, root).startswith("talk-"))

    def test_current_transcript_matches_size_and_mtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "talk.mp4"
            source.write_bytes(b"video")
            transcript = root / "transcript.json"
            transcript.write_text("{}", encoding="utf-8")
            stat = source.stat()
            meta = root / "source.json"
            meta.write_text(
                json.dumps(
                    {
                        "source": str(source.resolve()),
                        "size": stat.st_size,
                        "mtime_ns": str(stat.st_mtime_ns),
                        "sha256": "abc",
                    }
                ),
                encoding="utf-8",
            )
            paths = {"transcript_json": transcript, "source_json": meta, "root": root}
            self.assertTrue(_is_current(paths, source))
            source.write_bytes(b"video!")
            self.assertFalse(_is_current(paths, source))


class SkillTests(unittest.TestCase):
    def test_each_skill_names_itself(self):
        root = find_root() / "skills"
        found = sorted(path.name for path in root.iterdir() if path.is_dir())
        self.assertEqual(
            found,
            ["brief-session", "cut-short", "generate-short", "transcribe-session"],
        )
        for name in found:
            text = (root / name / "SKILL.md").read_text(encoding="utf-8")
            self.assertTrue(text.startswith("---\n"))
            header, _body = text.split("\n---", 1)
            self.assertIn(f"name: {name}", header)
            self.assertIn("description:", header)
