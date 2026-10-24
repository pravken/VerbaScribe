"""The assistant's conversations (app/assistant_chats.py, app/api/assistant.py)."""
import tests  # noqa: F401
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import assistant_chats as ac


class AssistantChatsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.file = Path(self.tmp.name) / "assistant_chats.json"
        self.patch = mock.patch.object(ac, "CHATS_FILE", self.file)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_saved_and_loaded_newest_first(self):
        ac.save_chats([
            {"id": "a", "title": "Старый", "updated": "2026-10-01T10:00", "items": []},
            {"id": "b", "title": "Новый", "updated": "2026-10-20T10:00", "items": [{"id": 1, "question": "Что решили?", "status": "done"}]},
        ])
        chats = ac.load_chats()
        self.assertEqual([c["id"] for c in chats], ["b", "a"])
        self.assertEqual(chats[0]["items"][0]["question"], "Что решили?")

    def test_a_running_answer_is_stored_as_stopped(self):
        ac.save_chats([{"id": "a", "items": [{"id": 1, "question": "?", "status": "pending"}]}])
        self.assertEqual(ac.load_chats()[0]["items"][0]["status"], "stopped")

    def test_unknown_fields_and_bad_lists(self):
        ac.save_chats([{"id": "a", "secret": "x", "items": [{"id": 1, "question": "q", "status": "done", "html": "<b>"}]}])
        chat = ac.load_chats()[0]
        self.assertNotIn("secret", chat)
        self.assertNotIn("html", chat["items"][0])
        with self.assertRaises(ValueError):
            ac.save_chats({"id": "a"})
        with self.assertRaises(ValueError):
            ac.save_chats([{"title": "no id"}])

    def test_limits(self):
        many = [{"id": str(i), "updated": f"2026-10-{i % 28 + 1:02d}", "items": [{"id": n, "question": "q", "status": "done"} for n in range(150)]}
                for i in range(250)]
        chats = ac.save_chats(many)
        self.assertEqual(len(chats), ac.MAX_CHATS)
        self.assertTrue(all(len(c["items"]) == ac.MAX_ITEMS for c in chats))
        self.assertLessEqual(self.file.stat().st_size, ac.MAX_BYTES)

    def test_a_broken_file_gives_no_conversations(self):
        self.file.write_text("{not json", encoding="utf-8")
        self.assertEqual(ac.load_chats(), [])
        self.file.write_text(json.dumps({"x": 1}), encoding="utf-8")
        self.assertEqual(ac.load_chats(), [])

    def test_answer_documents(self):
        src = [{"n": 1, "title": "Встреча", "date": "22.10", "start": 3725, "speaker": "Анна", "text": "Сделаем к среде"}]
        text = ac.answer_text("Что решили?", "- Пункт **один** [1]", src, "Источники")
        self.assertIn("# Что решили?", text)
        self.assertIn("[1] Встреча · 22.10 · 01:02:05 — Анна: Сделаем к среде", text)
        doc = ac.answer_docx("Что решили?", "## Итог\n- Пункт **один** [1]", src, "Источники")
        self.assertTrue(doc.startswith(b"PK"))


class AssistantApiTest(unittest.TestCase):
    def test_export_formats(self):
        from fastapi.testclient import TestClient
        from app.main import app

        from app import main

        client = TestClient(app, base_url="http://127.0.0.1:8790")
        client.get(f"/?token={main.SESSION_TOKEN}")
        body = {"question": "Что/решили?", "answer": "Ответ", "sources": [], "lang": "ru"}
        r = client.post("/api/assistant/export", json={**body, "fmt": "md"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("filename*=UTF-8''", r.headers["content-disposition"])
        self.assertNotIn("/", r.headers["content-disposition"].split("''", 1)[1].replace("%2F", ""))
        r = client.post("/api/assistant/export", json={**body, "fmt": "exe"})
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()
