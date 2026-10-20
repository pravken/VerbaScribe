import tests  # noqa: F401 - keeps the real user data out of reach (see tests/__init__.py)
import json
import unittest
from unittest import mock

import httpx

from app import llm, secret_store
from app.database import get_all_settings, init_db, update_settings

_RealClient = httpx.Client


class _Fake:
    """Answers HTTP requests of app/llm.py without the network and records them."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        status, body = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        return httpx.Response(status, json=body)

    def client(self, **kw):
        return _RealClient(transport=httpx.MockTransport(self.handler), **kw)


def _use(provider, model="m-1", **extra):
    update_settings({"llm_provider": "cloud", "llm_cloud_consent": "true", "llm_cloud_provider": provider,
                     f"llm_model_{provider}": model, **extra})
    secret_store.set_secret(llm.key_name(provider), "sk-test-1234567890")


class CloudLlmTest(unittest.TestCase):
    def setUp(self):
        init_db()

    def tearDown(self):
        update_settings({"llm_provider": "ollama"})
        for p in llm.PROVIDERS:
            secret_store.delete_secret(llm.key_name(p))

    def _chat(self, fake, system="sys", user="usr"):
        with mock.patch("app.llm.httpx.Client", side_effect=fake.client), mock.patch("app.llm.time.sleep"):
            return llm.target().chat(system, user)

    def test_anthropic_request_and_answer(self):
        _use("anthropic", "claude-sonnet-5-5")
        fake = _Fake((200, {"content": [{"type": "text", "text": "Ответ"}], "stop_reason": "end_turn"}))
        self.assertEqual(self._chat(fake), "Ответ")
        req = fake.requests[0]
        self.assertEqual(str(req.url), "https://api.anthropic.com/v1/messages")
        self.assertEqual(req.headers["x-api-key"], "sk-test-1234567890")
        self.assertEqual(req.headers["anthropic-version"], "2023-06-01")
        body = json.loads(req.content)
        self.assertEqual(body["system"], "sys")
        self.assertEqual(body["messages"], [{"role": "user", "content": "usr"}])
        self.assertEqual(body["model"], "claude-sonnet-5-5")

    def test_openai_and_compatible_services(self):
        _use("openai", "gpt-x")
        fake = _Fake((200, {"choices": [{"message": {"content": " Hi "}}]}))
        self.assertEqual(self._chat(fake), "Hi")
        self.assertEqual(fake.requests[0].headers["authorization"], "Bearer sk-test-1234567890")
        _use("openai_compat", "deepseek-chat", llm_base_url_openai_compat="https://api.deepseek.com/v1/")
        fake = _Fake((200, {"choices": [{"message": {"content": "ok"}}]}))
        self._chat(fake)
        self.assertEqual(str(fake.requests[0].url), "https://api.deepseek.com/v1/chat/completions")

    def test_gemini_request(self):
        _use("gemini", "gemini-2.5-flash")
        fake = _Fake((200, {"candidates": [{"content": {"parts": [{"text": "Привет"}]}}]}))
        self.assertEqual(self._chat(fake), "Привет")
        req = fake.requests[0]
        self.assertTrue(str(req.url).endswith("/models/gemini-2.5-flash:generateContent"))
        self.assertEqual(req.headers["x-goog-api-key"], "sk-test-1234567890")
        self.assertEqual(json.loads(req.content)["systemInstruction"]["parts"][0]["text"], "sys")

    def test_errors_are_readable_and_rate_limits_retried(self):
        _use("openai")
        with self.assertRaises(llm.LlmError) as ctx:
            self._chat(_Fake((401, {"error": {"message": "Incorrect API key provided"}})))
        self.assertIn("неверный API-ключ", str(ctx.exception))
        fake = _Fake((429, {"error": {"message": "slow down"}}), (200, {"choices": [{"message": {"content": "ok"}}]}))
        self.assertEqual(self._chat(fake), "ok")
        self.assertEqual(len(fake.requests), 2)

    def test_missing_key_and_availability(self):
        update_settings({"llm_provider": "cloud", "llm_cloud_provider": "anthropic"})
        self.assertFalse(llm.available())
        with self.assertRaises(llm.LlmError):
            llm.target()
        _use("anthropic")
        self.assertTrue(llm.available())

    def test_key_never_in_settings_and_hint_only(self):
        _use("anthropic")
        self.assertNotIn("sk-test-1234567890", json.dumps(get_all_settings()))
        self.assertNotIn("sk-test-1234567890", json.dumps(llm.status()))
        self.assertEqual(llm.status()["providers"]["anthropic"]["key"]["last4"], "…7890")

    def test_long_transcript_goes_in_one_request(self):
        from app.ollama_client import generate_meeting_summary

        _use("anthropic")
        segments = [{"id": i, "start": i * 5.0, "end": i * 5 + 4, "speaker": "S", "text": "слово " * 40} for i in range(200)]
        fake = _Fake((200, {"content": [{"type": "text", "text": "## Обзор"}]}))
        with mock.patch("app.llm.httpx.Client", side_effect=fake.client):
            out = generate_meeting_summary(segments, {}, "protocol", answer_lang="ru")
        self.assertEqual(out, "## Обзор")
        self.assertEqual(len(fake.requests), 1)  # Ollama would summarise ~48k characters in parts


class CloudApiTest(unittest.TestCase):
    def test_consent_and_key_endpoints(self):
        try:
            from fastapi.testclient import TestClient

            from app import main
        except ImportError:
            self.skipTest("fastapi not installed")
        init_db()
        update_settings({"llm_cloud_consent": "false", "llm_provider": "ollama"})
        with TestClient(main.app, base_url="http://127.0.0.1:8792") as c:
            c.get(f"/?token={main.SESSION_TOKEN}")
            r = c.post("/api/settings", json={"settings": {"llm_provider": "cloud"}})
            self.assertEqual(r.status_code, 400)
            r = c.post("/api/settings", json={"settings": {"llm_provider": "cloud", "llm_cloud_consent": "true"}})
            self.assertEqual(r.status_code, 200)
            r = c.post("/api/llm/key", json={"provider": "gemini", "key": "AIza-secret-key-0000"})
            self.assertEqual(r.status_code, 200)
            self.assertNotIn("AIza-secret-key-0000", r.text)
            self.assertNotIn("AIza-secret-key-0000", c.get("/api/system").text)
            self.assertEqual(c.post("/api/llm/key", json={"provider": "gemini", "key": "has space"}).status_code, 400)
            c.delete("/api/llm/key/gemini")
        update_settings({"llm_provider": "ollama"})


if __name__ == "__main__":
    unittest.main()
