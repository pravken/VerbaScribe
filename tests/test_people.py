"""The people directory (app/people.py) and the speaker edits of one recording."""
import tests  # noqa: F401 - keeps the real user data out of reach (see tests/__init__.py)
import unittest

try:
    from app import database, people
except ImportError:  # app dependencies are not installed (e.g. in the release CI job)
    people = None


@unittest.skipIf(people is None, "app dependencies not installed")
class PeopleDirectoryTest(unittest.TestCase):
    def setUp(self):
        database.init_db()
        people.init_table()
        with database._db_lock:
            conn = database.get_connection()
            conn.execute("DELETE FROM people_directory")
            conn.execute("DELETE FROM settings WHERE key = 'my_name'")  # who the user is: see people.my_names
            conn.execute("DELETE FROM meetings")
            conn.commit()
            conn.close()

    def _meeting(self, title="Встреча", names=("Мария", "Участник 2"), notes="", texts=None):
        m = database.create_meeting(title, "a.wav", "a.wav")
        spk = {f"SPEAKER_0{i}": {"id": f"SPEAKER_0{i}", "name": n, "color": "#0A84FF"} for i, n in enumerate(names)}
        texts = texts or [f"реплика {i}" for i in range(6)]
        segs = [{"id": i, "start": i * 10, "end": i * 10 + 8, "speaker": f"SPEAKER_0{i % len(names)}", "text": t}
                for i, t in enumerate(texts)]
        database.save_meeting_results(m["id"], segments=segs, speakers=spk)
        database.update_meeting_progress(m["id"], status="completed", progress=100)
        database.update_meeting_fields(m["id"], user_notes=notes)
        return m["id"]

    def _names(self):
        return [e["name"] for e in people.directory()]

    def test_directory_joins_speakers_calendar_and_added(self):
        self._meeting(notes="Участники по календарю: Мария, Олег Петров <oleg@example.com>")
        people.add("Отдел закупок", "dept")
        found = {e["name"]: e for e in people.directory()}
        self.assertIn("Мария", found)
        self.assertEqual(found["Мария"]["recordings"], 1)
        self.assertIn("records", found["Мария"]["sources"])
        self.assertIn("calendar", found["Мария"]["sources"])
        self.assertEqual(found["Олег Петров"]["calendar"], 1)  # the address in <…> is dropped
        self.assertEqual(found["Отдел закупок"]["kind"], "dept")
        self.assertNotIn("Участник 2", found)  # a default name is nobody yet

    def test_merge_keeps_first_and_finds_variants(self):
        self._meeting(names=("Мария Иванова", "Маша"))
        merged = people.merge(["Мария Иванова", "Маша"])
        self.assertEqual(merged["name"], "Мария Иванова")
        self.assertEqual(merged["aliases"], ["Маша"])
        self.assertEqual(merged["recordings"], 2)  # both spellings count for one person
        self.assertNotIn("Маша", self._names())
        self.assertEqual(people.canonical("маша"), "Мария Иванова")
        with self.assertRaises(ValueError):
            people.merge(["Мария Иванова"])

    def test_rename_keeps_old_spelling_and_me_is_one(self):
        people.add("Олег")
        e = people.update("Олег", new_name="Олег Петров", role="Ведущий")
        self.assertEqual((e["name"], e["role"], e["aliases"]), ("Олег Петров", "Ведущий", ["Олег"]))
        people.update("Олег Петров", is_me=True)
        people.update("Анна", is_me=True)
        me = [x["name"] for x in people.directory() if x["is_me"]]
        self.assertEqual(me, ["Анна"])
        with self.assertRaises(ValueError):
            people.add("  ")
        with self.assertRaises(ValueError):
            people.add("Кто-то", "robot")

    def test_me_is_the_my_name_setting(self):
        """«Это я» of the directory writes my_name, which the home page and the tasks read."""
        from app.database import get_all_settings, update_settings

        people.add("Олег")
        people.update("Олег", is_me=True)
        self.assertEqual(get_all_settings()["my_name"], "Олег")
        people.update("Олег", new_name="Олег Петров")
        self.assertEqual(get_all_settings()["my_name"], "Олег Петров")
        people.add("Анна")
        update_settings({"my_name": "Анна"})  # set on the home page: the directory follows
        self.assertEqual([x["name"] for x in people.directory() if x["is_me"]], ["Анна"])
        people.update("Анна", is_me=False)
        self.assertEqual(get_all_settings()["my_name"], "")

    def test_mentions_skip_speakers_and_partial_words(self):
        mid = self._meeting(names=("Мария", "Участник 2"), texts=[
            "Нужно спросить Олега про сроки", "Мария, это к тебе", "Аннотация готова", "Олег обещал прислать", "ок", "ок"])
        people.add("Олег")
        people.add("Анна")
        found = people.mentions(database.get_meeting(mid))
        self.assertEqual([(x["name"], x["count"]) for x in found], [("Олег", 2)])  # not Мария (speaks), not Анна

    def test_merge_and_split_speakers_of_a_recording(self):
        mid = self._meeting(names=("Мария", "Участник 2", "Участник 3"))
        m = people.merge_speakers(mid, "SPEAKER_02", "SPEAKER_01")
        self.assertNotIn("SPEAKER_02", m["speakers"])
        self.assertEqual({s["speaker"] for s in m["segments"]}, {"SPEAKER_00", "SPEAKER_01"})
        own = [i for i, s in enumerate(m["segments"]) if s["speaker"] == "SPEAKER_00"]
        m = people.split_speaker(mid, "SPEAKER_00", own[:1], "Анна")
        new_id = m["segments"][own[0]]["speaker"]
        self.assertNotEqual(new_id, "SPEAKER_00")
        self.assertEqual(m["speakers"][new_id]["name"], "Анна")
        self.assertEqual(m["segments"][own[1]]["speaker"], "SPEAKER_00")
        with self.assertRaises(ValueError):  # all lines would go: nothing would stay
            people.split_speaker(mid, "SPEAKER_00", own[1:], "Кто-то")
        with self.assertRaises(ValueError):
            people.merge_speakers(mid, "SPEAKER_00", "SPEAKER_00")

    def test_speaker_role_and_me(self):
        mid = self._meeting(names=("Мария", "Олег"))
        people.set_speaker_meta(mid, "SPEAKER_00", role="Ведущая", is_me=True)
        m = people.set_speaker_meta(mid, "SPEAKER_01", is_me=True)
        self.assertEqual(m["speakers"]["SPEAKER_00"]["role"], "Ведущая")
        self.assertNotIn("is_me", m["speakers"]["SPEAKER_00"])
        self.assertTrue(m["speakers"]["SPEAKER_01"]["is_me"])
        self.assertEqual([x["name"] for x in people.directory() if x["is_me"]], ["Олег"])


if __name__ == "__main__":
    unittest.main()
