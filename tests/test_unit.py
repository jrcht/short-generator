import json
import tempfile
import unittest
from pathlib import Path

from shortgen.brief import check_brief, quote_mismatch
from shortgen.cut import build_filter, build_plan, snap_range, transition_offsets
from shortgen.captions import cues_for_clips
from shortgen.package import clear_window, cover_crop, follow_crop, opening_cut, watch_cut
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


class PackageTests(unittest.TestCase):
    def test_opening_keeps_the_card_and_drops_the_green_hold(self):
        samples = []
        for index in range(30):
            time = index / 10
            if time < 1.0:
                samples.append((time, 0.0, 0.0 if index == 0 else 0.01))
            elif time < 1.3:
                samples.append((time, (time - 1.0) / 0.3, 0.2))
            else:
                samples.append((time, 1.0, 0.2 if time < 1.4 else 0.0))
        preroll, keyed_end = opening_cut(samples, 3.0)
        self.assertAlmostEqual(preroll, 0.9, delta=0.15)
        self.assertGreater(keyed_end, preroll)
        self.assertLess(keyed_end, 1.6)

    def test_watch_drops_the_green_head_and_keeps_the_card(self):
        samples = []
        for index in range(40):
            time = index / 10
            if time < 1.0:
                samples.append((time, 1.0, 0.0))
            elif time < 1.4:
                samples.append((time, max(0.0, 1.0 - (time - 1.0) / 0.4), 0.25))
            else:
                samples.append((time, 0.0, 0.2 if time < 1.8 else 0.01))
        overlap, post = watch_cut(samples, 4.0)
        self.assertAlmostEqual(overlap, 0.9, delta=0.15)
        self.assertGreater(post, 1.3)
        self.assertLess(post, 2.4)

    def test_cover_crop_cuts_the_wide_side(self):
        width, height = cover_crop(1920, 1080, 1080, 1548)
        self.assertLess(width, 1920)
        self.assertEqual(height, 1080)
        self.assertEqual(width % 2, 0)

    def test_follow_crop_stays_with_the_speaker(self):
        centers = [(0.0, 400.0), (2.0, 1400.0)]
        xs = follow_crop(centers, 2.0, 1920, 754, fps=10)
        self.assertEqual(len(xs), 20)
        self.assertLess(xs[1], 500)
        self.assertGreater(xs[-1], 800)
        self.assertTrue(all(0 <= x <= 1920 - 754 for x in xs))
        self.assertTrue(all(x % 2 == 0 for x in xs))

    def test_captions_follow_each_cut_and_drop_fillers(self):
        clips = [
            {"start": 10.0, "end": 14.0},
            {"start": 20.0, "end": 23.0},
        ]
        words = [
            word("whenever", 10.1, 10.4),
            word("uh", 10.5, 10.7),
            word("we", 10.8, 11.0),
            word("use", 11.05, 11.3),
            word("them.", 11.4, 11.8),
            word("So", 12.0, 12.2),
            word("what,", 12.3, 12.5),
            word("what", 12.55, 12.8),
            word("should", 12.9, 13.1),
            word("we", 13.15, 13.3),
            word("do?", 13.4, 13.7),
            word("ac-actually", 20.2, 20.6),
            word("install", 20.7, 21.1),
            word("locally.", 21.2, 21.7),
        ]
        cues = cues_for_clips(clips, words, fade=0.0)
        texts = [cue.text.replace("\n", " ") for cue in cues]
        self.assertIn("whenever we use them.", texts)
        self.assertNotIn("uh", " ".join(texts).split())
        self.assertIn("So what should we do?", texts)
        later = [cue for cue in cues if cue.text.replace("\n", " ").startswith("actually")]
        self.assertEqual(len(later), 1)
        self.assertAlmostEqual(later[0].start, 4.2, delta=0.15)
        self.assertTrue(all(cue.end > cue.start for cue in cues))
        self.assertLess(max(cue.end for cue in cues if cue.start < 4), 4.05)

    def test_clear_window_finds_the_transparent_band(self):
        width, height = 8, 10
        alpha = bytearray([255]) * (width * height)
        for y in range(2, 8):
            for x in range(width):
                alpha[y * width + x] = 0
        self.assertEqual(clear_window(bytes(alpha), width, height), (0, 2, 8, 6))


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
            ["brief-session", "cut-short", "generate-short", "transcribe-session", "write-article"],
        )
        for name in found:
            text = (root / name / "SKILL.md").read_text(encoding="utf-8")
            self.assertTrue(text.startswith("---\n"))
            header, _body = text.split("\n---", 1)
            self.assertIn(f"name: {name}", header)
            self.assertIn("description:", header)
