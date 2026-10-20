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
