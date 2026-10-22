import tests  # noqa: F401 - keeps the real user data out of reach (see tests/__init__.py)
import unittest

from app.text_utils import (
    clean_filler_words_in_segments,
    is_low_quality_segment,
    realign_words,
)


def _words(*items):
    return [{"start": s, "end": e, "word": w, "prob": 0.9} for w, s, e in items]


class CleanFillersTest(unittest.TestCase):
    def test_removes_hesitations_and_keeps_text_in_sync_with_words(self):
        seg = {
            "id": 1,
            "start": 0.0,
            "end": 3.0,
            "text": "Э-э, мы начинаем ммм сейчас.",
            "words": _words((" Э-э,", 0.0, 0.4), (" мы", 0.5, 0.7), (" начинаем", 0.8, 1.4), (" ммм", 1.5, 1.9), (" сейчас.", 2.0, 2.6)),
        }
        cleaned, removed = clean_filler_words_in_segments([seg])
        self.assertEqual(removed, 2)
        self.assertEqual(cleaned[0]["text"], "Мы начинаем сейчас.")
        self.assertEqual("".join(w["word"] for w in cleaned[0]["words"]).strip(), cleaned[0]["text"])

    def test_keeps_intentional_repeats_and_meaningful_phrases(self):
        text = " Да да, как бы то ни было, очень очень важно."
        seg = {
            "id": 1,
            "start": 0.0,
            "end": 4.0,
            "text": text.strip(),
            "words": _words(*[(f" {w}", i * 0.3, i * 0.3 + 0.25) for i, w in enumerate(text.split())]),
        }
        cleaned, removed = clean_filler_words_in_segments([seg])
        self.assertEqual(removed, 0)
        self.assertEqual(cleaned[0]["text"], text.strip())

    def test_removes_stutter_and_moves_sentence_end(self):
        seg = {
            "id": 1,
            "start": 0.0,
            "end": 2.0,
            "text": "Я я думаю так э.",
            "words": _words((" Я", 0.0, 0.2), (" я", 0.25, 0.4), (" думаю", 0.5, 0.9), (" так", 1.0, 1.2), (" э.", 1.3, 1.6)),
        }
        cleaned, removed = clean_filler_words_in_segments([seg])
        self.assertEqual(removed, 2)
        self.assertEqual(cleaned[0]["text"], "Я думаю так.")

    def test_drops_segments_that_were_only_fillers_and_renumbers(self):
        segs = [
            {"id": 1, "start": 0.0, "end": 0.5, "text": "Ммм", "words": _words((" Ммм", 0.0, 0.5))},
            {"id": 2, "start": 1.0, "end": 2.0, "text": "Привет", "words": _words((" Привет", 1.0, 2.0))},
        ]
        cleaned, _ = clean_filler_words_in_segments(segs)
        self.assertEqual(len(cleaned), 1)
        self.assertEqual(cleaned[0]["id"], 1)

    def test_text_only_segments(self):
        cleaned, removed = clean_filler_words_in_segments([{"id": 1, "start": 0, "end": 1, "text": "Эээ, ну типа привет"}])
        self.assertEqual(removed, 2)
        self.assertEqual(cleaned[0]["text"], "Привет")


class RealignWordsTest(unittest.TestCase):
    def test_unchanged_words_keep_timestamps(self):
        old = _words((" Привет", 0.0, 0.5), (" мир", 0.6, 1.0))
        new = realign_words(old, "Привет, мир!", 0.0, 1.0)
        self.assertEqual([(w["start"], w["end"]) for w in new], [(0.0, 0.5), (0.6, 1.0)])
        self.assertEqual("".join(w["word"] for w in new).strip(), "Привет, мир!")

    def test_replaced_and_inserted_words_get_interpolated_times(self):
        old = _words((" Привет", 0.0, 0.5), (" мирр", 0.6, 1.0), (" пока", 2.0, 2.5))
        new = realign_words(old, "Привет мир и вселенная пока", 0.0, 2.5)
        self.assertEqual(len(new), 5)
        starts = [w["start"] for w in new]
        self.assertEqual(starts, sorted(starts))
        self.assertEqual(new[0]["start"], 0.0)
        self.assertEqual(new[-1]["end"], 2.5)

    def test_empty_text(self):
        self.assertEqual(realign_words(_words((" a", 0, 1)), "   ", 0, 1), [])


class QualityFilterTest(unittest.TestCase):
    def test_silence_and_repetition_loops_are_rejected(self):
        self.assertTrue(is_low_quality_segment(0.9, -1.5, 1.2))
        self.assertTrue(is_low_quality_segment(0.1, -0.2, 3.0))
        self.assertFalse(is_low_quality_segment(0.9, -0.3, 1.2))
        self.assertFalse(is_low_quality_segment(None, None, None))


class SharedHelpersTest(unittest.TestCase):
    def test_srt_time_rounds_to_the_millisecond(self):
        from app.audio_utils import format_timestamp, srt_time

        self.assertEqual(srt_time(1.9996), "00:00:02,000")
        self.assertEqual(srt_time(3725.5), "01:02:05,500")
        self.assertEqual(format_timestamp(1.9996, srt=True), "00:00:02,000")
        self.assertEqual(format_timestamp(65), "01:05")

    def test_folder_size_counts_linked_files_once(self):
        import os
        import tempfile
        from pathlib import Path

        from app.paths import dir_size

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "blobs").mkdir()
            (root / "snapshots").mkdir()
            (root / "blobs" / "abc").write_bytes(b"x" * 1000)
            try:
                os.symlink(root / "blobs" / "abc", root / "snapshots" / "model.bin")
            except (OSError, NotImplementedError):
                self.skipTest("symbolic links are not allowed here")
            self.assertEqual(dir_size(root), 1000)  # the cache: the blob once
            self.assertEqual(dir_size(root / "snapshots"), 1000)  # a snapshot: through its link
            self.assertEqual(dir_size(root / "missing"), 0)


if __name__ == "__main__":
    unittest.main()
