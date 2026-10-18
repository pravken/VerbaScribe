import asyncio
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app import database, events
from app import paths
from app.paths import migrate_legacy_data_dir


class MigrationTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
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
