import tests  # noqa: F401 - keeps the real user data out of reach (see tests/__init__.py)
import unittest

try:
    from app.live_translate import _degenerate, _first_line, collapse_repeats, serbian_to_cyrillic, serbian_to_latin
except ImportError:  # httpx/app dependencies are not installed (e.g. in the release CI job)
    collapse_repeats = None


@unittest.skipIf(collapse_repeats is None, "app dependencies not installed")
class LiveTranslateTextTest(unittest.TestCase):
    def test_looping_runs_are_collapsed(self):
        self.assertEqual(collapse_repeats("что мы здесь будем что мы здесь будем испытать"), "что мы здесь будем испытать")

    def test_single_word_repeats_stay(self):
        self.assertEqual(collapse_repeats("да, да, хорошо"), "да, да, хорошо")

    def test_degenerate_answers_are_detected(self):
        self.assertTrue(_degenerate("that we're going to experience here.",
                                    "что мы здесь будем что мы здесь будем то, что мы здесь будем испытывать"))
        self.assertTrue(_degenerate("Hello", ""))
        self.assertFalse(_degenerate("that we're going to experience here.", "что мы будем испытывать здесь."))

    def test_only_the_first_line_without_quotes(self):
        self.assertEqual(_first_line('"Hallo"\n\nThis is the translation.'), "Hallo")
        self.assertEqual(_first_line("мы будем“”"), "мы будем")

    def test_serbian_scripts(self):
        self.assertEqual(serbian_to_cyrillic("што ćemo ovde, Ljubljana, džem"), "што ћемо овде, Љубљана, џем")
        self.assertEqual(serbian_to_latin("Љубљана, џем, ћемо"), "Ljubljana, džem, ćemo")


class PhraseLanguageTest(unittest.TestCase):
    def test_plain_russian_and_english(self):
        from app.live_translate import phrase_language

        self.assertEqual(phrase_language("Давайте перенесём встречу на пятницу"), "ru")
        self.assertEqual(phrase_language("Let's move the meeting to Friday"), "en")
        self.assertIsNone(phrase_language("Привіт, як справи? Їдемо завтра"))  # Ukrainian
        self.assertIsNone(phrase_language("Wir verschieben das Treffen auf Freitag"))  # German: no English words
        self.assertIsNone(phrase_language("Ок"))  # too short to tell


if __name__ == "__main__":
    unittest.main()
