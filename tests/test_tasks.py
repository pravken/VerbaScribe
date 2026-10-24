"""The Tasks page: due words as dates, kinds of assignees, tasks changed and added by hand, the summary."""
import tests  # noqa: F401 - keeps the real user data out of reach (see tests/__init__.py)
import unittest
from datetime import date
from unittest import mock

try:
    from fastapi.testclient import TestClient

    from app import main
except ImportError:  # app dependencies are not installed (e.g. in the release CI job)
    main = None

THURSDAY = date(2026, 10, 22)


class DueWordsTest(unittest.TestCase):
    def test_words_become_dates(self):
        from app.commitments import parse_due

        cases = {
            "к пятнице": "2026-10-23", "до среды": "2026-10-28", "в четверг": "2026-10-29", "завтра": "2026-10-23",
            "сегодня": "2026-10-22", "послезавтра": "2026-10-24", "до 15 ноября": "2026-11-15", "15.11": "2026-11-15",
            "через неделю": "2026-10-29", "через 3 дня": "2026-10-25", "через две недели": "2026-11-05",
            "на следующей неделе": "2026-10-26", "до конца месяца": "2026-10-31", "на этой неделе": "2026-10-23",
            "by Friday": "2026-10-23", "Nov 3": "2026-11-03", "in 2 days": "2026-10-24",
            "к 2 января": "2027-01-02",  # said in October about January: the next year
        }
        for words, want in cases.items():
            self.assertEqual(str(parse_due(words, THURSDAY)), want, words)
        for words in ("", "когда-нибудь", "как получится"):
            self.assertIsNone(parse_due(words, THURSDAY), words)

    def test_guess_names_and_day(self):
        from app.commitments import guess

        self.assertEqual(guess("Анне подготовить отчёт к пятнице", ["Анна", "Олег Петров"], THURSDAY),
                         {"who": "Анна", "due_date": "2026-10-23"})
        self.assertEqual(guess("Олегу проверить", ["Анна", "Олег Петров"], THURSDAY)["who"], "Олег Петров")
        self.assertEqual(guess("просто задача", ["Анна"], THURSDAY), {"who": "", "due_date": ""})


class TasksTest(unittest.TestCase):
    def setUp(self):
        from app.database import create_meeting, init_db, save_meeting_results

        init_db()
        self.m = create_meeting("Планёрка задач", "t.m4a", "/tmp/t.m4a", recorded_at="2026-10-22T10:00:00")
        segs = [{"id": 0, "start": 70, "end": 75, "text": "Анна подготовит презентацию к пятнице", "speaker": "S1"},
                {"id": 1, "start": 90, "end": 95, "text": "пусть бухгалтерия проверит реквизиты до среды", "speaker": "S2"}]
        save_meeting_results(self.m["id"], segments=segs, speakers={"S1": {"name": "Анна"}, "S2": {"name": "Олег"}})

    def _extract(self, answer):
        from app import commitments

        llm = mock.Mock(is_cloud=False, max_chars=100000)
        llm.chat.return_value = answer
        with mock.patch("app.llm.target", return_value=llm):
            return commitments.extract(self.m["id"])

    ANSWER = ('{"who": "Анна", "kind": "ext", "what": "подготовит презентацию", "due": "к пятнице", "time": "01:10", '
              '"quote": "Анна подготовит презентацию к пятнице"}\n'
              '{"who": "бухгалтерия", "kind": "team", "what": "проверит реквизиты", "due": "до среды", "time": "01:30", '
              '"quote": "пусть бухгалтерия проверит реквизиты"}')

    def test_extract_keeps_kind_quote_and_date(self):
        items = {x["who"]: x for x in self._extract(self.ANSWER)}
        anna, acc = items["Анна"], items["бухгалтерия"]
        self.assertEqual(anna["who_kind"], "part")  # she speaks in the recording, whatever the model said
        self.assertEqual(acc["who_kind"], "team")
        self.assertEqual(anna["due_date"], "2026-10-23")  # "к пятнице" said on Thursday 22 October
        self.assertEqual(acc["due_date"], "2026-10-28")
        self.assertIn("презентацию", anna["quote"])

    def test_changes_by_hand_survive_a_new_extraction(self):
        from app import commitments

        items = {x["who"]: x for x in self._extract(self.ANSWER)}
        commitments.update(items["бухгалтерия"]["id"], {"who": "Ковалёва", "who_kind": "ext", "due_date": "2026-10-30"})
        manual = commitments.create("Позвонить подрядчику", "Анна", "part", "2026-10-27", self.m["id"], 120)
        again = self._extract(self.ANSWER)
        by_what = {x["what"]: x for x in again}
        self.assertEqual(by_what["проверит реквизиты"]["who"], "Ковалёва")
        self.assertEqual(by_what["проверит реквизиты"]["due_date"], "2026-10-30")
        self.assertIn(manual["id"], [x["id"] for x in again])
        self.assertEqual(len(again), 3)  # the edited one is not found twice

    def test_create_update_delete(self):
        from app import commitments

        t = commitments.create("Без записи", "", "ext", "")
        self.assertEqual((t["meeting_id"], t["who_kind"], t["source"], t["status"]), (0, "none", "manual", "open"))
        t = commitments.update(t["id"], {"status": "done"})
        self.assertEqual(t["status"], "done")
        self.assertTrue(t["done_at"])
        self.assertEqual(t["edited"], 0)  # ticking off is not an edit
        t = commitments.update(t["id"], {"due_date": "2026-11-01", "who": "Отдел закупок", "who_kind": "team"})
        self.assertEqual((t["due_date"], t["who_kind"], t["edited"]), ("2026-11-01", "team", 1))
        t = commitments.update(t["id"], {"who": ""})
        self.assertEqual(t["who_kind"], "none")
        with self.assertRaises(ValueError):
            commitments.update(t["id"], {"due_date": "в пятницу"})
        with self.assertRaises(ValueError):
            commitments.create("  ")
        self.assertTrue(commitments.delete(t["id"]))
        with self.assertRaises(LookupError):
            commitments.update(t["id"], {"status": "open"})

    def test_people_and_old_rows(self):
        from app import commitments
        from app.database import _db_lock, get_connection

        commitments._init()
        with _db_lock:  # a row of an older library: no kind, no date, only words
            conn = get_connection()
            conn.execute("INSERT INTO commitments (meeting_id, who, what, due, status, created_at) VALUES (?,?,?,?,?,?)",
                         (self.m["id"], "Олег", "старая задача", "завтра", "open", "2026-10-22T10:00:00"))
            conn.commit()
            conn.close()
        row = next(t for t in commitments.all_tasks() if t["what"] == "старая задача")
        self.assertEqual((row["who_kind"], row["due_date"]), ("part", "2026-10-23"))
        ppl = {p["name"]: p for p in commitments.people()}
        self.assertEqual(ppl["Анна"]["kind"], "part")
        self.assertGreaterEqual(ppl["Олег"]["open"], 1)

    def test_report(self):
        from app import commitments

        tasks = [
            {"what": "сделано", "who": "Анна", "status": "done", "due_date": "2026-10-20"},
            {"what": "просрочено", "who": "Олег", "status": "open", "due_date": "2026-10-21"},
            {"what": "в работе", "who": "", "status": "open", "due_date": "2026-10-24"},
            {"what": "вне периода", "who": "Анна", "status": "open", "due_date": "2026-12-01"},
            {"what": "без срока", "who": "Анна", "status": "open", "due_date": ""},
        ]
        r = commitments.report(tasks, "2026-10-19", "2026-10-25", ["done", "open", "over"], "short", today=THURSDAY)
        self.assertEqual(r["counts"], {"all": 3, "done": 1, "open": 1, "over": 1})
        self.assertIn("Закрыто 1 из 3", r["text"])
        self.assertIn("«просрочено» — Олег", r["text"])
        people = commitments.report(tasks, "2026-10-19", "2026-10-25", ["over"], "people", today=THURSDAY)
        self.assertEqual(len(people["lines"]), 3)
        self.assertIn("без исполнителя", people["text"])
        mail = commitments.report(tasks, "2026-10-19", "2026-10-25", ["done", "open", "over"], "mail", "en", THURSDAY)
        self.assertTrue(mail["title"].startswith("Summary for"))


@unittest.skipIf(main is None, "fastapi and app dependencies not installed")
class TasksApiTest(unittest.TestCase):
    def _client(self):
        c = TestClient(main.app, base_url="http://127.0.0.1:8790")
        c.cookies.set("vs_session_8790", main.SESSION_TOKEN)
        return c

    def test_crud_and_report(self):
        with self._client() as c:
            r = c.post("/api/tasks", json={"what": "Через API", "who": "Отдел качества", "who_kind": "team", "due_date": "2026-10-23"})
            self.assertEqual(r.status_code, 200, r.text)
            tid = r.json()["id"]
            listed = c.get("/api/tasks").json()
            self.assertIn(tid, [t["id"] for t in listed["tasks"]])
            self.assertIn("Отдел качества", [p["name"] for p in listed["people"]])
            self.assertEqual(c.patch(f"/api/tasks/{tid}", json={"status": "done"}).json()["status"], "done")
            self.assertEqual(c.patch(f"/api/tasks/{tid}", json={"due_date": "нет"}).status_code, 400)
            g = c.post("/api/tasks/guess", json={"text": "отделу качества проверить завтра"}).json()
            self.assertEqual(g["who"], "Отдел качества")
            self.assertEqual(g["who_kind"], "team")
            rep = c.post("/api/tasks/report", json={"start": "2026-10-19", "end": "2026-10-25", "use_model": False}).json()
            self.assertGreaterEqual(rep["counts"]["done"], 1)
            self.assertEqual(c.delete(f"/api/tasks/{tid}").status_code, 200)
            self.assertEqual(c.delete(f"/api/tasks/{tid}").status_code, 404)
            self.assertEqual(c.post("/api/tasks/export", json={"fmt": "pdf", "ids": [1]}).status_code, 400)


if __name__ == "__main__":
    unittest.main()
