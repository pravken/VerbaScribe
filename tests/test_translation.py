import unittest

from app import database
from app.translation import (
    build_cues,
    cues_to_srt,
    meeting_for_export,
    merge_translated_items,
    resolve_translation,
    segments_needing_translation,
    split_text_for_cues,
    wrap_subtitle_line,
)


def _segs(texts, step=10.0):
    return [
        {"id": i + 1, "start": i * step, "end": i * step + 5.0, "speaker": "SPEAKER_00", "text": t, "words": []}
        for i, t in enumerate(texts)
    ]


def _translated(segs, prefix="EN "):
    return merge_translated_items(
        None, segs, {s["id"]: prefix + s["text"] for s in segs}, model="m", source_lang="ru"
    )


class StalenessTest(unittest.TestCase):
    def test_fresh_translation_matches_every_segment(self):
        segs = _segs(["один", "два", "три"])
        r = resolve_translation(segs, _translated(segs))
        self.assertEqual((r["translated"], r["stale"], r["missing"]), (3, 0, 0))
        self.assertEqual(r["segments"]["2"], {"text": "EN два", "stale": False})

    def test_edited_segment_becomes_stale_and_keeps_old_text(self):
        segs = _segs(["один", "два", "три"])
        entry = _translated(segs)
        segs[1]["text"] = "два с половиной"
        r = resolve_translation(segs, entry)
        self.assertEqual((r["translated"], r["stale"], r["missing"]), (2, 1, 0))
        self.assertEqual(r["segments"]["2"], {"text": "EN два", "stale": True})
        self.assertEqual([s["id"] for s in segments_needing_translation(segs, entry)], [2])
        # whitespace-only edits do not invalidate the translation
        segs[0]["text"] = "  один "
        self.assertFalse(resolve_translation(segs, entry)["segments"]["1"]["stale"])

    def test_merge_renumbers_but_untouched_segments_stay_fresh(self):
        segs = _segs(["один", "два", "три", "четыре"])
        entry = _translated(segs)
        # merge segment 2 into 1 (what merge_segment_with_previous does): ids are renumbered
        merged = [dict(segs[0], text="один два", end=segs[1]["end"]), dict(segs[2], id=2), dict(segs[3], id=3)]
        r = resolve_translation(merged, entry)
        self.assertEqual(r["segments"]["1"], {"text": "EN один", "stale": True})
        self.assertEqual(r["segments"]["2"], {"text": "EN три", "stale": False})
        self.assertEqual(r["segments"]["3"], {"text": "EN четыре", "stale": False})
        # updating only the stale one re-keys the others to their new ids
        entry2 = merge_translated_items(entry, merged, {1: "EN один два"}, model="m", source_lang="ru")
        r2 = resolve_translation(merged, entry2)
        self.assertEqual((r2["translated"], r2["stale"], r2["missing"]), (3, 0, 0))
        self.assertEqual(sorted(entry2["items"]), ["1", "2", "3"])

    def test_new_segment_is_missing(self):
        segs = _segs(["один", "два"])
        entry = _translated(segs)
        segs.append({"id": 3, "start": 40.0, "end": 41.0, "speaker": "SPEAKER_00", "text": "новый"})
        r = resolve_translation(segs, entry)
        self.assertEqual(r["missing"], 1)
        self.assertNotIn("3", r["segments"])

    def test_partial_batches_accumulate(self):
        segs = _segs(["a", "b", "c"])
        entry = merge_translated_items(None, segs, {1: "A"}, model="m", source_lang="en")
        entry = merge_translated_items(entry, segs, {2: "B", 3: "C"}, model="m", source_lang="en")
        r = resolve_translation(segs, entry)
        self.assertEqual([r["segments"][k]["text"] for k in "123"], ["A", "B", "C"])


class SubtitleTest(unittest.TestCase):
    def test_srt_format_and_timing_kept(self):
        segs = _segs(["привет", "пока"])
        segs[1]["start"], segs[1]["end"] = 3661.5, 3662.25
        r = resolve_translation(segs, _translated(segs))
        srt = cues_to_srt(build_cues(segs, r, variant="translated"))
        self.assertEqual(
            srt,
            "1\n00:00:00,000 --> 00:00:05,000\nEN привет\n\n2\n01:01:01,500 --> 01:01:02,250\nEN пока\n",
        )

    def test_bilingual_and_fallback_to_original(self):
        segs = _segs(["привет", "без перевода"])
        entry = merge_translated_items(None, segs, {1: "hello"}, model="m", source_lang="ru")
        r = resolve_translation(segs, entry)
        bi = build_cues(segs, r, variant="bilingual")
        self.assertEqual(bi[0]["text"], "привет\nhello")
        self.assertEqual(bi[1]["text"], "без перевода")
        tr = build_cues(segs, r, variant="translated")
        self.assertEqual([c["text"] for c in tr], ["hello", "без перевода"])

    def test_long_segment_split_keeps_segment_bounds(self):
        long_text = "First sentence is here. " * 8
        segs = [{"id": 1, "start": 10.0, "end": 30.0, "speaker": "S", "text": "x"}]
        entry = merge_translated_items(None, segs, {1: long_text}, model="m", source_lang="ru")
        cues = build_cues(segs, resolve_translation(segs, entry), variant="translated")
        self.assertGreater(len(cues), 1)
        self.assertEqual(cues[0]["start"], 10.0)
        self.assertEqual(cues[-1]["end"], 30.0)
        for a, b in zip(cues, cues[1:]):
            self.assertAlmostEqual(a["end"], b["start"], places=3)
        for c in cues:
            self.assertTrue(all(len(line) <= 42 for line in c["text"].split("\n")), c["text"])

    def test_long_bilingual_segment_is_split_in_step(self):
        segs = [{"id": 1, "start": 0.0, "end": 30.0, "speaker": "S", "text": "слово " * 40}]
        entry = merge_translated_items(None, segs, {1: "word " * 40}, model="m", source_lang="ru")
        cues = build_cues(segs, resolve_translation(segs, entry), variant="bilingual")
        self.assertGreater(len(cues), 2)
        self.assertEqual((cues[0]["start"], cues[-1]["end"]), (0.0, 30.0))
        for c in cues:
            ru, en = c["text"].split("\n")[0], c["text"].split("\n")[-1]
            self.assertTrue(ru.startswith("слово") and en.startswith("word"), c["text"])
        self.assertEqual(" ".join(c["text"].replace("\n", " ") for c in cues).split().count("word"), 40)

    def test_split_and_wrap_helpers(self):
        self.assertEqual(split_text_for_cues("short"), ["short"])
        pieces = split_text_for_cues("word " * 60, max_chars=50)
        self.assertTrue(all(len(p) <= 50 for p in pieces))
        self.assertEqual(" ".join(pieces), ("word " * 60).strip())
        self.assertEqual(wrap_subtitle_line("aaa bbb ccc", width=7), "aaa bbb\nccc")


class ExportPrepTest(unittest.TestCase):
    def test_translated_and_bilingual_copies(self):
        segs = _segs(["привет", "пока"])
        m = {"segments": segs, "translations": {"en": resolve_translation(segs, _translated(segs))}}
        t = meeting_for_export(m, "en", "translated")
        self.assertEqual([s["text"] for s in t["segments"]], ["EN привет", "EN пока"])
        self.assertEqual(segs[0]["text"], "привет")  # the original is not modified
        b = meeting_for_export(m, "en", "bilingual")
        self.assertEqual(b["segments"][0]["text"], "привет")
        self.assertEqual(b["segments"][0]["secondary_text"], "EN привет")
        self.assertIn("EN", t["translation_note"])
        with self.assertRaises(ValueError):
            meeting_for_export(m, "de", "translated")


try:
    from app import llm_translate
except ImportError:  # httpx not installed
    llm_translate = None


@unittest.skipIf(llm_translate is None, "httpx not installed")
class BatchTranslationTest(unittest.TestCase):
    def test_numbered_answer_parsing(self):
        answer = "Here you go:\n1. Hello\n3) Third `x`\n2: Second\n1. duplicate\n9. out of range"
        self.assertEqual(llm_translate.parse_numbered_lines(answer, 3), ["Hello", "Second", "Third `x"])

    def test_skipped_lines_are_retried_one_by_one(self):
        calls = {"single": []}
        orig_batch, orig_single = llm_translate.translate_batch, llm_translate.translate_single
        llm_translate.translate_batch = lambda base, model, texts, *a, **k: [t.upper() if i != 1 else None for i, t in enumerate(texts)]
        llm_translate.translate_single = lambda base, model, text, *a, **k: calls["single"].append(text) or f"single:{text}"
        progress = []
        try:
            out = llm_translate.translate_texts(
                ["a", "b", "c", "d"], "ru", "en", base_url="u", model="m", style="text",
                check_cancel=lambda: None, on_batch=lambda done, got: progress.append((done, sorted(got))), batch_size=3,
            )
        finally:
            llm_translate.translate_batch, llm_translate.translate_single = orig_batch, orig_single
        self.assertEqual(out, ["A", "single:b", "C", "D"])
        self.assertEqual(calls["single"], ["b"])
        self.assertEqual(progress, [(3, [0, 1, 2]), (4, [3])])


class TranslationStorageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        database.init_db()

    def test_saved_translation_is_resolved_in_meeting_and_survives_edits(self):
        m = database.create_meeting(title="tr", filename="x.wav", media_path="x.wav")
        segs = _segs(["один", "два"])
        database.save_meeting_results(m["id"], segments=segs)
        database.save_meeting_translation(m["id"], "en", _translated(segs))
        database.save_meeting_translation(m["id"], "de", _translated(segs, "DE "))
        got = database.get_meeting(m["id"])
        self.assertEqual(sorted(got["translations"]), ["de", "en"])
        self.assertEqual(got["translations"]["en"]["segments"]["1"]["text"], "EN один")
        self.assertNotIn("translations_json", got)

        merged = database.merge_segment_with_previous(m["id"], 2)
        self.assertEqual(merged["translations"]["en"]["stale"], 1)
        self.assertEqual(len(merged["segments"]), 1)

        database.save_meeting_translation(m["id"], "de", None)
        self.assertEqual(sorted(database.get_meeting(m["id"])["translations"]), ["en"])
        self.assertEqual(sorted(database.get_meeting_translations_raw(m["id"])), ["en"])


if __name__ == "__main__":
    unittest.main()
