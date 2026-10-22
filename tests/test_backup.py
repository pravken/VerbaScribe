import tests  # noqa: F401 - keeps the real user data out of reach (see tests/__init__.py)
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

from app import backup


class BackupTest(unittest.TestCase):
    def test_make_restore_apply(self):
        from app import secret_store
        from app.database import DB_PATH, _db_lock, create_meeting, get_meeting, init_db, list_meetings

        init_db()
        media = Path(tempfile.mkdtemp()) / "talk.m4a"
        media.write_bytes(b"sound")
        m = create_meeting("Встреча для копии", "talk.m4a", str(media))
        secret_store.set_secret("llm_key_openai", "sk-test")
        out = Path(tempfile.mkdtemp()) / "copy.zip"
        info = backup.make(out, with_media=True)
        self.assertGreaterEqual(info["media_files"], 1)
        with zipfile.ZipFile(out) as z:
            names = z.namelist()
            self.assertIn("meetings.db", names)
            db = Path(z.extract("meetings.db", tempfile.mkdtemp()))
        conn = sqlite3.connect(str(db))
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        self.assertNotIn("secrets", tables)  # API keys stay on this computer

        before = len(list_meetings())
        media.unlink()  # lost; the restore puts it back (it lived outside the app's folders: into the recordings folder)
        res = backup.restore(out)
        self.assertEqual(res["media_restored"], 1)
        self.assertFalse(media.exists())
        self.assertTrue(backup.PENDING.exists())

        create_meeting("Сделана после копии", "x.m4a", "/tmp/x.m4a")
        # What the next start does, before anything opens the database. Here the background threads of
        # the app started by earlier tests (call detection, retention, the search indexer...) still
        # read it; on Windows an open -wal cannot be deleted, so they wait on the database lock.
        with _db_lock:
            kept = backup.apply_pending()
        self.assertTrue(Path(kept).exists())
        self.assertFalse(backup.PENDING.exists())
        self.assertEqual(len(list_meetings()), before)
        self.assertEqual(get_meeting(m["id"])["title"], "Встреча для копии")
        self.assertEqual(secret_store.get_secret("llm_key_openai"), "sk-test")  # the keys of this computer stay
        moved = Path(get_meeting(m["id"])["media_path"])
        self.assertEqual(moved.parent.name, backup.RESTORED_FOLDER)
        self.assertEqual(moved.read_bytes(), b"sound")
        self.assertTrue(DB_PATH.exists())

    def test_restore_of_a_foreign_copy(self):
        """A copy someone sent: it may not write files where it likes nor change where data goes."""
        import json

        from app.database import create_meeting, get_all_settings, init_db, update_settings
        from app.paths import DATA_DIR

        init_db()
        update_settings({"ollama_url": "http://127.0.0.1:11434"})
        inside = DATA_DIR / "uploads" / "inside.m4a"
        m = create_meeting("Своя", "inside.m4a", str(inside))
        outside = Path(tempfile.mkdtemp()) / "Startup" / "evil.bat"
        stray = Path(tempfile.mkdtemp()) / "stray.m4a"
        out = Path(tempfile.mkdtemp()) / "copy.zip"
        backup.make(out)
        with zipfile.ZipFile(out) as z:
            db = Path(z.extract("meetings.db", tempfile.mkdtemp()))
        conn = sqlite3.connect(str(db))
        conn.execute("UPDATE settings SET value = 'http://attacker.example' WHERE key = 'ollama_url'")
        conn.execute("INSERT INTO meetings (title, filename, media_path, created_at) VALUES ('Чужая', 'evil.bat', ?, '2026-01-01')", (str(outside),))
        evil_id = conn.execute("SELECT max(id) FROM meetings").fetchone()[0]
        conn.commit()
        conn.close()
        bad = Path(tempfile.mkdtemp()) / "bad.zip"
        media = [{"id": m["id"], "path": str(inside), "name": f"media/{m['id']}_inside.m4a"},
                 {"id": evil_id, "path": str(outside), "name": f"media/{evil_id}_evil.bat"},
                 {"id": m["id"], "path": str(stray), "name": "media/stray.m4a"}]
        with zipfile.ZipFile(bad, "w") as z:
            z.writestr("manifest.json", json.dumps({"format": 1, "app": "VerbaScribe", "media": media}))
            z.write(db, "meetings.db")
            for f in media:
                z.writestr(f["name"], b"x")
        res = backup.restore(bad)
        self.assertEqual(res["media_restored"], 1)  # only the file of its own recording, at its own place
        self.assertTrue(inside.exists())
        self.assertFalse(outside.exists())
        self.assertFalse(outside.parent.exists())
        self.assertFalse(stray.exists())
        conn = sqlite3.connect(str(backup.PENDING))
        url = conn.execute("SELECT value FROM settings WHERE key = 'ollama_url'").fetchone()[0]
        conn.close()
        self.assertEqual(url, get_all_settings()["ollama_url"])
        backup.PENDING.unlink()
        inside.unlink()

    def test_not_a_backup(self):
        bad = Path(tempfile.mkdtemp()) / "x.zip"
        with zipfile.ZipFile(bad, "w") as z:
            z.writestr("hello.txt", "hi")
        with self.assertRaises(ValueError):
            backup.restore(bad)


if __name__ == "__main__":
    unittest.main()
