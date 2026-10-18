import unittest

from app.dub_text import UNIT_MAX_SEC, build_dub_units


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


if __name__ == "__main__":
    unittest.main()
