import tests  # noqa: F401 - keeps the real user data out of reach (see tests/__init__.py)
import unittest

from app.series import matching_meeting, people, title_key


def _m(id, title, when, names=()):
    return {"id": id, "title": title, "recorded_at": when,
            "speakers": {f"S{i}": {"name": n} for i, n in enumerate(names, 1)}}


class SeriesMatchTest(unittest.TestCase):
    def test_title_key_ignores_dates(self):
        self.assertEqual(title_key("Планёрка 12.05"), title_key("Планёрка 19.05"))

    def test_people_skip_default_names(self):
        self.assertEqual(people(_m(1, "", "", ["Анна", "Speaker 2"])) - {"speaker 2"}, {"анна"})

    def test_match_by_title_picks_latest_earlier(self):
        new = _m(3, "Планёрка команды 19.05", "2026-05-19")
        others = [_m(1, "Планёрка команды 05.05", "2026-05-05"), _m(2, "Планёрка команды 12.05", "2026-05-12"),
                  _m(4, "Планёрка команды 26.05", "2026-05-26"), _m(5, "Другое", "2026-05-18")]
        self.assertEqual(matching_meeting(new, others)["id"], 2)

    def test_no_match(self):
        self.assertIsNone(matching_meeting(_m(2, "Интервью", "2026-05-02"), [_m(1, "Созвон", "2026-05-01")]))


if __name__ == "__main__":
    unittest.main()


class OnnxEnsurePackageTest(unittest.TestCase):
    def test_installs_when_missing(self):
        from unittest import mock
        from app import asr_onnx

        with mock.patch.object(asr_onnx, "_run", return_value=mock.Mock(returncode=0)) as run, \
             mock.patch("shutil.which", return_value="uv"), \
             mock.patch("importlib.import_module", side_effect=ImportError):
            asr_onnx.ensure_package()
        cmd = run.call_args[0][0]
        self.assertEqual(cmd[:3], ["uv", "pip", "install"])
        self.assertIn(asr_onnx.ONNX_ASR_PIN, cmd)

    def test_fetch_downloads_into_existing_folder(self):
        import tempfile
        from pathlib import Path
        from unittest import mock
        from app import asr_onnx
        from onnx_asr.resolver import Resolver

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "gigaam-v3"
            d.mkdir()  # an earlier failed attempt left an empty folder
            calls = []

            def fake_download(self, quantization, *, local_files_only):
                calls.append(local_files_only)
                Path(self.local_dir).mkdir(parents=True, exist_ok=True)
                for f in self.model_type._get_model_files(quantization).values():
                    p = Path(self.local_dir, f.removeprefix("**/").replace("?", "_"))
                    p.write_text("x")
                return self.local_dir

            with mock.patch.object(asr_onnx, "model_dir", return_value=d), \
                 mock.patch.object(asr_onnx, "_vad_dir", return_value=Path(tmp) / "vad"), \
                 mock.patch.object(Resolver, "_download_model", fake_download):
                asr_onnx.fetch("gigaam-v3")
            self.assertIn(False, calls)
            self.assertTrue(list(d.glob("*.onnx")))


class LiveOnnxChoiceTest(unittest.TestCase):
    def _pick(self, setting, lang, installed):
        from unittest import mock
        from app import asr_onnx, live

        with mock.patch.object(asr_onnx, "is_installed", side_effect=lambda m: m in installed):
            return live.caption_onnx_model({"live_whisper_model": setting}, lang)

    def test_auto(self):
        both = {"gigaam-v3", "parakeet-v3"}
        self.assertEqual(self._pick("auto", "ru", both), "gigaam-v3")
        self.assertEqual(self._pick("auto", "ru", both | {"gigaam-v3"}), "gigaam-v3")
        self.assertEqual(self._pick("auto", "en", both), "parakeet-v3")
        self.assertEqual(self._pick("auto", None, both), "parakeet-v3")
        self.assertIsNone(self._pick("auto", None, {"gigaam-v3"}))
        self.assertIsNone(self._pick("auto", "ja", both))
        self.assertIsNone(self._pick("auto", "ru", set()))

    def test_explicit(self):
        self.assertEqual(self._pick("gigaam-v3", "ru", {"gigaam-v3"}), "gigaam-v3")
        self.assertIsNone(self._pick("gigaam-v3", "en", {"gigaam-v3"}))
        self.assertEqual(self._pick("gigaam-v3", None, {"gigaam-v3"}), "gigaam-v3")
        self.assertIsNone(self._pick("small", "ru", {"gigaam-v3"}))


class LiveOnnxWindowTest(unittest.TestCase):
    def test_window_goes_to_onnx(self):
        from unittest import mock
        import numpy as np
        from app import asr_onnx, live

        with mock.patch.object(live, "caption_onnx_model", return_value="gigaam-v3"):
            s = live.LiveSession("ru", None)
        s.audio = np.zeros(live.SR * 6, dtype=np.float32)
        with mock.patch.object(asr_onnx, "recognize_array", return_value=[(0.2, 1.5, "Привет всем"), (4.8, 5.9, "дальше")]):
            s._transcribe_window()
        self.assertEqual([l["text"] for l in s.lines], ["Привет всем"])
        self.assertEqual(s.partial, "дальше")


class LiveDraftTranslationTest(unittest.TestCase):
    def test_draft_sent_to_translator_only_when_on(self):
        from unittest import mock
        import numpy as np
        from app import asr_onnx, live

        for on, expected in ((True, "дальше"), (False, "")):
            with mock.patch.object(live, "caption_onnx_model", return_value="gigaam-v3"):
                s = live.LiveSession("ru", None)
            s.draft = on
            s.translator = mock.Mock()
            s.audio = np.zeros(live.SR * 6, dtype=np.float32)
            with mock.patch.object(asr_onnx, "recognize_array", return_value=[(0.2, 1.5, "Привет"), (4.8, 5.9, "дальше")]):
                s._transcribe_window()
            s.translator.set_draft.assert_called_with(expected)


class RemovedModelTest(unittest.TestCase):
    def test_settings_move_to_v3(self):
        from app import asr_onnx
        from app.database import get_all_settings, init_db, update_settings

        init_db()
        update_settings({"whisper_model": "gigaam-v2", "live_whisper_model": "medium"})
        from app.model_manager import WHISPER_CATALOG, remove_retired_models
        remove_retired_models()
        s = get_all_settings()
        self.assertEqual((s["whisper_model"], s["live_whisper_model"]), ("gigaam-v3", "large-v3-turbo"))
        self.assertEqual([m["id"] for m in WHISPER_CATALOG], ["large-v3-turbo", "large-v3"])
        update_settings({"whisper_model": "large-v3-turbo", "live_whisper_model": "auto"})
        self.assertNotIn("gigaam-v2", asr_onnx.ONNX_MODELS)


class SentenceSplitTest(unittest.TestCase):
    def test_words(self):
        from app.asr_onnx import split_sentences_words
        ws = [{"word": " " + w, "start": i, "end": i + 0.5} for i, w in enumerate("Привет. Как дела? Хорошо".split())]
        out = split_sentences_words({"start": 0, "end": 3, "text": "", "words": ws})
        self.assertEqual([o["text"] for o in out], ["Привет.", "Как дела?", "Хорошо"])
        self.assertEqual(out[1]["start"], 1)

    def test_text(self):
        from app.asr_onnx import split_sentences_text
        out = split_sentences_text(0, 10, "Раз два. Три четыре!")
        self.assertEqual([o[2] for o in out], ["Раз два.", "Три четыре!"])
        self.assertEqual(out[-1][1], 10)


class LiveSentenceLinesTest(unittest.TestCase):
    def test_lines_per_sentence(self):
        from unittest import mock
        import numpy as np
        from app import asr_onnx, live

        with mock.patch.object(live, "caption_onnx_model", return_value="gigaam-v3"):
            s = live.LiveSession("ru", None)
        s.audio = np.zeros(live.SR * 8, dtype=np.float32)
        with mock.patch.object(asr_onnx, "recognize_array", return_value=[(0.2, 4.0, "Первое. Второе предложение? Третье")]):
            s._transcribe_window()
        self.assertEqual([l["text"] for l in s.lines], ["Первое.", "Второе предложение?", "Третье"])

    def test_sentence_final_once_next_starts(self):
        from unittest import mock
        import numpy as np
        from app import asr_onnx, live

        with mock.patch.object(live, "caption_onnx_model", return_value="gigaam-v3"):
            s = live.LiveSession("ru", None)
        s.audio = np.zeros(live.SR * 5, dtype=np.float32)
        # Both end within the last 1.6 s, but the first is followed by another sentence
        with mock.patch.object(asr_onnx, "recognize_array", return_value=[(0.2, 3.6, "Первое."), (3.6, 4.9, "Второе")]):
            s._transcribe_window()
        self.assertEqual([l["text"] for l in s.lines], ["Первое."])
        self.assertEqual(s.partial, "Второе")
        self.assertAlmostEqual(s.offset, 3.6, places=2)

    def test_recognize_array_cuts_at_words(self):
        from types import SimpleNamespace
        from unittest import mock
        from app import asr_onnx

        res = SimpleNamespace(text="Да. Хорошо", start=1.0, end=3.0,
                              tokens=["▁Да", ".", "▁Хо", "рошо"], timestamps=[0.0, 0.3, 1.2, 1.5])
        fake = mock.Mock()
        fake.recognize.return_value = [res]
        with mock.patch.object(asr_onnx, "_load", return_value=fake):
            out = asr_onnx.recognize_array("gigaam-v3", None)
        self.assertEqual([o[2] for o in out], ["Да.", "Хорошо"])
        self.assertEqual(out[1][0], 2.2)

    def test_state_names_models(self):
        from unittest import mock
        from app import live

        with mock.patch.object(live, "caption_onnx_model", return_value="gigaam-v3"):
            s = live.LiveSession("ru", None)
        st = s.state()
        self.assertEqual(st["caption_model"], "GigaAM v3")
        self.assertEqual(st["translate_model"], "")

    def test_pick_models_by_hand(self):
        from unittest import mock
        from app import asr_onnx, live
        from app.database import get_all_settings, init_db, update_settings

        init_db()
        with mock.patch.object(live, "caption_onnx_model", return_value=None):
            s = live.LiveSession("ru", None)
        with mock.patch.object(asr_onnx, "is_installed", return_value=True), \
             mock.patch.object(asr_onnx, "preload"):
            s.set_caption_model("parakeet-v3")
        self.assertEqual(s.onnx_model, "parakeet-v3")
        self.assertEqual(get_all_settings()["live_whisper_model"], "parakeet-v3")
        s.translator = mock.Mock()
        s.set_translate_model("qwen3:4b")
        s.translator.set_model.assert_called_with("qwen3:4b")
        self.assertEqual(s.state()["translate_choice"], "qwen3:4b")
        update_settings({"live_whisper_model": "auto"})


class TitleCleanTest(unittest.TestCase):
    def test_cjk_removed(self):
        from app.ollama_client import clean_title
        self.assertEqual(clean_title("Планирование 发布 релиза.", "ru"), "Планирование релиза")
        self.assertEqual(clean_title("<think>x</think>«Итоги квартала»", "ru"), "Итоги квартала")
        self.assertEqual(clean_title("会议总结", "zh"), "会议总结")


class SpaceTokenTest(unittest.TestCase):
    def test_separate_space_tokens(self):
        from app.asr_onnx import words_from_tokens
        toks = ["в", "▁", "той", "▁", "ста", "дии", ",", "▁", "8", ":", "00", " "]
        words = words_from_tokens(toks, [i * 0.1 for i in range(len(toks))], 0, 2)
        self.assertEqual("".join(w["word"] for w in words).strip(), "в той стадии, 8:00")

    def test_prefixed_tokens_unchanged(self):
        from app.asr_onnx import words_from_tokens
        words = words_from_tokens(["▁При", "вет", ".", "▁Да"], [0, 0.1, 0.2, 0.3], 0, 1)
        self.assertEqual([w["word"] for w in words], [" Привет.", " Да"])

    def test_words_follow_model_text(self):
        from app.asr_onnx import words_from_text
        toks = ["в", "той", "ста", "дии", ",", "8", ":", "00"]  # no space tokens at all
        words = words_from_text("в той стадии, 8:00", toks, [i * 0.1 for i in range(len(toks))], 1.0, 2.0)
        self.assertEqual([w["word"] for w in words], [" в", " той", " стадии,", " 8:00"])
        self.assertEqual(words[2]["start"], 1.2)


class DateFolderTest(unittest.TestCase):
    def test_names_and_parent(self):
        import tempfile
        from datetime import datetime
        from pathlib import Path
        from app.naming import date_folder, in_date_folder, without_date_folders

        dt = datetime(2026, 10, 21, 9, 30)
        self.assertEqual(date_folder(dt, "day"), "2026-10-21")
        self.assertEqual(date_folder(dt, "month"), "2026-10")
        self.assertEqual(date_folder(dt, "year_month"), "2026/2026-10")
        self.assertEqual(date_folder(dt, "off"), "")
        self.assertEqual(without_date_folders(Path("/x/Exports/2026/2026-10")), Path("/x/Exports"))
        with tempfile.TemporaryDirectory() as tmp:
            from unittest import mock
            with mock.patch("app.naming.get_all_settings", return_value={"date_folders": "day"}):
                # The remembered folder is yesterday's date folder: today's goes next to it
                d = in_date_folder(Path(tmp) / "2026-10-20", dt)
            self.assertEqual(d, Path(tmp) / "2026-10-21")
            self.assertTrue(d.is_dir())


class DeleteWithFilesTest(unittest.TestCase):
    def test_exports_and_note_deleted_on_request(self):
        import tempfile
        from pathlib import Path
        from app.database import add_export_path, create_meeting, delete_meeting, get_meeting, init_db, update_meeting_fields

        init_db()
        with tempfile.TemporaryDirectory() as tmp:
            files = [Path(tmp) / n for n in ("a.md", "b.srt", "note.md", "src.m4a")]
            for f in files:
                f.write_text("x")
            m = create_meeting("Del", "src.m4a", str(files[3]), source="local")
            add_export_path(m["id"], str(files[0]))
            add_export_path(m["id"], str(files[1]))
            update_meeting_fields(m["id"], obsidian_path=str(files[2]))
            self.assertEqual(len(get_meeting(m["id"])["exports"]), 2)
            delete_meeting(m["id"], with_exports=True)
            self.assertFalse(files[0].exists() or files[1].exists())
            self.assertTrue(files[2].exists() and files[3].exists())  # not asked for


class RecordingSalvageTest(unittest.TestCase):
    def test_unfinished_parts_become_recordings(self):
        from unittest import mock
        from app import main
        from app.database import init_db, list_meetings

        init_db()
        rec = main._recordings_dir()
        rec.mkdir(parents=True, exist_ok=True)
        part = rec / ".page_abcdef123456.webm.part"
        part.write_bytes(b"x" * 10000)
        empty = rec / ".page_000000000000.webm.part"
        empty.write_bytes(b"")
        before = len(list_meetings())
        with mock.patch.object(main, "enqueue_meeting_job"):
            self.assertEqual(main.recover_orphan_recordings(), 1)
        self.assertFalse(part.exists() or empty.exists())
        after = list_meetings()
        self.assertEqual(len(after), before + 1)
        self.assertTrue(after[0]["filename"].endswith(".webm"))


class WhisperDeleteTest(unittest.TestCase):
    def test_exact_model_only(self):
        from unittest import mock
        import tempfile
        from pathlib import Path
        from app import model_manager as mm

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(mm, "MODELS_DIR", Path(tmp)):
            pro = Path(tmp) / "whisper" / "models--Systran--faster-whisper-large-v3" / "snapshots" / "a"
            turbo = Path(tmp) / "whisper" / "models--mobiuslabsgmbh--faster-whisper-large-v3-turbo" / "snapshots" / "a"
            for d in (pro, turbo):
                d.mkdir(parents=True)
                (d / "model.bin").write_bytes(b"x")
            self.assertTrue(mm.is_whisper_model_installed("large-v3"))
            mm.delete_whisper_model("large-v3")
            self.assertFalse(mm.is_whisper_model_installed("large-v3"))
            self.assertTrue(mm.is_whisper_model_installed("large-v3-turbo"))
            with self.assertRaises(ValueError):
                mm.delete_whisper_model("..")


class NotesAndTemplatesTest(unittest.TestCase):
    def test_templates_saved_and_used(self):
        from unittest import mock
        from app.database import init_db, save_summary_templates, summary_templates
        from app import ollama_client

        init_db()
        saved = save_summary_templates([{"name": "1:1", "prompt": "Обратная связь и шаги"}, {"name": "", "prompt": "x"}])
        self.assertEqual(len(saved), 1)
        self.assertEqual(summary_templates()[0]["name"], "1:1")
        llm = mock.Mock(is_cloud=False, max_chars=100000)
        llm.chat.return_value = "ok"
        segs = [{"start": 0, "end": 2, "text": "Привет", "speaker": "S1"}]
        with mock.patch("app.llm.target", return_value=llm):
            ollama_client.generate_meeting_summary(segs, {}, template_key="tpl:x", template_prompt="Обратная связь и шаги",
                                                   user_notes="[00:01] спросить про отпуск")
        user = llm.chat.call_args[0][1]
        self.assertIn("Обратная связь и шаги", user)
        self.assertIn("спросить про отпуск", user)
        with mock.patch("app.llm.target", return_value=llm), self.assertRaises(RuntimeError):
            ollama_client.generate_meeting_summary(segs, {}, template_key="notes")
        save_summary_templates([])
