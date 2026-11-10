import json
import os
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from shortgen.brief import check_brief
from shortgen.captions import cues_for_clips
from shortgen.cut import clip_audio_filter
from shortgen.view import Viewer, assembly_pieces, make_server, viewer_for


def word(text, start, end):
    return {"text": text, "start": start, "end": end, "type": "word"}


class ViewTests(unittest.TestCase):
    def test_voice_level_is_a_volume_filter(self):
        self.assertTrue(clip_audio_filter(1.5).startswith("volume=1.500,"))
        self.assertNotIn("volume=", clip_audio_filter(1))

    def test_edited_caption_replaces_the_generated_line(self):
        clips = [{"id": "hook", "start": 10.0, "end": 14.0}]
        words = [word("Hello", 10.2, 10.6), word("there.", 10.7, 11.2)]
        cues = cues_for_clips(clips, words, 0.0, texts=[["Local models."]])
        self.assertEqual([cue.text.replace("\n", " ") for cue in cues], ["Local models."])

    def test_gain_outside_range_is_rejected(self):
        report = check_brief(_brief(gain=3))
        self.assertTrue(any("gain" in error for error in report.errors))
        self.assertTrue(check_brief(_brief(gain=0.5)).ok)

    def test_assembly_lists_every_merged_file_in_order(self):
        clips = [
            {"id": "hook", "start": 289.58, "end": 309.1},
            {"id": "turn", "start": 309.82, "end": 318.0},
            {"id": "proof", "start": 336.06, "end": 345.32},
            {"id": "payoff", "start": 492.62, "end": 514.84},
        ]
        package = {
            "opening_preroll": 2.2,
            "watch_post": 2.433,
            "ending": 9.761,
            "body": 58.43,
        }
        pieces = assembly_pieces(clips, 0.25, package)
        self.assertEqual([piece["id"] for piece in pieces], ["opening", "hook", "turn", "proof", "payoff", "watch", "ending"])
        self.assertEqual(pieces[0]["start"], 0.0)
        self.assertEqual(pieces[0]["end"], 2.2)
        self.assertAlmostEqual(pieces[1]["start"], 2.2)
        self.assertAlmostEqual(pieces[-1]["end"], 2.2 + 58.43 + 2.433 + 9.761, places=2)
        for previous, current in zip(pieces, pieces[1:]):
            self.assertAlmostEqual(previous["end"], current["start"], places=2)

    def test_a_rendered_short_opens_when_the_session_file_is_gone(self):
        with _project() as (root, video):
            short = root / "generated" / "talk" / "shorts" / "main" / "short.mp4"
            short.parent.mkdir(parents=True)
            short.write_bytes(b"0123456789")
            video.unlink()
            with patch.dict(os.environ, {"SHORTGEN_ROOT": str(root)}):
                from_short = viewer_for(str(short))
                from_name = viewer_for("videos/talk.mp4")
            self.assertIsNone(from_short.source)
            self.assertEqual(from_short.paths["slug"], "talk")
            self.assertEqual(from_short.paths["name"], "main")
            self.assertEqual(from_short.project()["source_missing"], "videos/talk.mp4")
            self.assertIsNone(from_name.source)
            self.assertEqual(from_name.paths["brief_json"], from_short.paths["brief_json"])

    def test_player_serves_the_brief_and_saves_a_trim(self):
        with _project() as (root, video):
            with patch.dict(os.environ, {"SHORTGEN_ROOT": str(root)}):
                viewer = Viewer(video, "main")
                server = make_server(viewer, 0)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                self.addCleanup(server.shutdown)
                self.addCleanup(server.server_close)
                url = f"http://127.0.0.1:{server.server_address[1]}"

                page = urllib.request.urlopen(url).read().decode()
                self.assertIn("Voice", page)
                project = json.load(urllib.request.urlopen(url + "/api/project"))
                self.assertEqual(project["title"], "Tone")
                self.assertEqual(project["clips"][0]["id"] if "clips" in project else project["brief"]["moments"][0]["id"], "open")
                self.assertTrue(any(cue["text"] for cue in project["cues"]))

                ranged = urllib.request.Request(url + "/media/source", headers={"Range": "bytes=0-3"})
                with urllib.request.urlopen(ranged) as response:
                    self.assertEqual(response.status, 206)
                    self.assertEqual(response.read(), b"0123")

                brief = project["brief"]
                brief["moments"][0]["end"] = 3
                brief["moments"][0]["gain"] = 0.5
                body = json.dumps(brief).encode()
                saved = json.load(urllib.request.urlopen(urllib.request.Request(
                    url + "/api/brief",
                    data=body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )))
                self.assertTrue(saved["ok"])
                written = json.loads((root / "generated" / "talk" / "brief" / "main.json").read_text())
                self.assertEqual(written["moments"][0]["gain"], 0.5)
                self.assertIn("voice", (root / "generated" / "talk" / "brief" / "main.md").read_text())

                brief["moments"][0]["gain"] = 4
                rejected = urllib.request.Request(
                    url + "/api/brief",
                    data=json.dumps(brief).encode(),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(rejected)
                self.assertEqual(caught.exception.code, 400)

                with patch("shortgen.view.cut_video", return_value={}) as cut:
                    started = json.load(urllib.request.urlopen(urllib.request.Request(url + "/api/render", data=b"{}", method="POST")))
                    self.assertEqual(started["status"], "running")
                    for _ in range(50):
                        status = json.load(urllib.request.urlopen(url + "/api/render"))
                        if status["status"] != "running":
                            break
                    self.assertEqual(status["status"], "done")
                    cut.assert_called_once()


def _brief(gain=None):
    moment = {
        "id": "open",
        "start": 1,
        "end": 3,
        "speaker": "speaker_0",
        "quote": "Hello there.",
        "why": "Open.",
    }
    if gain is not None:
        moment["gain"] = gain
    return {
        "title": "Tone",
        "hook": "Two words.",
        "transition": {"type": "cut"},
        "moments": [moment],
    }


class _project:
    def __enter__(self):
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        video = root / "videos" / "talk.mp4"
        video.parent.mkdir()
        video.write_bytes(b"0123456789")
        folder = root / "generated" / "talk"
        (folder / "brief").mkdir(parents=True)
        (folder / "transcript").mkdir()
        (folder / "source.json").write_text(json.dumps({"source": "videos/talk.mp4", "duration": 30}), encoding="utf-8")
        (folder / "brief" / "main.json").write_text(json.dumps(_brief()), encoding="utf-8")
        (folder / "transcript" / "transcript.json").write_text(
            json.dumps({"words": [word("Hello", 1.1, 1.4), word("there.", 1.5, 2.0)]}),
            encoding="utf-8",
        )
        return root, video

    def __exit__(self, *args):
        self.tmp.cleanup()
