import tests  # noqa: F401 - keeps the real user data out of reach (see tests/__init__.py)
import unittest
from unittest import mock

import numpy as np

from app import dictation, llm
from app.database import init_db, update_settings


class _Target:
    def __init__(self, answer=None, error=None, wait=0.0):
        self.answer, self.error, self.wait, self.asked = answer, error, wait, []

    def chat(self, system, user):
        import time

        self.asked.append((system, user))
        time.sleep(self.wait)
        if self.error:
            raise self.error
        return self.answer


class DictationTranslateTest(unittest.TestCase):
    def setUp(self):
        init_db()
        update_settings({"dictation_translate_cloud": "false", "dictation_translate_to": "en"})

    def test_text_is_translated_by_the_local_model_unless_the_cloud_is_allowed(self):
        tgt = _Target(answer="<think>x</think> «Hello, I will send the report tomorrow.»")
        with mock.patch.object(llm, "target", return_value=tgt) as pick:
            out, note = dictation.translate("Привет, завтра пришлю отчёт.", "en")
        self.assertEqual((out, note), ("Hello, I will send the report tomorrow.", ""))
        self.assertEqual(pick.call_args.kwargs, {"local_only": True})
        self.assertIn("English", tgt.asked[0][0])
        update_settings({"dictation_translate_cloud": "true"})
        with mock.patch.object(llm, "target", return_value=tgt) as pick:
            dictation.translate("Привет, завтра пришлю отчёт.", "en")
        self.assertEqual(pick.call_args.kwargs, {"local_only": False})

    def test_text_already_in_the_language_is_typed_as_it_is(self):
        with mock.patch.object(llm, "target") as pick:
            self.assertEqual(dictation.translate("I will send you the report", "en"), ("I will send you the report", ""))
        pick.assert_not_called()

    def test_failure_or_silence_types_the_original(self):
        src = "Привет, завтра пришлю отчёт."
        for tgt, why in ((_Target(error=RuntimeError("Ollama не отвечает")), "Ollama не отвечает"),
                         (_Target(answer="  "), "пустой")):
            with mock.patch.object(llm, "target", return_value=tgt):
                out, note = dictation.translate(src, "en")
            self.assertEqual(out, src)
            self.assertIn(why, note)
        with mock.patch.object(llm, "target", side_effect=llm.LlmError("Ollama не запущена")):
            self.assertIn("Ollama не запущена", dictation.translate(src, "en")[1])
        with mock.patch.object(dictation, "TRANSLATE_TIMEOUT_SEC", 0.1), \
                mock.patch.object(llm, "target", return_value=_Target(answer="late", wait=1.0)):
            out, note = dictation.translate(src, "en")
        self.assertEqual(out, src)
        self.assertIn("не ответила", note)

    def test_hotkey_with_translation_types_the_translation(self):
        typed, notes = [], []
        with mock.patch.object(dictation, "_record"), \
                mock.patch.object(dictation, "recognize", return_value="Привет, завтра пришлю отчёт."), \
                mock.patch.object(dictation, "type_text", side_effect=typed.append), \
                mock.patch.object(llm, "target", return_value=_Target(answer="Hi, I will send the report tomorrow.")):
            self.assertEqual(dictation.toggle(translate=True), "started")
            dictation._chunks.append(np.zeros(dictation.SR, dtype=np.float32))
            dictation._finish(notify=lambda title, text, warning=False: notes.append((title, warning)))
        self.assertEqual(typed, ["Hi, I will send the report tomorrow."])
        self.assertEqual(notes, [("Диктовка с переводом", False)])

        # the plain hotkey still types what was said
        typed.clear()
        with mock.patch.object(dictation, "_record"), \
                mock.patch.object(dictation, "recognize", return_value="Привет"), \
                mock.patch.object(dictation, "type_text", side_effect=typed.append), \
                mock.patch.object(llm, "target") as pick:
            dictation.toggle()
            dictation._chunks.append(np.zeros(dictation.SR, dtype=np.float32))
            dictation._finish()
        self.assertEqual(typed, ["Привет"])
        pick.assert_not_called()

    def test_target_language_is_checked_when_saved(self):
        from app.settings_schema import clean

        self.assertEqual(clean("dictation_translate_to", "de"), "de")
        with self.assertRaises(ValueError):
            clean("dictation_translate_to", "klingon")


if __name__ == "__main__":
    unittest.main()


class OllamaModelChoiceTest(unittest.TestCase):
    """The embedding model of search by meaning (bge-m3) is never picked to write text: Ollama
    answers a chat with it by 400. And an Ollama error reaches the user in Ollama's words."""

    def setUp(self):
        init_db()
        update_settings({"ollama_model": "qwen2.5:7b", "semantic_model": "bge-m3"})

    def test_a_missing_model_falls_back_to_a_chat_model(self):
        from app import ollama_client

        status = {"running": True, "models": ["bge-m3:latest", "nomic-embed-text:latest", "qwen3:8b"]}
        status["chat_models"] = ollama_client.chat_models(status["models"])
        with mock.patch("app.ollama_client.ensure_ollama_running"), \
                mock.patch("app.ollama_client.get_ollama_status", return_value=status):
            self.assertEqual(llm.target(None, local_only=True).model, "qwen3:8b")
        with mock.patch("app.ollama_client.ensure_ollama_running"), \
                mock.patch("app.ollama_client.get_ollama_status",
                           return_value={"running": True, "models": ["bge-m3:latest"], "chat_models": []}):
            with self.assertRaises(llm.LlmError):  # only an embedding model: "no model downloaded"
                llm.target(None, local_only=True)

    def test_ollama_error_text_is_kept(self):
        import httpx

        from app import ollama_client

        resp = httpx.Response(400, json={"error": "\"bge-m3:latest\" does not support chat"},
                              request=httpx.Request("POST", "http://127.0.0.1:11434/api/chat"))
        with self.assertRaises(RuntimeError) as e:
            ollama_client.raise_for_ollama(resp)
        self.assertIn("does not support chat", str(e.exception))
        self.assertIn("400", str(e.exception))
