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

    def test_search_endpoint_and_static_revalidation(self):
        with self._client(8790) as c:
            c.get(f"/?token={main.SESSION_TOKEN}")
            m = main.create_meeting(title="Api search", filename="a.wav", media_path="a.wav")
            main.save_meeting_results(m["id"], segments=[{"id": 1, "start": 1.5, "end": 3, "speaker": "SPEAKER_00", "text": "проверка поиска"}])
            hits = c.get("/api/meetings/search", params={"q": "поиск"}).json()
            self.assertIn(m["id"], [h["meeting_id"] for h in hits])
            self.assertEqual(c.get("/static/app.js").headers.get("cache-control"), "no-cache")


@unittest.skipIf(main is None, "fastapi and app dependencies not installed")
class ConnectionResetFilterTest(unittest.TestCase):
    def test_only_proactor_connection_lost_resets_are_ignored(self):
        class Handle:
            def __repr__(self):
                return "<Handle _ProactorBasePipeTransport._call_connection_lost(None)>"

        self.assertTrue(main._is_proactor_connection_reset({"exception": ConnectionResetError(10054), "handle": Handle()}))
        self.assertFalse(main._is_proactor_connection_reset({"exception": ConnectionResetError(10054), "message": "other"}))
        self.assertFalse(main._is_proactor_connection_reset({"exception": ValueError(), "handle": Handle()}))


if __name__ == "__main__":
    unittest.main()
