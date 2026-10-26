"""The player of the redesign: pauses to skip, the text shown without fillers (the text itself stays),
the saved copies of a recording, the options of the text export."""
import tests  # noqa: F401 - keeps the real user data out of reach (see tests/__init__.py)
import tempfile
import unittest
from pathlib import Path

from app.copies import copy_kind, delete_copy, list_copies
from app.database import add_export_path, create_meeting, get_meeting, init_db, save_meeting_results
from app.media_cut import speech_intervals, speech_pauses
from app.text_utils import filler_view


def _w(word, start, end):
    return {"word": word, "start": start, "end": end}


SEGS = [
    {"id": 1, "start": 0.5, "end": 3.0, "speaker": "S1", "text": "Эээ привет всем",
     "words": [_w(" Эээ", 0.5, 1.0), _w(" привет", 1.1, 1.8), _w(" всем", 1.9, 3.0)]},
    {"id": 2, "start": 8.0, "end": 10.0, "speaker": "S2", "text": "Начнём с плана",
     "words": [_w(" Начнём", 8.0, 8.6), _w(" с", 8.7, 8.8), _w(" плана", 8.9, 10.0)]},
    {"id": 3, "start": 10.4, "end": 12.0, "speaker": "S1", "text": "ну типа да", "words": []},
]


class PausesTest(unittest.TestCase):
    def test_pauses_between_speech_fillers_count_as_silence(self):
        pauses = speech_pauses(SEGS, 20.0)
        # the filler at the start is silence, but 0.85 s before "привет" is too short to skip
        # between "всем" (3.0) and "Начнём" (8.0), padded on both sides
        self.assertEqual(pauses[0], (3.25, 7.75))
        long_lead = speech_pauses([dict(SEGS[1])], 20.0)
        self.assertEqual(long_lead[0], (0.0, 7.75))
        # the end of the recording after the last words
        self.assertAlmostEqual(pauses[-1][1], 19.75, places=2)
        # 0.4 s between segments 2 and 3 is part of speech
        self.assertFalse(any(a < 10.2 < b for a, b in pauses))
        # only fillers in the text: nothing is skipped (not one pause over the whole recording)
        only_fillers = [{"id": 1, "start": 20.0, "end": 21.0, "speaker": "S1", "text": "э-э", "words": [_w(" э-э", 20.0, 21.0)]}]
        self.assertEqual(speech_pauses(only_fillers, 60.0), [])

    def test_no_speech_no_duration(self):
        self.assertEqual(speech_pauses([], 0.0), [])

    def test_clean_cut_leaves_fillers_out(self):
        kept = speech_intervals(SEGS, 20.0)
        self.assertGreater(kept[0][0], 0.9)  # "Эээ" (0.5–1.0) is not in the cut


class FillerViewTest(unittest.TestCase):
    def test_view_hides_fillers_and_keeps_the_text(self):
        segs = [dict(s) for s in SEGS]
        view = filler_view(segs)
        self.assertEqual(view["segments"]["1"]["drop"], [0])
        self.assertEqual(view["segments"]["1"]["text"], "Привет всем")
        self.assertEqual(view["segments"]["3"]["drop"], [])
        self.assertEqual(view["segments"]["3"]["text"], "да")
        self.assertNotIn("2", view["segments"])
        self.assertEqual(view["count"], 2)
        self.assertAlmostEqual(view["seconds"], 0.5)
        self.assertEqual(segs[0]["text"], "Эээ привет всем")  # nothing changed


class CopiesTest(unittest.TestCase):
    def setUp(self):
        init_db()
        self.dir = Path(tempfile.mkdtemp())

    def test_media_exports_are_copies_and_delete_removes_file_and_line(self):
        m = create_meeting(title="Запись", filename="a.wav", media_path=str(self.dir / "a.wav"))
        clean = self.dir / "Запись.clean.m4a"
        clean.write_bytes(b"x" * 10)
        doc = self.dir / "Запись.docx"
        doc.write_bytes(b"d")
        add_export_path(m["id"], str(clean))
        add_export_path(m["id"], str(doc))
        copies = list_copies(get_meeting(m["id"]))
        self.assertEqual([c["name"] for c in copies], ["Запись.clean.m4a"])  # a document is not a copy
        self.assertEqual(copies[0]["kind"], "clean")
        self.assertEqual(copies[0]["size"], 10)
        self.assertTrue(delete_copy(get_meeting(m["id"]), copies[0]["id"]))
        self.assertFalse(clean.exists())
        self.assertEqual(list_copies(get_meeting(m["id"])), [])
        self.assertIn(str(doc), get_meeting(m["id"])["exports"])

    def test_kinds(self):
        self.assertEqual(copy_kind(Path("x.clean-sound.m4a")), "denoise")
        self.assertEqual(copy_kind(Path("x.highlights.mp4")), "highlights")
        self.assertEqual(copy_kind(Path("x.mp3")), "audio")


class ExportOptionsTest(unittest.TestCase):
    def test_times_names_and_fillers(self):
        try:
            from app.export_build import build_export_bytes
        except ImportError:  # python-docx is not installed
            self.skipTest("app dependencies not installed")
        init_db()
        m = create_meeting(title="Опции", filename="a.wav", media_path="a.wav")
        save_meeting_results(m["id"], segments=SEGS, speakers={"S1": {"name": "Анна"}, "S2": {"name": "Борис"}}, language="ru")
        m = get_meeting(m["id"])
        full = build_export_bytes(m, "txt")[0].decode("utf-8")
        self.assertIn("[00:00] Анна: Эээ привет всем", full)
        bare = build_export_bytes(m, "txt", opts={"times": False, "names": False, "nofill": True})[0].decode("utf-8")
        self.assertIn("\nПривет всем", bare)
        self.assertNotIn("Анна:", bare)
        self.assertNotIn("[00:00]", bare)
        self.assertIn("Эээ", get_meeting(m["id"])["segments"][0]["text"])  # the export changed nothing
        # JSON is the full transcript: «Без запинок» never takes words out of it
        js = build_export_bytes(m, "json", opts={"nofill": True})[0].decode("utf-8")
        self.assertIn("Эээ", js)

    def test_without_fillers_keeps_each_translation_on_its_line(self):
        try:
            from app.export_build import build_export_bytes
        except ImportError:  # python-docx is not installed
            self.skipTest("app dependencies not installed")

        def srt(segs, tr):
            m = {"id": 1, "title": "T", "created_at": "2026-01-01 10:00:00", "segments": segs, "speakers": {},
                 "translations": {"en": {"model": "x", "segments": {k: {"text": v, "stale": False} for k, v in tr.items()}}}}
            return build_export_bytes(m, "srt", lang="en", opts={"nofill": True})[0].decode("utf-8")

        # A dictation's only line has the id 0
        self.assertIn("Hello world", srt([{"id": 0, "start": 0.0, "end": 2.0, "text": "Привет мир"}], {"0": "Hello world"}))
        # A line of fillers only is left out: the next lines keep their own translations
        out = srt([{"id": 1, "start": 0.0, "end": 1.0, "text": "Э-э."}, {"id": 2, "start": 1.0, "end": 2.0, "text": "Первая."},
                   {"id": 3, "start": 2.0, "end": 3.0, "text": "Вторая."}], {"1": "Uh.", "2": "First.", "3": "Second."})
        self.assertNotIn("Uh.", out)
        self.assertLess(out.index("First."), out.index("Second."))

    def test_names_option_applies_to_translated_subtitles(self):
        try:
            from app.export_build import build_export_bytes
        except ImportError:  # python-docx is not installed
            self.skipTest("app dependencies not installed")
        m = {"id": 1, "title": "T", "created_at": "2026-01-01 10:00:00", "speakers": {"S1": {"name": "Анна"}},
             "segments": [{"id": 1, "start": 0.0, "end": 2.0, "text": "Привет", "speaker": "S1"}],
             "translations": {"en": {"model": "x", "segments": {"1": {"text": "Hello", "stale": False}}}}}
        named = build_export_bytes(m, "srt", lang="en")[0].decode("utf-8")
        bare = build_export_bytes(m, "srt", lang="en", opts={"names": False})[0].decode("utf-8")
        self.assertIn("[Анна] Hello", named)
        self.assertNotIn("Анна", bare)


if __name__ == "__main__":
    unittest.main()
