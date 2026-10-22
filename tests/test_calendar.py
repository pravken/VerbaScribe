import tests  # noqa: F401 - keeps the real user data out of reach (see tests/__init__.py)
import unittest
from datetime import datetime, timedelta
from unittest import mock

from app import calendar_ics

ICS = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:a
SUMMARY:Планёрка команды
DTSTART:20261019T100000
DTEND:20261019T103000
RRULE:FREQ=WEEKLY;BYDAY=MO,WE;COUNT=6
EXDATE:20261021T100000
ATTENDEE;CN="Анна Петрова";ROLE=REQ-PARTICIPANT:mailto:anna@example.com
ATTENDEE:mailto:bob@example.com
END:VEVENT
BEGIN:VEVENT
UID:b
SUMMARY:Отменено
DTSTART:20261020T120000
STATUS:CANCELLED
END:VEVENT
BEGIN:VEVENT
UID:c
SUMMARY:Длинное
 название
DTSTART;VALUE=DATE:20261022
END:VEVENT
END:VCALENDAR
"""


class CalendarParseTest(unittest.TestCase):
    def test_parse(self):
        evs = calendar_ics.parse(ICS)
        self.assertEqual([e["uid"] for e in evs], ["a", "c"])  # cancelled skipped
        self.assertEqual(evs[0]["attendees"], ["Анна Петрова", "bob@example.com"])
        self.assertEqual(evs[1]["title"], "Длинноеназвание")  # folded line
        self.assertTrue(evs[1]["all_day"])

    def test_weekly_byday_count_exdate(self):
        ev = calendar_ics.parse(ICS)[0]
        occ = calendar_ics.occurrences(ev, datetime(2026, 10, 1), datetime(2026, 12, 1))
        days = [o["start"].strftime("%m-%d") for o in occ]
        self.assertEqual(days, ["10-19", "10-26", "10-28", "11-02", "11-04"])  # 6 by COUNT, 10-21 excluded
        self.assertEqual(occ[0]["end"] - occ[0]["start"], timedelta(minutes=30))

    def test_utc_time(self):
        ev = calendar_ics.parse("BEGIN:VEVENT\nSUMMARY:X\nDTSTART:20261019T100000Z\nEND:VEVENT")[0]
        from datetime import timezone

        expected = datetime(2026, 10, 19, 10, tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
        self.assertEqual(ev["start"], expected)

    def test_daily_until(self):
        ev = calendar_ics.parse("BEGIN:VEVENT\nSUMMARY:X\nDTSTART:20261019T090000\nRRULE:FREQ=DAILY;UNTIL=20261021T090000\nEND:VEVENT")[0]
        self.assertEqual(len(calendar_ics.occurrences(ev, datetime(2026, 10, 1), datetime(2026, 11, 1))), 3)


def _event(rule, start="20100104T090000", end="20100104T091500", extra=""):
    return f"BEGIN:VEVENT\nUID:{rule}\nSUMMARY:x\nDTSTART:{start}\nDTEND:{end}\nRRULE:{rule}\n{extra}END:VEVENT\n"


class CalendarRobustnessTest(unittest.TestCase):
    """A calendar someone else publishes: one odd event must not stop the whole calendar."""

    def _window(self, text, start, days=7):
        evs = calendar_ics.parse("BEGIN:VCALENDAR\n" + text + "END:VCALENDAR\n")
        with mock.patch.object(calendar_ics, "events", return_value=evs):
            return calendar_ics.window(start, start + timedelta(days=days))

    def test_odd_rules_do_not_break_the_calendar(self):
        good = _event("FREQ=WEEKLY;BYDAY=MO", start="20261019T100000", end="20261019T103000")
        for rule in ("FREQ=DAILY;INTERVAL=abc", "FREQ=DAILY;INTERVAL=99999999999", "FREQ=MONTHLY;INTERVAL=100000",
                     "FREQ=YEARLY;COUNT=99999", "FREQ=DAILY;UNTIL=99991231T235959Z"):
            got = self._window(_event(rule, start="20261019T090000", end="20261019T091500") + good, datetime(2026, 10, 18))
            self.assertIn(datetime(2026, 10, 19, 10, 0), [e["start"] for e in got], rule)
        # dates a calendar cannot have
        evs = calendar_ics.parse("BEGIN:VCALENDAR\nBEGIN:VEVENT\nSUMMARY:y\nDTSTART:99991231T235900Z\nEND:VEVENT\n"
                                 "BEGIN:VEVENT\nSUMMARY:z\nDTSTART:99991231T235900\nEND:VEVENT\nEND:VCALENDAR\n")
        self.assertLessEqual(len(evs), 1)

    def test_old_series_reach_today(self):
        """A daily or weekly meeting started years ago is still there (the walk starts near the window)."""
        start = datetime(2026, 10, 19)
        got = self._window(_event("FREQ=DAILY") + _event("FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,TH", start="20100104T120000",
                                                          end="20100104T123000"), start)
        daily = [e["start"] for e in got if e["start"].hour == 9]
        self.assertEqual(daily, [datetime(2026, 10, 19 + i, 9, 0) for i in range(7)])
        weekly = [e["start"] for e in got if e["start"].hour == 12]
        first = datetime(2010, 1, 4, 12, 0)  # a Monday; every second week, on Monday and Thursday
        want = [d for d in (start.replace(hour=12) + timedelta(days=i) for i in range(7))
                if d.weekday() in (0, 3) and ((d - first).days // 7) % 2 == 0]
        self.assertEqual(weekly, want)

    def test_many_events_are_fast(self):
        import time

        text = "".join(_event("FREQ=DAILY", start=f"2000{1 + i % 12:02d}{1 + i % 28:02d}T09{i % 60:02d}00",
                              end=f"2000{1 + i % 12:02d}{1 + i % 28:02d}T10{i % 60:02d}00").replace(f"UID:FREQ=DAILY", f"UID:{i}")
                       for i in range(2000))
        t = time.time()
        got = self._window(text, datetime(2026, 10, 19), days=1)
        self.assertLess(time.time() - t, 3.0)
        self.assertEqual(len(got), 2000)


class CalendarApplyTest(unittest.TestCase):
    def test_recording_gets_title_and_participants(self):
        from app.database import create_meeting, get_meeting, init_db

        init_db()
        m = create_meeting(title="Запись 10-05", filename="a.m4a", media_path="a.m4a", language="ru",
                           model_name="x", num_speakers_hint=0, source="recording", title_is_default=True)
        evs = calendar_ics.parse(ICS)
        with mock.patch.object(calendar_ics, "_source", return_value="cal.ics"), \
             mock.patch.object(calendar_ics, "events", return_value=evs):
            self.assertTrue(calendar_ics.apply_to_recording(m["id"], datetime(2026, 10, 26, 10, 5)))
            self.assertFalse(calendar_ics.apply_to_recording(m["id"], datetime(2026, 10, 26, 10, 5)))  # nothing new
        got = get_meeting(m["id"])
        self.assertEqual(got["title"], "Планёрка команды")
        self.assertIn("Анна Петрова", got["user_notes"])

    def test_no_meeting_no_change(self):
        with mock.patch.object(calendar_ics, "_source", return_value="cal.ics"), \
             mock.patch.object(calendar_ics, "events", return_value=calendar_ics.parse(ICS)):
            self.assertIsNone(calendar_ics.current_meeting(datetime(2026, 10, 21, 10, 5)))  # the excluded day


class OutlookCalendarTest(unittest.TestCase):
    def test_parse_powershell_json(self):
        from app import calendar_outlook

        one = '{"uid":"A1","title":"Планёрка","start":"2026-10-26T10:00:00","end":"2026-10-26T10:30:00","location":"Teams","attendees":"Анна Петрова"}'
        evs = calendar_outlook.parse(one)  # PowerShell 5 gives a single object, not a list
        self.assertEqual(evs[0]["title"], "Планёрка")
        self.assertEqual(evs[0]["attendees"], ["Анна Петрова"])
        self.assertEqual(evs[0]["end"] - evs[0]["start"], timedelta(minutes=30))
        many = '[' + one + ',{"uid":"B","title":"X","start":"bad","end":"bad","attendees":[]}]'
        self.assertEqual(len(calendar_outlook.parse(many)), 1)
        self.assertEqual(calendar_outlook.parse(""), [])

    def test_outlook_source_feeds_the_calendar(self):
        from app import calendar_outlook

        ev = {"uid": "A", "title": "Созвон", "start": datetime(2026, 10, 26, 10), "end": datetime(2026, 10, 26, 11),
              "attendees": ["Борис"], "rrule": "", "exdates": [], "all_day": False, "status": ""}
        calendar_ics._cache.update(source="", at=0.0, events=[], error="")
        with mock.patch.object(calendar_ics, "_source", return_value="outlook"), \
             mock.patch.object(calendar_outlook, "fetch", return_value=[ev]) as fetch:
            self.assertEqual(calendar_ics.current_meeting(datetime(2026, 10, 26, 10, 20))["title"], "Созвон")
            calendar_ics.events()  # cached: Outlook is not asked again
        fetch.assert_called_once()


if __name__ == "__main__":
    unittest.main()
