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


@needs_deps
class GpuSharingTest(unittest.TestCase):
    def test_caption_model(self):
        from app.live import caption_model

        installed = {"small", "large-v3"}
        with mock.patch("app.model_manager.is_whisper_model_installed", side_effect=lambda m: m in installed):
            big = {"vram_total_gb": 24, "recommended_device": "cuda"}
            small = {"vram_total_gb": 8, "recommended_device": "cuda"}
            s = {"whisper_model": "large-v3", "device": "cuda"}
            self.assertEqual(caption_model(s, big), "large-v3")
            self.assertEqual(caption_model(s, small), "small")
            self.assertEqual(caption_model({**s, "device": "cpu"}, big), "small")
            self.assertEqual(caption_model({**s, "live_whisper_model": "large-v3"}, small), "large-v3")
            self.assertEqual(caption_model({**s, "live_whisper_model": "medium"}, small), "large-v3")  # not downloaded

    def test_job_waits_while_captions_hold_the_gpu(self):
        from app import transcriber

        calls = {"n": 0}

        def sessions():
            calls["n"] += 1
            return 1 if calls["n"] <= 2 else 0  # the recording ends after two checks

        with mock.patch("app.live.active_session_count", side_effect=sessions), \
                mock.patch.object(transcriber, "_free_vram_gb", return_value=1.0), \
                mock.patch.object(transcriber, "update_meeting_progress") as progress, \
                mock.patch.object(transcriber, "_check_cancel"), \
                mock.patch.object(transcriber.time, "sleep") as sleep:
            transcriber._wait_for_vram_after_live(1, "large-v3")
        self.assertEqual(sleep.call_count, 2)
        self.assertIn("субтитрами", progress.call_args_list[0].kwargs["stage_text"])
        with mock.patch("app.live.active_session_count", return_value=1), \
                mock.patch.object(transcriber, "_free_vram_gb", return_value=20.0), \
                mock.patch.object(transcriber.time, "sleep") as sleep:
            transcriber._wait_for_vram_after_live(1, "large-v3")
        sleep.assert_not_called()


@needs_deps
class UpdateSourceTest(unittest.TestCase):
    def test_only_this_repository_and_digests_must_agree(self):
        import httpx

        from app import updater

        base = updater.DOWNLOAD_PREFIX + "v9.0.0/"
        release = {"tag_name": "v9.0.0", "assets": [
            {"name": "VerbaScribe-v9.0.0.zip", "browser_download_url": base + "VerbaScribe-v9.0.0.zip", "digest": "sha256:" + "a" * 64},
            {"name": "SHA256SUMS.txt", "browser_download_url": "https://evil.example/SHA256SUMS.txt"},
        ]}
        real = httpx.Client
        transport = httpx.MockTransport(lambda req: httpx.Response(200, json=release))
        db.init_db()
        with mock.patch("httpx.Client", side_effect=lambda **kw: real(transport=transport, **kw)):
            latest = updater.check(force=True)["latest"]
        self.assertEqual(latest["zip_url"], base + "VerbaScribe-v9.0.0.zip")
        self.assertIsNone(latest["sums_url"])  # a file from another host is ignored
        self.assertEqual(latest["zip_digest"], "a" * 64)

        sums = f"{'b' * 64}  VerbaScribe-v9.0.0.zip\n"
        transport = httpx.MockTransport(lambda req: httpx.Response(200, text=sums))
        with mock.patch("httpx.Client", side_effect=lambda **kw: real(transport=transport, **kw)):
            updater._download({**latest, "sums_url": base + "SHA256SUMS.txt"})
        self.assertIn("не совпадают", updater.status()["download"]["error"])


if __name__ == "__main__":
    unittest.main()
