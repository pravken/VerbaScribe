import tests  # noqa: F401 - keeps the real user data out of reach (see tests/__init__.py)
import unittest
from pathlib import Path

from app.sysinfo import short_cpu_name

# The release CI runs the tests without the app's packages: what needs them is skipped there
try:
    from app import llm
    from app.live_translate import LiveTranslator
    from app.ollama_client import _segment_parts, build_transcript_lines
except ImportError:
    llm = None
needs_deps = unittest.skipIf(llm is None, "app dependencies not installed")


class CpuNameTest(unittest.TestCase):
    def test_short_names(self):
        self.assertEqual(short_cpu_name("Intel(R) Core(TM) i7-9700K CPU @ 3.60GHz"), "Intel Core i7-9700K")
        self.assertEqual(short_cpu_name("AMD Ryzen 9 5950X 16-Core Processor"), "AMD Ryzen 9 5950X")
        self.assertEqual(short_cpu_name("AMD Ryzen 7 7840HS w/ Radeon 780M Graphics"), "AMD Ryzen 7 7840HS")
        self.assertEqual(short_cpu_name(""), "")


def _words(*items):
    return [{"word": f" {w}", "start": s, "end": s + 0.4} for w, s in items]


@needs_deps
class TranscriptLinesTest(unittest.TestCase):
    def test_long_segment_is_split_at_sentence_ends(self):
        words = _words(("Первая", 0.0), ("фраза.", 3.0), ("Вторая", 6.5), ("фраза", 9.0), ("тут.", 12.0),
                       ("Третья.", 20.0))
        seg = {"start": 0.0, "end": 20.4, "speaker": "S1", "text": "Первая фраза. Вторая фраза тут. Третья.",
               "words": words}
        self.assertEqual(_segment_parts(seg), [(0.0, "Первая фраза. Вторая фраза тут."), (20.0, "Третья.")])
        lines = build_transcript_lines([seg], {"S1": {"name": "Анна"}})
        self.assertEqual(lines[0][:6], "[00:00")
        self.assertIn("Анна: Первая", lines[0])
        self.assertNotIn("Анна", lines[1])
        self.assertTrue(lines[1].startswith("[00:20"))

    def test_edited_or_short_segments_stay_whole(self):
        seg = {"start": 0.0, "end": 30.0, "text": "Совсем другой текст", "words": _words(("а.", 0.0), ("б.", 20.0))}
        self.assertEqual(_segment_parts(seg), [(0.0, "Совсем другой текст")])
        self.assertEqual(_segment_parts({"start": 1.0, "end": 3.0, "text": "Коротко"}), [(1.0, "Коротко")])


@needs_deps
class DefaultModelTest(unittest.TestCase):
    def test_picks_current_fast_model(self):
        gemini = ["gemini-2.0-flash", "gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-3.0-flash-preview",
                  "gemini-2.5-pro", "text-embedding-004", "gemma-3-27b-it"]
        self.assertEqual(llm.pick_default_model("gemini", gemini), "gemini-2.5-flash")
        self.assertEqual(llm.pick_default_model("openai", ["gpt-4o-mini", "gpt-5-mini", "gpt-5-nano", "tts-1"]), "gpt-5-mini")
        self.assertIsNone(llm.pick_default_model("openai", []))


@needs_deps
class AutoPairTest(unittest.TestCase):
    def test_other_language_of_the_pair(self):
        tr = LiveTranslator.__new__(LiveTranslator)  # without its worker thread
        tr.target, tr.pair = "auto", ["ru", "en"]
        self.assertEqual(tr.target_for("ru"), "en")
        self.assertEqual(tr.target_for("en"), "ru")
        self.assertEqual(tr.target_for("de"), "ru")
        tr.target = "fr"
        self.assertEqual(tr.target_for("ru"), "fr")


@needs_deps
class RecordingApiTest(unittest.TestCase):
    def setUp(self):
        try:
            from app import main
        except ImportError:
            self.skipTest("fastapi not installed")
        self.main = main

    def test_mix_commands(self):
        cmd = self.main.record_mix_command
        page, system, dest = Path("p.webm"), Path("s.wav"), Path("out")
        self.assertIsNone(cmd("ff", page, None, False, True, dest))
        self.assertIn("copy", cmd("ff", page, None, True, False, dest))
        self.assertIn("aac", cmd("ff", None, system, False, False, dest))
        video_mic = cmd("ff", page, system, True, True, dest)
        self.assertIn("libopus", video_mic)
        self.assertTrue(any("amix" in a for a in video_mic))
        video_only = cmd("ff", page, system, True, False, dest)
        self.assertIn("1:a", video_only)
        self.assertFalse(any("amix" in a for a in video_only))
        self.assertTrue(any("amix" in a for a in cmd("ff", page, system, False, True, dest)))

    def test_live_target(self):
        from fastapi import HTTPException

        self.assertEqual(self.main._live_target("auto", ["ru", "en"]), ("auto", ["ru", "en"]))
        self.assertEqual(self.main._live_target("EN", None), ("en", None))
        self.assertEqual(self.main._live_target("", None), (None, None))
        for bad in [("xx", None), ("auto", ["ru", "ru"]), ("auto", ["ru"])]:
            with self.assertRaises(HTTPException):
                self.main._live_target(*bad)


class LibraryBadgesTest(unittest.TestCase):
    def test_list_shows_what_a_recording_has(self):
        from app import database as db

        db.init_db()
        mid = db.create_meeting("Бейджи", "a.wav", "/tmp/a.wav")["id"]
        row = next(m for m in db.list_meetings() if m["id"] == mid)
        self.assertEqual((row["has_transcript"], row["has_summary"], row["translations_langs"], row["dub_langs"]),
                         (False, False, [], []))
        db.save_meeting_results(mid, segments=[{"id": 1, "start": 0, "end": 1, "text": "x"}], summaries={"protocol": "## x"})
        db.save_meeting_translation(mid, "en", {"segments": {}})
        db.save_meeting_dub(mid, {"lang": "de", "path": "", "srt_path": ""})
        row = next(m for m in db.list_meetings() if m["id"] == mid)
        self.assertTrue(row["has_transcript"] and row["has_summary"])
        self.assertEqual(row["translations_langs"], ["en"])
        self.assertEqual(row["dub_langs"], ["de"])
        db.delete_meeting(mid)


if __name__ == "__main__":
    unittest.main()
