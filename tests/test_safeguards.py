import tests  # noqa: F401 - keeps the real user data out of reach (see tests/__init__.py)
import unittest
from unittest import mock

from app import database as db

try:
    from app import llm
except ImportError:
    llm = None
needs_deps = unittest.skipIf(llm is None, "app dependencies not installed")


class LocalOnlyTest(unittest.TestCase):
    def setUp(self):
        db.init_db()
        self.parent = db.create_folder("Секретное")
        self.child = db.create_folder("Вложенная", self.parent["id"])
        self.inside = db.create_meeting("Внутри", "a.wav", "/tmp/a.wav")["id"]
        self.outside = db.create_meeting("Снаружи", "b.wav", "/tmp/b.wav")["id"]
        db.update_meeting_fields(self.inside, folder_id=self.child["id"])

    def tearDown(self):
        for mid in (self.inside, self.outside):
            db.delete_meeting(mid)
        db.delete_folder(self.child["id"])
        db.delete_folder(self.parent["id"])

    def test_folder_mark_covers_subfolders(self):
        self.assertFalse(db.is_local_only(db.get_meeting(self.inside)))
        db.update_folder(self.parent["id"], local_only=True)
        self.assertTrue(db.get_folder(self.parent["id"])["local_only"])
        self.assertTrue(db.is_local_only(db.get_meeting(self.inside)))
        self.assertFalse(db.is_local_only(db.get_meeting(self.outside)))
        self.assertIn(self.inside, db.local_only_ids())
        self.assertNotIn(self.outside, db.local_only_ids())

    def test_recording_mark(self):
        db.update_meeting_fields(self.outside, local_only=1)
        self.assertTrue(db.get_meeting(self.outside)["local_only"])
        self.assertIn(self.outside, db.local_only_ids())
        row = next(m for m in db.list_meetings() if m["id"] == self.outside)
        self.assertIs(row["local_only"], True)


@needs_deps
class LocalOnlyTargetTest(unittest.TestCase):
    def setUp(self):
        db.init_db()
        db.update_settings({"llm_provider": "cloud", "llm_cloud_consent": "true", "llm_cloud_provider": "anthropic"})

    def tearDown(self):
        db.update_settings({"llm_provider": "ollama"})

    def test_cloud_gives_way_to_ollama(self):
        with mock.patch("app.llm._ollama_target", return_value="ollama") as local, \
                mock.patch("app.llm._cloud_target", return_value="cloud"):
            self.assertEqual(llm.target(), "cloud")
            self.assertEqual(llm.target(local_only=True), "ollama")
            local.assert_called_once()
            with self.assertRaises(llm.LlmError):
                llm.target("cloud", local_only=True)

    def test_token_estimate(self):
        self.assertAlmostEqual(llm.estimate_tokens("а" * 1000), 401, delta=2)
        self.assertAlmostEqual(llm.estimate_tokens("a" * 1000), 251, delta=2)


@needs_deps
class DiskApiTest(unittest.TestCase):
    def setUp(self):
        try:
            from fastapi.testclient import TestClient

            from app import main
        except ImportError:
            self.skipTest("fastapi not installed")
        self.main, self.TestClient = main, TestClient

    def test_recording_refused_when_disk_is_full(self):
        main = self.main
        full = mock.Mock(free=100 * 1024 * 1024)
        with mock.patch.object(main, "enqueue_meeting_job"), \
                self.TestClient(main.app, base_url="http://127.0.0.1:8793") as c:
            c.get(f"/?token={main.SESSION_TOKEN}")
            self.assertFalse(c.get("/api/disk").json()["critical"])
            with mock.patch("app.main.shutil.disk_usage", return_value=full):
                st = c.get("/api/disk").json()
                self.assertTrue(st["critical"] and st["low"])
                self.assertEqual(c.post("/api/record/page/start").status_code, 507)


if __name__ == "__main__":
    unittest.main()
