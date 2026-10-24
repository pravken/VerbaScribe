"""The home page and the import window: processing chosen per file, today's meetings, dictation."""
import tests  # noqa: F401 - keeps the real user data out of reach (see tests/__init__.py)
import json
import unittest
from datetime import datetime
from unittest import mock

from app import calendar_ics, database, settings_schema

try:
    from fastapi.testclient import TestClient

    from app import main
    from app.api import library
except ImportError:  # app dependencies are not installed (e.g. in the release CI job)
    main = None

ICS = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:a1
SUMMARY:Созвон
DTSTART:20261024T100000
DTEND:20261024T110000
LOCATION:https://call.example/abc
END:VEVENT
BEGIN:VEVENT
UID:b2
SUMMARY:Встреча в переговорной
DTSTART:20261024T140000
DTEND:20261024T150000
LOCATION:Переговорная 2
DESCRIPTION:Повестка\\nбез ссылки
END:VEVENT
BEGIN:VEVENT
UID:c3
SUMMARY:Ночная смена
DTSTART:20261024T230000
DTEND:20261025T010000
X-MICROSOFT-SKYPETEAMSMEETINGURL:https://call.example/night
END:VEVENT
END:VCALENDAR"""


class ProcessingTest(unittest.TestCase):
    def test_settings_give_the_defaults_and_a_recording_its_own(self):
        s = {"audio_enhance": "true", "vad_filter": "true", "diarization_enabled": "true", "clean_fillers": "true",
             "auto_summary": "false"}
        p = database.effective_processing({}, s)
        self.assertEqual((p["denoise"], p["loudnorm"], p["leveling"], p["summary"]), (True, True, True, False))
        # the import window saved separate switches: they win over audio_enhance
        p = database.effective_processing({}, {**s, "proc_denoise": "false", "proc_leveling": "false"})
        self.assertEqual((p["denoise"], p["loudnorm"], p["leveling"]), (False, True, False))
        p = database.effective_processing({"summary": True, "vad": False, "unknown": True}, s)
        self.assertTrue(p["summary"])
        self.assertFalse(p["vad"])
        self.assertNotIn("unknown", p)

    def test_switches_given_as_words(self):
        """A form may send the switches as words: "false" is off, not a non-empty string."""
        database.init_db()
        m = database.create_meeting(title="Слова", filename="w.wav", media_path="w.wav")
        database.set_meeting_processing(m["id"], {"denoise": "false", "vad": "true", "summary": 0})
        p = database.effective_processing(database.get_meeting(m["id"]).get("processing"), {})
        self.assertEqual((p["denoise"], p["vad"], p["summary"]), (False, True, False))

    def test_home_settings_are_checked(self):
        self.assertEqual(settings_schema.clean("work_day_end", "30"), "24")
        self.assertEqual(settings_schema.clean("ask_import_params", "false"), "false")
        self.assertEqual(json.loads(settings_schema.clean("meet_overrides", '{"a|2026-10-24T10:00": "skip"}')),
                         {"a|2026-10-24T10:00": "skip"})
        for bad in ('{"a": "maybe"}', "[1]", "not json"):
            with self.assertRaises(ValueError):
                settings_schema.clean("meet_overrides", bad)


class CalendarDayTest(unittest.TestCase):
    def test_meetings_with_a_link_are_calls(self):
        evs = {e["uid"]: e for e in calendar_ics.parse(ICS)}
        self.assertTrue(evs["a1"]["online"])
        self.assertFalse(evs["b2"]["online"])
        self.assertEqual(evs["b2"]["location"], "Переговорная 2")
        self.assertTrue(evs["c3"]["online"])

    def test_a_night_shift_reaches_past_midnight(self):
        with mock.patch.object(calendar_ics, "events", return_value=calendar_ics.parse(ICS)):
            day = calendar_ics.day(datetime(2026, 10, 24, 9), datetime(2026, 10, 24, 18))
            night = calendar_ics.day(datetime(2026, 10, 24, 20), datetime(2026, 10, 25, 8))
        self.assertEqual([e["title"] for e in day], ["Созвон", "Встреча в переговорной"])
        self.assertEqual([e["title"] for e in night], ["Ночная смена"])
        self.assertEqual(day[0]["key"], "a1|2026-10-24T10:00")


@unittest.skipIf(main is None, "fastapi and app dependencies not installed")
class HomeApiTest(unittest.TestCase):
    def _client(self):
        # Nothing is processed: neither these recordings nor the queued ones the app resumes at start
        for target in (library, main):
            patcher = mock.patch.object(target, "enqueue_meeting_job")
            patcher.start()
            self.addCleanup(patcher.stop)
        c = TestClient(main.app, base_url="http://127.0.0.1:8792")
        c.__enter__()
        c.get(f"/?token={main.SESSION_TOKEN}")
        # the recordings made here are gone after the test: no other test's worker picks them up
        known = {m["id"] for m in database.list_meetings()}
        self.addCleanup(lambda: [database.delete_meeting(m["id"]) for m in database.list_meetings() if m["id"] not in known])
        self.addCleanup(c.__exit__, None, None, None)
        return c

    def test_upload_keeps_the_processing_of_the_import_window(self):
        c = self._client()
        proc = {"denoise": False, "vad": True, "summary": False}
        r = c.post("/api/meetings/upload", files={"files": ("talk.mp3", b"mp3", "audio/mpeg")},
                   data={"processing": json.dumps(proc)})
        self.assertEqual(r.status_code, 200, r.text)
        m = database.get_meeting(r.json()["created"][0]["id"])
        self.assertFalse(m["processing"]["denoise"])
        self.assertFalse(m["processing"]["summary"])
        bad = c.post("/api/meetings/upload", files={"files": ("x.mp3", b"mp3", "audio/mpeg")}, data={"processing": "[1]"})
        self.assertEqual(bad.status_code, 400)

    def test_recent_gives_a_line_of_the_overview(self):
        c = self._client()
        m = database.create_meeting(title="С обзором", filename="o.wav", media_path="o.wav", recorded_at="2099-01-01T10:00:00")
        database.save_meeting_results(m["id"], summaries={"tldr": "## Итоги\n- **Решили** запустить к пятнице.\n"})
        rows = c.get("/api/home/recent", params={"limit": 3}).json()
        self.assertEqual(rows[0]["id"], m["id"])
        self.assertEqual(rows[0]["summary"], "Решили запустить к пятнице.")

    def test_calendar_day_finds_the_recording_of_a_meeting(self):
        c = self._client()
        m = database.create_meeting(title="Созвон", filename="s.wav", media_path="s.wav", recorded_at="2026-10-24T10:05:00")
        with mock.patch.object(calendar_ics, "events", return_value=calendar_ics.parse(ICS)):
            r = c.get("/api/calendar/day", params={"start": "2026-10-24T09:00", "end": "2026-10-24T18:00"})
        self.assertEqual(r.status_code, 200, r.text)
        evs = r.json()["events"]
        self.assertEqual(evs[0]["recording"]["id"], m["id"])
        self.assertIsNone(evs[1]["recording"])
        self.assertEqual(c.get("/api/calendar/day", params={"start": "2026-10-24T18:00", "end": "2026-10-24T09:00"}).status_code, 400)

    def test_a_recording_time_with_a_zone_does_not_break_the_day(self):
        """An imported recording may carry «+03:00»: compared as local time, not with a 500."""
        from datetime import datetime

        c = self._client()
        local = datetime(2026, 10, 24, 10, 5).astimezone()  # 10:05 here, written with this computer's zone
        m = database.create_meeting(title="С зоной", filename="z.wav", media_path="z.wav", recorded_at=local.isoformat())
        with mock.patch.object(calendar_ics, "events", return_value=calendar_ics.parse(ICS)):
            r = c.get("/api/calendar/day", params={"start": "2026-10-24T09:00", "end": "2026-10-24T18:00"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIsNotNone(r.json()["events"][0]["recording"])  # it (or another 10:05 recording) is the meeting's
        self.assertTrue(m["id"])

    def test_dictation_of_the_page_is_not_typed(self):
        from app import dictation

        c = self._client()
        with mock.patch.object(dictation, "_start") as start:
            self.assertEqual(c.post("/api/dictation/toggle").json()["state"], "started")
            start.assert_called_once()
        with mock.patch.object(dictation, "recognize", return_value=("привет", "m")), \
                mock.patch.object(dictation, "type_text") as typed, mock.patch.object(dictation, "save_to_library"):
            dictation._chunks[:] = [__import__("numpy").ones(16000, dtype="float32")]
            dictation._finish(None, type_out=False)
            typed.assert_not_called()
        self.assertEqual(c.get("/api/dictation").json()["last_text"], "привет")
        c.post("/api/dictation/cancel")
        self.assertFalse(dictation.status()["active"])


if __name__ == "__main__":
    unittest.main()
