import tests  # noqa: F401 - keeps the real user data out of reach (see tests/__init__.py)
import asyncio
import shutil
import sqlite3
import tempfile
import sys
import unittest
from pathlib import Path

from app import database, events
from app import paths
from app.paths import migrate_legacy_data_dir


class MigrationTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp()).resolve()  # the CI runner has a short (8.3) temp path
        self.legacy = self.root / "app" / "data"
        (self.legacy / "uploads").mkdir(parents=True)
        (self.legacy / "audio").mkdir()
        (self.legacy / "uploads" / "a_call.mp4").write_bytes(b"video" * 100)
        (self.legacy / "audio" / "meeting_1.wav").write_bytes(b"wave" * 100)
        (self.legacy / ".session_token").write_text("old-token")
        conn = sqlite3.connect(self.legacy / "meetings.db")
        conn.execute("CREATE TABLE meetings (id INTEGER PRIMARY KEY, media_path TEXT, audio_path TEXT)")
        conn.execute(
            "INSERT INTO meetings VALUES (1, ?, ?)",
            (str(self.legacy / "uploads" / "a_call.mp4"), str(self.legacy / "audio" / "meeting_1.wav")),
        )
        conn.execute("INSERT INTO meetings VALUES (2, 'D:\\Calls\\external.mkv', '')")
        conn.commit()
        conn.close()
        self.target = self.root / "LocalAppData" / "VerbaScribe"

    def tearDown(self):
        paths.LEGACY_CONFLICT = None
        shutil.rmtree(self.root, ignore_errors=True)

    def test_moves_files_and_rewrites_paths(self):
        self.assertTrue(migrate_legacy_data_dir(self.legacy, self.target))
        self.assertTrue((self.target / "uploads" / "a_call.mp4").is_file())
        self.assertFalse((self.target / ".session_token").exists())
        self.assertEqual(sorted(p.name for p in self.legacy.iterdir()), ["MOVED.txt"])
        conn = sqlite3.connect(self.target / "meetings.db")
        rows = dict((r[0], (r[1], r[2])) for r in conn.execute("SELECT id, media_path, audio_path FROM meetings"))
        conn.close()
        self.assertEqual(Path(rows[1][0]), self.target / "uploads" / "a_call.mp4")
        self.assertEqual(Path(rows[1][1]), self.target / "audio" / "meeting_1.wav")
        self.assertEqual(rows[2][0], "D:\Calls\external.mkv")  # files outside data/ stay where they are
        # Second start: nothing left to migrate
        self.assertFalse(migrate_legacy_data_dir(self.legacy, self.target))

    def _target_db(self, rows):
        self.target.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.target / "meetings.db")
        conn.execute("CREATE TABLE meetings (id INTEGER PRIMARY KEY, media_path TEXT, audio_path TEXT)")
        for i in range(rows):
            conn.execute("INSERT INTO meetings VALUES (?, 'x.wav', '')", (100 + i,))
        conn.commit()
        conn.close()

    def test_never_overwrites_existing_database(self):
        self._target_db(rows=1)
        before = (self.target / "meetings.db").read_bytes()
        self.assertFalse(migrate_legacy_data_dir(self.legacy, self.target))
        self.assertTrue((self.legacy / "meetings.db").is_file())
        self.assertEqual((self.target / "meetings.db").read_bytes(), before)
        # ...but the conflict is reported so the UI can warn about the old folder
        self.assertEqual(paths.LEGACY_CONFLICT["legacy"], str(self.legacy.resolve()))
        self.assertEqual(paths.LEGACY_CONFLICT["legacy_recordings"], 2)

    def test_empty_target_database_is_backed_up_and_replaced(self):
        # e.g. the app was started once from another checkout and created an empty library
        self._target_db(rows=0)
        (self.target / "meetings.db-wal").write_bytes(b"")
        self.assertTrue(migrate_legacy_data_dir(self.legacy, self.target))
        conn = sqlite3.connect(self.target / "meetings.db")
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM meetings").fetchone()[0], 2)
        conn.close()
        backups = sorted(p.name for p in self.target.glob("meetings.empty-*"))
        self.assertEqual(len(backups), 2, backups)  # .db and .db-wal
        self.assertTrue((self.target / "uploads" / "a_call.mp4").is_file())
        self.assertIsNone(paths.LEGACY_CONFLICT)


class SearchIndexTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        database.init_db()

    def _meeting(self, title, texts):
        m = database.create_meeting(title=title, filename="x.wav", media_path="x.wav")
        segs = [
            {"id": i + 1, "start": float(i * 10), "end": float(i * 10 + 5), "speaker": "SPEAKER_00", "text": t, "words": []}
            for i, t in enumerate(texts)
        ]
        database.save_meeting_results(
            m["id"], speakers={"SPEAKER_00": {"id": "SPEAKER_00", "name": "Анна"}}, segments=segs
        )
        return m["id"]

    def test_word_prefix_search_case_insensitive(self):
        mid = self._meeting("Планёрка", ["Обсудили БЮДЖЕТ на квартал", "Погода хорошая", "Бюджетный план готов"])
        res = {r["meeting_id"]: r for r in database.search_all_meetings("бюджет")}
        self.assertIn(mid, res)
        hits = res[mid]["hits"]
        self.assertEqual([h["segment_id"] for h in hits], [1, 3])
        self.assertEqual(hits[0]["speaker"], "Анна")
        # all words must match, user-typed FTS syntax is harmless
        self.assertEqual(
            [h["segment_id"] for h in {r["meeting_id"]: r for r in database.search_all_meetings('план "готов')}[mid]["hits"]],
            [3],
        )
        self.assertEqual(database.search_all_meetings('NEAR( OR *'), [])

    def test_title_match_and_index_follows_edits(self):
        mid = self._meeting("Созвон с подрядчиком", ["тест"])
        self.assertIn(mid, [r["meeting_id"] for r in database.search_all_meetings("подрядч")])
        database.save_meeting_results(mid, segments=[{"id": 1, "start": 0, "end": 1, "speaker": "SPEAKER_00", "text": "уникальнаяфраза"}])
        self.assertIn(mid, [r["meeting_id"] for r in database.search_all_meetings("уникальнаяфраза")])
        self.assertNotIn(mid, [r["meeting_id"] for r in database.search_all_meetings("тест")])
        database.delete_meeting(mid)
        self.assertEqual(database.search_all_meetings("уникальнаяфраза"), [])

    def test_mid_word_fragment_falls_back_to_substring_search(self):
        mid = self._meeting("Обед", ["Сварили борщ с капустой", "Без совпадений"])
        res = {r["meeting_id"]: r for r in database.search_all_meetings("рщ с")}
        self.assertEqual([h["segment_id"] for h in res[mid]["hits"]], [1])
        # words in any order still use the index
        res = {r["meeting_id"]: r for r in database.search_all_meetings("капуст борщ")}
        self.assertEqual([h["segment_id"] for h in res[mid]["hits"]], [1])

    def test_hits_are_capped_per_meeting(self):
        mid = self._meeting("cap", [f"повтор {i}" for i in range(10)])
        res = {r["meeting_id"]: r for r in database.search_all_meetings("повтор")}
        self.assertEqual(len(res[mid]["hits"]), 4)


class EventBusTest(unittest.TestCase):
    def test_progress_events_are_coalesced_without_losing_status(self):
        async def scenario():
            sub = events.subscribe()
            try:
                events.publish({"type": "progress", "id": 7, "status": "transcribing", "progress": 20})
                events.publish({"type": "progress", "id": 7, "progress": 30, "stage_text": "x"})
                events.publish({"type": "list"})
                batch = await sub.next_batch(1.0)
                self.assertEqual(len(batch), 2)
                progress = next(e for e in batch if e["type"] == "progress")
                self.assertEqual(progress["status"], "transcribing")
                self.assertEqual(progress["progress"], 30)
                self.assertEqual(await sub.next_batch(0.05), [])
            finally:
                events.unsubscribe(sub)
            self.assertEqual(events.subscriber_count(), 0)

        asyncio.run(scenario())

    def test_database_updates_publish_progress(self):
        async def scenario():
            database.init_db()
            m = database.create_meeting(title="t", filename="t.wav", media_path="t.wav")
            sub = events.subscribe()
            try:
                await asyncio.get_running_loop().run_in_executor(
                    None, lambda: database.update_meeting_progress(m["id"], status="transcribing", progress=150)
                )
                batch = await sub.next_batch(1.0)
                self.assertEqual(batch, [{"type": "progress", "id": m["id"], "status": "transcribing", "progress": 100}])
            finally:
                events.unsubscribe(sub)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()


class PendingDeleteTest(unittest.TestCase):
    """A file Windows keeps open (the player streams it) is deleted at the next start, not left forever."""

    def test_locked_file_is_deleted_later(self):
        import tempfile
        from pathlib import Path
        from unittest import mock

        from app import database

        database.init_db()
        f = Path(tempfile.mkdtemp()) / "open.wav"
        f.write_bytes(b"x")
        real_unlink = Path.unlink

        def locked(self, missing_ok=False):
            if self == f:
                raise PermissionError("used by another process")
            return real_unlink(self, missing_ok=missing_ok)

        with mock.patch.object(Path, "unlink", locked):
            self.assertFalse(database.delete_file(str(f)))
            self.assertEqual(database.retry_pending_deletes(), 0)  # still open
        self.assertTrue(f.exists())
        self.assertEqual(database.retry_pending_deletes(), 1)
        self.assertFalse(f.exists())
        self.assertEqual(database.retry_pending_deletes(), 0)


class UploadDiskSpaceTest(unittest.TestCase):
    def test_upload_refused_without_room(self):
        from unittest import mock

        from fastapi.testclient import TestClient

        from app import main
        from app.api import library

        with TestClient(main.app, base_url="http://127.0.0.1:8790") as c:
            c.get(f"/?token={main.SESSION_TOKEN}")
            before = set(main.UPLOADS_DIR.iterdir())
            with mock.patch.object(library, "_free_bytes", return_value=100 * 1024 * 1024):
                r = c.post("/api/meetings/upload", files={"files": ("big.wav", b"x" * 1024, "audio/wav")})
            self.assertEqual(r.status_code, 507, r.text)
            self.assertEqual(set(main.UPLOADS_DIR.iterdir()), before)


class ShutdownTest(unittest.TestCase):
    def test_work_processes_end_with_the_app(self):
        import subprocess
        import sys

        from app import shutdown

        proc = shutdown.track(subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"]))
        shutdown.stop_all()
        self.assertIsNotNone(proc.wait(timeout=10))

    @unittest.skipUnless(sys.platform == "win32", "Windows jobs")
    def test_work_processes_end_even_when_the_app_is_killed(self):
        """A crash or Task Manager skips stop_all(): the job of the tracked processes ends them."""
        import ctypes
        import subprocess
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        app_like = (
            "import os, subprocess, sys\n"
            "from app import shutdown\n"
            "child = shutdown.track(subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)']))\n"
            "print(child.pid, flush=True)\n"
            "os._exit(1)  # no stop_all(), as after a crash\n"
        )
        out = subprocess.run([sys.executable, "-c", app_like], capture_output=True, text=True, cwd=str(root), timeout=60)
        pid = int(out.stdout.split()[0])
        SYNCHRONIZE, WAIT_OBJECT_0 = 0x00100000, 0
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        h = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if not h:
            return  # already gone
        try:
            self.assertEqual(kernel32.WaitForSingleObject(ctypes.c_void_p(h), 10000), WAIT_OBJECT_0, out.stderr)
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(h))

    def test_background_work_keeps_the_app_open(self):
        from unittest import mock

        from app import app_status, backup, dub_setup

        with mock.patch("app.transcriber.queue_snapshot", return_value=[]):
            self.assertEqual(app_status.snapshot()["state"], "idle")
            with mock.patch.dict(dub_setup._state, {"active": True}):
                st = app_status.snapshot()
                self.assertEqual(st["state"], "busy")
                self.assertIn("Установка дубляжа", st["text"])
            with mock.patch.object(backup, "busy", 1):
                self.assertIn("Резервная копия", app_status.snapshot()["text"])


class OrphanFilesTest(unittest.TestCase):
    def test_only_old_files_of_no_recording_are_found(self):
        import os
        import time

        from app.paths import DATA_DIR

        database.init_db()
        m = database.create_meeting(title="Живая", filename="a.wav", media_path=str(DATA_DIR / "uploads" / "kept.wav"))
        old = time.time() - 3 * 86400

        def put(rel, age=old):
            p = DATA_DIR / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"x" * 100)
            os.utime(p, (age, age))
            return p

        kept = [put("uploads/kept.wav"), put(f"thumbs/{m['id']}.jpg"), put(f"audio/meeting_{m['id']}.peaks.json"),
                put("uploads/system_123.wav"), put("uploads/fresh.wav", age=time.time())]
        orphans = [put("uploads/lost.mp4"), put("thumbs/999999.jpg"), put("audio/meeting_999999.wav"),
                   put("dubs/work_meeting_999999_en/clip_1.wav")]
        self.addCleanup(lambda: [p.unlink(missing_ok=True) for p in kept])
        found = set(database.orphan_files())
        for p in orphans:
            self.assertIn(p, found)
        for p in kept:
            self.assertNotIn(p, found)

        res = database.delete_orphan_files()
        self.assertGreaterEqual(res["files"], len(orphans))
        self.assertGreaterEqual(res["bytes"], 400)
        self.assertTrue(all(p.exists() for p in kept))
        self.assertFalse(any(p.exists() for p in orphans))
        self.assertFalse((DATA_DIR / "dubs" / "work_meeting_999999_en").exists())  # its folder too


class ClearResultsTest(unittest.TestCase):
    def test_dubs_and_waiting_exports_go_with_the_results(self):
        from app.paths import DATA_DIR

        database.init_db()
        m = database.create_meeting(title="Очистка", filename="a.wav", media_path="a.wav")
        dub = DATA_DIR / "dubs" / f"{m['id']}_en.m4a"
        dub.parent.mkdir(parents=True, exist_ok=True)
        dub.write_bytes(b"x")
        database.save_meeting_dub(m["id"], {"lang": "en", "path": str(dub)})
        database.set_pending_exports(m["id"], [{"kind": "transcript", "fmt": "txt"}])
        database.clear_results(m["id"])
        after = database.get_meeting(m["id"])
        self.assertEqual(after["dubs"], [])
        self.assertEqual(after["pending_exports"], [])
        self.assertFalse(dub.exists())


class FolderSortMigrationTest(unittest.TestCase):
    def test_sorting_cut_off_is_done_at_the_next_start(self):
        """The column came (autocommit) but the sorting did not: the next start sorts."""
        database.init_db()
        m = database.create_meeting(title="Старая", filename="a.wav", media_path="a.wav")
        with database._db_lock:
            conn = database.get_connection()
            conn.execute("UPDATE meetings SET folder_id = NULL")
            conn.execute("DELETE FROM settings WHERE key = 'folders_sorted_v1'")
            conn.commit()
            conn.close()
        database.init_db()
        self.assertIsNotNone(database.get_meeting(m["id"])["folder_id"])
        # sorted once: a recording the user moves to the top level stays there
        database.set_meeting_folder(m["id"], None)
        database.init_db()
        self.assertIsNone(database.get_meeting(m["id"])["folder_id"])


class WhisperModelFilesTest(unittest.TestCase):
    def test_cut_off_download_is_not_installed_and_delete_is_whole(self):
        from app import model_manager as mm

        mid = next(iter(mm._WHISPER_REPOS))
        d = mm._whisper_model_dir(mid)
        d.mkdir(parents=True, exist_ok=True)
        (d / "model.bin").write_bytes(b"w")
        (d / "config.json").write_text("{}")
        self.assertFalse(mm.is_whisper_model_installed(mid))  # the tokenizer never came
        (d / "tokenizer.json").write_text("{}")
        self.assertTrue(mm.is_whisper_model_installed(mid))  # an older complete download
        (d / "tokenizer.json").unlink()
        (d / mm.COMPLETE_MARK).write_text("repo")
        self.assertTrue(mm.is_whisper_model_installed(mid))
        self.assertTrue(mm.delete_whisper_model(mid))
        self.assertFalse(d.exists())
        self.assertEqual(list(d.parent.glob(d.name + ".deleting-*")), [])


class ListFlagsTest(unittest.TestCase):
    def test_flags_follow_the_transcript_and_summary(self):
        database.init_db()
        m = database.create_meeting(title="Флаги", filename="a.wav", media_path="a.wav")
        flags = lambda: next((x["has_transcript"], x["has_summary"]) for x in database.list_meetings() if x["id"] == m["id"])
        self.assertEqual(flags(), (False, False))
        database.save_meeting_results(m["id"], segments=[{"id": 1, "start": 0, "end": 1, "text": "да"}])
        database.merge_summaries(m["id"], {"protocol": "# Итог"})
        self.assertEqual(flags(), (True, True))
        database.clear_results(m["id"])
        self.assertEqual(flags(), (False, False))
        # a library from before the flags: filled in once at the start
        database.save_meeting_results(m["id"], segments=[{"id": 1, "start": 0, "end": 1, "text": "да"}])
        with database._db_lock:
            conn = database.get_connection()
            conn.execute("UPDATE meetings SET has_transcript = 0")
            conn.execute("DELETE FROM settings WHERE key = 'meeting_flags_v1'")
            conn.commit()
            conn.close()
        database.init_db()
        self.assertEqual(flags(), (True, False))
