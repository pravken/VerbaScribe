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


if __name__ == "__main__":
    unittest.main()
