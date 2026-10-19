import tests  # noqa: F401 - keeps the real user data out of reach (see tests/__init__.py)
import unittest

from app.dub_text import (
    MAX_LEAD,
    UNIT_MAX_SEC,
    build_dub_units,
    expected_speech_sec,
    has_foreign_script,
    plan_placement,
    speech_free_gaps,
)


def _words(*items):
    return [{"start": s, "end": e, "word": w, "prob": 0.9} for w, s, e in items]


class BuildDubUnitsTest(unittest.TestCase):
    def test_splits_on_sentence_end_and_long_pause(self):
        seg = {
            "start": 0.0, "end": 6.0, "speaker": "SPEAKER_01", "text": "Привет всем. Начнём сейчас, хорошо",
            "words": _words(
                (" Привет", 0.0, 0.6), (" всем.", 0.7, 1.4),
                (" Начнём", 1.5, 2.0), (" сейчас,", 2.1, 2.6),
                (" хорошо", 3.6, 4.2),  # 1.0 s pause before this word
            ),
        }
        units = build_dub_units([seg])
        self.assertEqual([u["text"] for u in units], ["Привет всем.", "Начнём сейчас,", "хорошо"])
        self.assertEqual([u["id"] for u in units], [0, 1, 2])
        self.assertTrue(all(u["speaker"] == "SPEAKER_01" for u in units))
        self.assertEqual((units[0]["start"], units[0]["end"]), (0.0, 1.4))

    def test_short_sentence_is_not_split_off(self):
        seg = {"start": 0.0, "end": 2.0, "speaker": "S", "text": "Да. Конечно",
               "words": _words((" Да.", 0.0, 0.3), (" Конечно", 0.35, 1.0))}
        self.assertEqual([u["text"] for u in build_dub_units([seg])], ["Да. Конечно"])

    def test_long_run_without_punctuation_is_capped(self):
        words = _words(*[(f" w{i}", i * 0.5, i * 0.5 + 0.45) for i in range(60)])
        units = build_dub_units([{"start": 0.0, "end": 30.0, "speaker": "S", "text": "", "words": words}])
        self.assertGreater(len(units), 2)
        for u in units:
            self.assertLessEqual(u["end"] - u["start"], UNIT_MAX_SEC + 0.5)

    def test_segment_without_words_is_kept_whole(self):
        units = build_dub_units([{"start": 1.0, "end": 2.5, "speaker": "S", "text": " Ок ", "words": []},
                                 {"start": 3.0, "end": 3.5, "speaker": "S", "text": "  ", "words": []}])
        self.assertEqual(len(units), 1)
        self.assertEqual((units[0]["text"], units[0]["start"], units[0]["end"]), ("Ок", 1.0, 2.5))


class PlacementTest(unittest.TestCase):
    def test_monologue_phrases_are_pulled_together_within_limit(self):
        units = [
            {"start": 0.0, "end": 3.0, "speaker": "A", "dur": 1.5},
            {"start": 3.2, "end": 6.0, "speaker": "A", "dur": 2.0},
        ]
        plan = plan_placement(units, 20.0, 1.6)
        self.assertLess(plan[1]["start"], 3.2)
        self.assertGreaterEqual(plan[1]["start"], 3.2 - MAX_LEAD - 1e-9)

    def test_speaker_change_keeps_original_start(self):
        units = [
            {"start": 0.0, "end": 3.0, "speaker": "A", "dur": 1.0},
            {"start": 3.2, "end": 6.0, "speaker": "B", "dur": 2.0},
        ]
        self.assertEqual(plan_placement(units, 20.0, 1.6)[1]["start"], 3.2)

    def test_long_clip_is_sped_up_but_capped(self):
        units = [
            {"start": 0.0, "end": 1.0, "speaker": "A", "dur": 5.0},
            {"start": 1.5, "end": 3.0, "speaker": "B", "dur": 1.0},
        ]
        self.assertEqual(plan_placement(units, 10.0, 1.6)[0]["tempo"], 1.6)

    def test_phrases_never_overlap(self):
        units = [{"start": i * 1.0, "end": i * 1.0 + 0.9, "speaker": "A", "dur": 0.8} for i in range(10)]
        plan = plan_placement(units, 20.0, 1.6)
        for a, b, u in zip(plan, plan[1:], units):
            self.assertLessEqual(a["start"] + u["dur"] / a["tempo"], b["start"] + 1e-6)


class TextChecksTest(unittest.TestCase):
    def test_expected_duration_depends_on_script(self):
        self.assertGreater(expected_speech_sec("你好世界你好世界", "zh"), expected_speech_sec("helloworld", "en"))

    def test_cjk_leakage_detection(self):
        self.assertTrue(has_foreign_script("Вы先开始。", "ru"))
        self.assertFalse(has_foreign_script("Вы начинайте.", "ru"))
        self.assertFalse(has_foreign_script("你先开始。", "zh"))


if __name__ == "__main__":
    unittest.main()


class SpeechFreeGapsTest(unittest.TestCase):
    def test_gaps_keep_a_margin_around_phrases(self):
        units = [{"start": 1.0, "end": 2.0}, {"start": 2.2, "end": 3.0}, {"start": 5.0, "end": 6.0}]
        gaps = speech_free_gaps(units, 8.0, margin=0.1, min_len=0.25)
        self.assertEqual([(round(a, 2), round(b, 2)) for a, b in gaps], [(0.0, 0.9), (3.1, 4.9), (6.1, 8.0)])

    def test_short_and_overlapping_spans(self):
        units = [{"start": 0.05, "end": 2.0}, {"start": 1.5, "end": 4.0}]
        # 0..0 before the first phrase is too short; overlapping phrases leave no gap between them
        self.assertEqual(speech_free_gaps(units, 4.2, margin=0.1, min_len=0.25), [])
        self.assertEqual(speech_free_gaps([], 3.0), [(0.0, 3.0)])
