import tests  # noqa: F401 - keeps the real user data out of reach (see tests/__init__.py)
import unittest

from app.translation import build_cues, cues_to_srt, cues_to_vtt


def _words(text, start, step=0.5):
    return [{"word": " " + w, "start": start + i * step, "end": start + i * step + 0.4} for i, w in enumerate(text.split())]


class SubtitleCueTest(unittest.TestCase):
    def setUp(self):
        text = ("Сегодня мы обсуждаем релиз. Его нужно выпустить до пятницы, иначе клиенты будут недовольны, "
                "а мы потеряем ещё одну неделю на согласования с отделом качества.")
        self.seg = {"id": 1, "start": 10.0, "end": 30.0, "speaker": "S1", "text": text, "words": _words(text, 10.0)}

    def test_original_is_cut_by_word_timings(self):
        cues = build_cues([self.seg], None, variant="original", speakers={"S1": {"name": "Анна"}}, with_speaker=True)
        self.assertGreater(len(cues), 1)
        self.assertTrue(cues[0]["text"].startswith("[Анна] Сегодня"))
        self.assertEqual(cues[0]["start"], 10.0)
        # The second piece starts exactly when its first word is spoken
        first_word = cues[1]["text"].split()[0]
        w = next(w for w in self.seg["words"] if w["word"].strip() == first_word)
        self.assertEqual(cues[1]["start"], w["start"])
        self.assertTrue(all(len(line) <= 50 for c in cues for line in c["text"].split("\n")))

    def test_edited_text_falls_back_to_proportional_split(self):
        seg = {**self.seg, "text": "Совсем другой текст после правки."}
        cues = build_cues([seg], None, variant="original")
        self.assertEqual(cues[0]["start"], 10.0)
        self.assertEqual(cues[-1]["end"], 30.0)

    def test_vtt_and_srt(self):
        cues = [{"start": 1.5, "end": 3.25, "text": "Привет"}]
        vtt = cues_to_vtt(cues)
        self.assertTrue(vtt.startswith("WEBVTT\n\n00:00:01.500 --> 00:00:03.250\nПривет"))
        self.assertIn("1\n00:00:01,500 --> 00:00:03,250\nПривет", cues_to_srt(cues))


class HotkeyTest(unittest.TestCase):
    def test_parse(self):
        from app.hotkey import MOD_ALT, MOD_CONTROL, MOD_SHIFT, parse_hotkey

        self.assertEqual(parse_hotkey("Ctrl+Alt+R"), (MOD_CONTROL | MOD_ALT, ord("R")))
        self.assertEqual(parse_hotkey("ctrl + shift + f9"), (MOD_CONTROL | MOD_SHIFT, 0x78))
        self.assertEqual(parse_hotkey("Alt+5"), (MOD_ALT, ord("5")))
        for bad in ("", "R", "Shift+R", "Ctrl+Alt", "Ctrl+Я", "Hyper+R", "Ctrl+F25"):
            self.assertIsNone(parse_hotkey(bad), bad)


if __name__ == "__main__":
    unittest.main()
