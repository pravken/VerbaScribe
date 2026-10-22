import tests  # noqa: F401 - keeps the real user data out of reach (see tests/__init__.py)
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import database, sysinfo
from app.database import (
    create_folder,
    create_meeting,
    delete_folder,
    delete_meeting,
    get_meeting,
    init_db,
    list_folders,
    meeting_ids_in_folder,
    save_meeting_results,
    set_meeting_folder,
    system_folder_id,
    update_folder,
    update_meeting_progress,
    update_settings,
)

# The release CI runs the tests without the app's packages: what needs them is skipped there
try:
    from app import idle_unload, obsidian, pending_exports
    from app.ollama_client import clean_title, is_generic_title
except ImportError:  # httpx, python-docx … are not installed
    idle_unload = obsidian = pending_exports = None
needs_deps = unittest.skipIf(obsidian is None, "app dependencies not installed")

SEGMENTS = [
    {"id": 1, "start": 0.0, "end": 4.0, "speaker": "SPEAKER_00", "text": "Обсудим релиз на следующей неделе."},
    {"id": 2, "start": 65.0, "end": 70.0, "speaker": "SPEAKER_01", "text": "Тестирование до пятницы."},
]
SPEAKERS = {"SPEAKER_00": {"name": "Анна"}, "SPEAKER_01": {"name": "Борис"}}


def _done_meeting(title="Планёрка", **kw):
    m = create_meeting(title=title, filename="a.wav", media_path="a.wav", **kw)
    save_meeting_results(m["id"], segments=SEGMENTS, speakers=SPEAKERS, summaries={"tldr": "- Коротко"}, language="ru")
    update_meeting_progress(m["id"], status="completed", progress=100, duration=70.0)
    return get_meeting(m["id"])


class FolderTest(unittest.TestCase):
    def setUp(self):
        init_db()

    def test_system_folders_and_default_placement(self):
        keys = {f["system_key"] for f in list_folders() if f["system_key"]}
        self.assertEqual(keys, {"recordings", "imports", "dictations"})
        rec = create_meeting(title="r", filename="r.webm", media_path="r.webm", source="recording")
        imp = create_meeting(title="i", filename="i.mp4", media_path="i.mp4")
        self.assertEqual(rec["folder_id"], system_folder_id("recordings"))
        self.assertEqual(imp["folder_id"], system_folder_id("imports"))
        self.assertTrue(imp["is_video"])
        self.assertFalse(rec["is_video"])  # a recording made in the app is audio, even in .webm

    def test_nesting_moving_and_deleting(self):
        a = create_folder("A")
        b = create_folder("B", a["id"])
        c = create_folder("C", b["id"])
        with self.assertRaises(ValueError):
            update_folder(a["id"], parent_id=c["id"])  # into its own subfolder
        with self.assertRaises(ValueError):
            update_folder(system_folder_id("imports"), parent_id=a["id"])
        m = create_meeting(title="x", filename="x.wav", media_path="x.wav")
        set_meeting_folder(m["id"], c["id"])
        self.assertIn(m["id"], meeting_ids_in_folder(a["id"]))
        delete_folder(b["id"])  # C and its recordings move up into A
        folders = {f["id"]: f for f in list_folders()}
        self.assertNotIn(b["id"], folders)
        self.assertEqual(folders[c["id"]]["parent_id"], a["id"])
        with self.assertRaises(ValueError):
            delete_folder(system_folder_id("recordings"))

    def test_old_library_is_sorted_once(self):
        root = Path(tempfile.mkdtemp())
        try:
            conn = sqlite3.connect(root / "old.db")
            conn.execute("CREATE TABLE meetings (id INTEGER PRIMARY KEY, media_path TEXT, filename TEXT, created_at TEXT, "
                         "folder_id INTEGER, source TEXT, owns_media INTEGER, recorded_at TEXT)")
            conn.execute("CREATE TABLE folders (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, parent_id INTEGER, system_key TEXT, created_at TEXT)")
            up = database.UPLOADS_DIR
            rows = [
                (1, str(up / "1a2b3c4d_Созвон.m4a"), "1a2b3c4d_Созвон.m4a"),       # the app's own mix
                (2, str(up / "1a2b3c4d_Запись 10-15.webm"), "Запись 10-15.webm"),  # microphone recording
                (3, str(up / "1a2b3c4d_lecture.mp4"), "lecture.mp4"),              # imported copy
                (4, "D:\\Video\\talk.mkv", "talk.mkv"),                             # used in place
            ]
            for r in rows:
                conn.execute("INSERT INTO meetings (id, media_path, filename, created_at) VALUES (?, ?, ?, '2024-01-01 10:00:00')", r)
            cur = conn.cursor()
            database._ensure_system_folders(cur)
            database._sort_existing_meetings(cur)
            got = {r[0]: r[1:] for r in cur.execute("SELECT id, source, owns_media FROM meetings")}
            conn.close()
            self.assertEqual(got[1], ("recording", 1))
            self.assertEqual(got[2], ("recording", 1))
            self.assertEqual(got[3], ("import", 1))
            self.assertEqual(got[4], ("local", 0))
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_delete_removes_own_recordings_only(self):
        outside = Path(tempfile.mkdtemp())
        try:
            own = outside / "rec.m4a"
            theirs = outside / "theirs.mp4"
            own.write_bytes(b"x")
            theirs.write_bytes(b"x")
            a = create_meeting(title="a", filename="rec.m4a", media_path=str(own), source="recording", owns_media=True)
            b = create_meeting(title="b", filename="theirs.mp4", media_path=str(theirs), source="local")
            delete_meeting(a["id"])
            delete_meeting(b["id"])
            self.assertFalse(own.exists())
            self.assertTrue(theirs.exists())  # a file added by its path is the user's
        finally:
            shutil.rmtree(outside, ignore_errors=True)


@needs_deps
class TitleTest(unittest.TestCase):
    def test_generic_titles(self):
        for title in ("Запись 14-05", "zoom_0", "VID_20240512_130245", "Recording (3)", "Голосовая заметка", "audio", "123"):
            self.assertTrue(is_generic_title(title), title)
        for title in ("Планёрка 12.05", "Интервью с Петровым", "Q3 roadmap"):
            self.assertFalse(is_generic_title(title), title)

    def test_clean_title(self):
        self.assertEqual(clean_title('Название: «Планирование релиза».\nВторое'), "Планирование релиза")


@needs_deps
class ObsidianTest(unittest.TestCase):
    def setUp(self):
        init_db()
        self.vault = Path(tempfile.mkdtemp())
        update_settings({"obsidian_vault": str(self.vault), "obsidian_subfolder": "Meetings/2024",
                         "obsidian_filename": "{date} {title}", "obsidian_generate": "false"})

    def tearDown(self):
        update_settings({"obsidian_vault": ""})
        shutil.rmtree(self.vault, ignore_errors=True)

    def test_note_is_written_renamed_and_never_overwrites_others(self):
        m = _done_meeting(title="Релиз: план/итоги", recorded_at="2024-05-12 10:30:00")
        path = obsidian.export_meeting(m["id"])
        self.assertEqual(path, self.vault / "Meetings" / "2024" / "2024-05-12 Релиз план итоги.md")
        text = path.read_text(encoding="utf-8")
        self.assertIn("date: 2024-05-12", text)
        self.assertIn(f"verbascribe_id: {m['id']}", text)
        self.assertIn('  - "Анна"', text)
        self.assertIn("**Борис** `01:05` Тестирование до пятницы.", text)
        self.assertIn("## Резюме", text)

        database.update_meeting_fields(m["id"], title="Итоги релиза")
        new_path = obsidian.export_meeting(m["id"])
        self.assertFalse(path.exists())  # renamed, not copied
        self.assertTrue(new_path.name.endswith("Итоги релиза.md"))

        foreign = new_path.with_name("2024-05-12 Чужая заметка.md")
        foreign.write_text("моя заметка", encoding="utf-8")
        other = _done_meeting(title="Чужая заметка", recorded_at="2024-05-12 09:00:00")
        written = obsidian.export_meeting(other["id"])
        self.assertNotEqual(written, foreign)
        self.assertEqual(foreign.read_text(encoding="utf-8"), "моя заметка")

    def test_user_notes_and_edits_survive_updates(self):
        m = _done_meeting(title="Планёрка", recorded_at="2024-05-13 10:30:00")
        path = obsidian.export_meeting(m["id"])
        text = path.read_text(encoding="utf-8")
        self.assertIn(obsidian.NOTES_MARKER, text)
        # The user writes in their section: kept by every update
        path.write_text(text + "- [x] позвонить Анне\n", encoding="utf-8")
        database.update_meeting_fields(m["id"], title="Планёрка команды")
        path = obsidian.export_meeting(m["id"], explicit=False)
        self.assertIn("- [x] позвонить Анне", path.read_text(encoding="utf-8"))
        # An edit above the section: an automatic update leaves the note alone
        edited = path.read_text(encoding="utf-8").replace("## Резюме", "## Резюме (моё)")
        path.write_text(edited, encoding="utf-8")
        obsidian.export_meeting(m["id"], explicit=False)
        self.assertEqual(path.read_text(encoding="utf-8"), edited)

    def test_tasks_become_checklist(self):
        self.assertEqual(obsidian._tasks_as_checklist("- сделать\n- [x] готово"), "- [ ] сделать\n- [x] готово")


@needs_deps
class PendingExportTest(unittest.TestCase):
    def setUp(self):
        init_db()
        self.out = Path(tempfile.mkdtemp())
        update_settings({"export_dir": str(self.out)})

    def tearDown(self):
        update_settings({"export_dir": ""})
        shutil.rmtree(self.out, ignore_errors=True)

    def test_ready_queued_and_saved_after_the_job(self):
        m = _done_meeting()
        self.assertEqual(pending_exports.request(m["id"], {"kind": "doc", "fmt": "txt"})["status"], "ready")
        with mock.patch("app.transcriber.enqueue_translate_job", return_value=True) as enq:
            res = pending_exports.request(m["id"], {"kind": "doc", "fmt": "srt", "lang": "en"})
        self.assertEqual(res["status"], "queued")
        enq.assert_called_once()
        self.assertEqual(len(database.get_pending_exports(m["id"])), 1)
        # The translation job finished: the file is written into the export folder
        from app.translation import source_fingerprint

        database.save_meeting_translation(m["id"], "en", {"model": "test", "items": {
            "1": {"text": "Let us discuss the release.", "src": source_fingerprint(SEGMENTS[0]["text"]), "start": 0.0},
        }})
        pending_exports.process(m["id"])
        files = list(self.out.iterdir())
        self.assertEqual(len(files), 1)
        self.assertIn("Let us discuss the release.", files[0].read_text(encoding="utf-8"))
        self.assertEqual(database.get_pending_exports(m["id"]), [])

    def test_failed_job_drops_the_export(self):
        m = _done_meeting()
        database.set_pending_exports(m["id"], [{"kind": "doc", "fmt": "txt", "lang": "de", "variant": "translated", "tries": 1}])
        pending_exports.process(m["id"])  # the translation was already tried once
        self.assertEqual(database.get_pending_exports(m["id"]), [])
        self.assertEqual(list(self.out.iterdir()), [])

    def test_dub_needs_the_dub_window(self):
        m = _done_meeting()
        self.assertEqual(pending_exports.request(m["id"], {"kind": "dub_media", "lang": "en"})["status"], "needs_dub")


@needs_deps
class LibraryScopeTest(unittest.TestCase):
    def setUp(self):
        init_db()

    def test_scoped_question_reads_small_folders_in_full(self):
        from app import library_qa

        folder = create_folder("Scope")
        m = _done_meeting()
        set_meeting_folder(m["id"], folder["id"])
        with mock.patch("app.ollama_client.ensure_ollama_running"), \
                mock.patch("app.ollama_client.get_ollama_status", return_value={"running": True, "models": ["qwen2.5:7b"]}), \
                mock.patch("app.ollama_client._ollama_chat", return_value="О релизе [1].") as chat:
            res = library_qa.ask_library("о чём это?", meeting_ids=meeting_ids_in_folder(folder["id"]))
        self.assertEqual(res["mode"], "full")
        self.assertTrue(res["found"])
        self.assertIn("Тестирование до пятницы.", chat.call_args[0][3])
        with self.assertRaises(ValueError):
            library_qa.ask_library("вопрос", meeting_ids=[])


class SystemTest(unittest.TestCase):
    def test_memory_and_cpu(self):
        mem = sysinfo.memory()
        if mem is not None:
            self.assertGreater(mem["total_gb"], 0)
        sysinfo.cpu_percent()
        cpu = sysinfo.cpu_percent()
        self.assertTrue(cpu is None or 0 <= cpu <= 100)

    @needs_deps
    def test_idle_keep_alive_follows_the_setting(self):
        init_db()
        update_settings({"idle_unload_minutes": "15"})
        self.assertEqual(idle_unload.ollama_keep_alive(), "15m")
        update_settings({"idle_unload_minutes": "0"})
        self.assertIsNone(idle_unload.ollama_keep_alive())
        update_settings({"idle_unload_minutes": "10"})


if __name__ == "__main__":
    unittest.main()
