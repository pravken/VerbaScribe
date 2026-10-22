import tests  # noqa: F401 - keeps the real user data out of reach (see tests/__init__.py)
import unittest

try:
    from fastapi.testclient import TestClient

    from app import main
except ImportError:  # app dependencies are not installed (e.g. in the release CI job)
    main = None


@unittest.skipIf(main is None, "fastapi and app dependencies not installed")
class SessionAndSearchApiTest(unittest.TestCase):
    def _client(self, port):
        return TestClient(main.app, base_url=f"http://127.0.0.1:{port}")

    def test_session_cookie_is_per_port(self):
        with self._client(8790) as c:
            r = c.get(f"/?token={main.SESSION_TOKEN}", follow_redirects=False)
            self.assertEqual(r.status_code, 303)
            self.assertIn("vs_session_8790=", r.headers["set-cookie"])
            self.assertEqual(c.get("/api/meetings").status_code, 200)
        # A cookie issued for another port (another local instance) is not accepted here
        with self._client(8799) as other:
            other.cookies.set("vs_session_8790", main.SESSION_TOKEN)
            self.assertEqual(other.get("/api/meetings").status_code, 401)

    def test_requests_of_other_pages_are_refused(self):
        """Another local program's page gets the cookie sent along (same site): it is told by its headers."""
        with self._client(8790) as c:
            c.get(f"/?token={main.SESSION_TOKEN}")
            other = {"Sec-Fetch-Site": "same-site", "Origin": "http://127.0.0.1:9999"}
            self.assertEqual(c.post("/api/settings", json={"settings": {}}, headers=other).status_code, 403)
            self.assertEqual(c.get("/api/meetings", headers={"Sec-Fetch-Site": "cross-site"}).status_code, 403)
            self.assertEqual(c.post("/api/settings", json={"settings": {}}, headers={"Origin": "http://127.0.0.1:9999"}).status_code, 403)
            # the app's own page, a typed address, and a program without browser headers
            self.assertEqual(c.post("/api/settings", json={"settings": {}}, headers={"Sec-Fetch-Site": "same-origin"}).status_code, 200)
            self.assertEqual(c.post("/api/settings", json={"settings": {}}, headers={"Origin": "http://127.0.0.1:8790"}).status_code, 200)
            self.assertEqual(c.get("/api/meetings", headers={"Sec-Fetch-Site": "none"}).status_code, 200)
            self.assertEqual(c.get("/api/meetings").status_code, 200)
            self.assertEqual(c.get("/static/js/app.js", headers={"Sec-Fetch-Site": "cross-site"}).status_code, 200)

    def test_search_endpoint_and_static_revalidation(self):
        with self._client(8790) as c:
            c.get(f"/?token={main.SESSION_TOKEN}")
            m = main.create_meeting(title="Api search", filename="a.wav", media_path="a.wav")
            main.save_meeting_results(m["id"], segments=[{"id": 1, "start": 1.5, "end": 3, "speaker": "SPEAKER_00", "text": "проверка поиска"}])
            hits = c.get("/api/meetings/search", params={"q": "поиск"}).json()
            self.assertIn(m["id"], [h["meeting_id"] for h in hits])
            self.assertEqual(c.get("/static/js/app.js").headers.get("cache-control"), "no-cache")


@unittest.skipIf(main is None, "fastapi and app dependencies not installed")
class ConcurrentEditsTest(unittest.TestCase):
    """Edits that arrive at once (two segments, two participants) must all stay."""

    def _meeting(self):
        main.init_db()
        segs = [{"id": i, "start": i, "end": i + 1, "speaker": "SPEAKER_00", "text": f"реплика {i}"} for i in range(1, 31)]
        spk = {f"SPEAKER_0{i}": {"id": f"SPEAKER_0{i}", "name": f"Участник {i}", "color": "#0A84FF"} for i in range(4)}
        m = main.create_meeting(title="Правки сразу", filename="a.wav", media_path="a.wav")
        main.save_meeting_results(m["id"], segments=segs, speakers=spk)
        return m["id"]

    def test_segments_and_speakers_edited_at_once(self):
        from concurrent.futures import ThreadPoolExecutor

        from app.api.analysis import SegmentUpdatePayload, api_update_segment
        from app.api.library import SpeakerUpdatePayload, api_update_speaker

        mid = self._meeting()
        with ThreadPoolExecutor(16) as pool:
            jobs = [pool.submit(api_update_segment, mid, i, SegmentUpdatePayload(text=f"правка {i}")) for i in range(1, 31)]
            jobs += [pool.submit(api_update_speaker, mid, SpeakerUpdatePayload(speaker_id=f"SPEAKER_0{i}", name=f"Имя {i}"))
                     for i in range(4)]
            for j in jobs:
                j.result()
        m = main.get_meeting(mid)
        self.assertEqual([s["text"] for s in m["segments"]], [f"правка {i}" for i in range(1, 31)])
        self.assertEqual(sorted(s["name"] for s in m["speakers"].values()), [f"Имя {i}" for i in range(4)])

    def test_failed_edit_changes_nothing(self):
        from fastapi import HTTPException

        from app.api.analysis import SegmentUpdatePayload, api_update_segment

        mid = self._meeting()
        with self.assertRaises(HTTPException):
            api_update_segment(mid, 5, SegmentUpdatePayload(speaker="SPEAKER_99"))
        with self.assertRaises(HTTPException):
            api_update_segment(mid, 999, SegmentUpdatePayload(text="нет такой"))
        self.assertEqual(main.get_meeting(mid)["segments"][4]["text"], "реплика 5")
        from app.database import merge_segment_with_previous

        m = main.get_meeting(mid)
        merged = merge_segment_with_previous(mid, 2)
        self.assertEqual(merged["segments"][0]["text"], "реплика 1 реплика 2")
        self.assertEqual(len(merged["segments"]), len(m["segments"]) - 1)


@unittest.skipIf(main is None, "fastapi and app dependencies not installed")
class ConnectionResetFilterTest(unittest.TestCase):
    def test_only_proactor_connection_lost_resets_are_ignored(self):
        class Handle:
            def __repr__(self):
                return "<Handle _ProactorBasePipeTransport._call_connection_lost(None)>"

        self.assertTrue(main._is_proactor_connection_reset({"exception": ConnectionResetError(10054), "handle": Handle()}))
        self.assertFalse(main._is_proactor_connection_reset({"exception": ConnectionResetError(10054), "message": "other"}))
        self.assertFalse(main._is_proactor_connection_reset({"exception": ValueError(), "handle": Handle()}))


@unittest.skipIf(main is None, "fastapi and app dependencies not installed")
class LibraryApiTest(unittest.TestCase):
    def _client(self):
        from unittest import mock

        # The app resumes queued recordings at start: other tests' recordings must not be processed
        patcher = mock.patch.object(main, "enqueue_meeting_job")
        patcher.start()
        self.addCleanup(patcher.stop)
        c = TestClient(main.app, base_url="http://127.0.0.1:8791")
        c.__enter__()
        c.get(f"/?token={main.SESSION_TOKEN}")
        self.addCleanup(c.__exit__, None, None, None)
        return c

    def test_recording_upload_goes_to_the_recordings_folder(self):
        c = self._client()
        r = c.post("/api/meetings/upload", files={"files": ("Запись 10-15.webm", b"webm", "audio/webm")},
                   data={"source": "recording", "title": "Созвон с командой"})
        self.assertEqual(r.status_code, 200, r.text)
        m = r.json()["created"][0]
        imp = c.post("/api/meetings/upload", files={"files": ("talk.mp4", b"mp4", "video/mp4")},
                     data={"last_modified": "1715504400000"}).json()["created"][0]
        self.assertEqual(m["title"], "Созвон с командой")
        self.assertEqual(m["source"], "recording")
        self.assertTrue(m["media_path"].startswith(str(main._recordings_dir())))
        self.assertTrue(imp["recorded_at"].startswith("2024-05-1"))
        self.assertTrue(imp["media_path"].startswith(str(main.UPLOADS_DIR)))
        folders = {f["system_key"]: f["id"] for f in c.get("/api/folders").json() if f["system_key"]}
        self.assertEqual(m["folder_id"], folders["recordings"])
        self.assertEqual(imp["folder_id"], folders["imports"])

    def test_folder_endpoints_and_details(self):
        c = self._client()
        f = c.post("/api/folders", json={"name": "Клиенты"}).json()
        sub = c.post("/api/folders", json={"name": "Акме", "parent_id": f["id"]}).json()
        self.assertEqual(c.patch(f"/api/folders/{f['id']}", json={"parent_id": sub["id"]}).status_code, 400)
        m = main.create_meeting(title="Звонок", filename="c.wav", media_path="c.wav")
        self.assertEqual(c.patch(f"/api/meetings/{m['id']}/folder", json={"folder_id": sub["id"]}).status_code, 200)
        info = c.get(f"/api/meetings/{m['id']}/info").json()
        self.assertEqual([x["name"] for x in info["folders"]], ["Клиенты", "Акме"])
        self.assertFalse(info["media"]["exists"])
        self.assertEqual(c.delete(f"/api/folders/{f['id']}").status_code, 200)
        self.assertEqual(c.get(f"/api/meetings/{m['id']}").json()["folder_id"], sub["id"])

    def test_export_request_and_vram_system_load(self):
        c = self._client()
        m = main.create_meeting(title="Ещё обрабатывается", filename="d.wav", media_path="d.wav")
        r = c.post(f"/api/meetings/{m['id']}/export-request", json={"kind": "doc", "fmt": "docx"})
        self.assertEqual(r.status_code, 409)  # not transcribed and nothing running
        self.assertEqual(c.post(f"/api/meetings/{m['id']}/export-request", json={"kind": "bogus"}).status_code, 409)
        snap = c.get("/api/vram").json()
        self.assertIn("system", snap)
        self.assertIn("idle", snap)


if __name__ == "__main__":
    unittest.main()
