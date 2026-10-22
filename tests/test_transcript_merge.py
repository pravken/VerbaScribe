import tests  # noqa: F401 - keeps the real user data out of reach (see tests/__init__.py)
import json
import unittest

from app import transcript_merge as tm


def version(*phrases, step=0.5):
    """Segments with timed words: phrases are lists of words, each word `step` seconds long."""
    segs, t = [], 0.0
    for i, words in enumerate(phrases):
        ws = []
        for w in words.split():
            ws.append({"word": " " + w, "start": round(t, 2), "end": round(t + step * 0.8, 2)})
            t += step
        segs.append({"id": i, "start": ws[0]["start"], "end": ws[-1]["end"], "speaker": "SPEAKER_00",
                     "text": " ".join(words.split()), "words": ws})
        t += 1.0
    return {"segments": segs, "speakers": {"SPEAKER_00": {"name": "Анна", "color": "#0A84FF"}}}


def texts(res):
    return [s["text"] for s in res["segments"]]


class MergeTest(unittest.TestCase):
    def test_two_of_three_models_win(self):
        res = tm.merge({
            "gigaam-v3": version("дайте ему пилюли утром", "и вечером тоже"),
            "large-v3": version("дайте ему пили утром", "и вечером тоже"),
            "parakeet-v3": version("дайте ему пилюли утром", "и вечером тоже"),
        }, "ru")
        self.assertEqual(texts(res), ["дайте ему пилюли утром", "и вечером тоже"])
        self.assertEqual(res["disputed"], 0)
        self.assertEqual(res["base"], "gigaam-v3")

    def test_the_majority_overrules_the_base_model(self):
        res = tm.merge({
            "gigaam-v3": version("он принял таблетку"),
            "large-v3": version("он принял таблетки"),
            "parakeet-v3": version("он принял таблетки"),
        }, "ru")
        self.assertEqual(texts(res), ["он принял таблетки"])

    def test_a_word_only_one_model_heard_is_dropped_and_one_two_heard_is_added(self):
        res = tm.merge({
            "gigaam-v3": version("ну я пришёл домой"),
            "large-v3": version("я пришёл домой"),
            "parakeet-v3": version("я пришёл поздно домой"),
            "large-v3-turbo": version("я пришёл поздно домой"),
        }, "ru")
        # "ну": only GigaAM said it — out. "поздно": two models against two, a dispute; without an
        # analysis model the pair with the more accurate one (GigaAM, Whisper Pro) wins
        self.assertEqual(texts(res), ["я пришёл домой"])
        self.assertEqual(res["disputed"], 1)

    def test_two_models_that_differ_ask_the_analysis_model(self):
        asked = []

        def chat(system, user):
            asked.append(user)
            return json.dumps({"choices": [2]})

        res = tm.merge({"gigaam-v3": version("купи хлеб и молоко"), "large-v3": version("купи хлеб и малако")}, "ru", chat)
        self.assertEqual(res["disputed"], 1)
        self.assertEqual(res["by_llm"], 1)
        self.assertIn("«молоко»", asked[0])
        self.assertIn("купи хлеб и [?]", asked[0])
        self.assertEqual(texts(res), ["купи хлеб и малако"])
        word = res["segments"][0]["words"][-1]
        self.assertEqual(sorted(a["text"] for a in word["alts"]), ["малако", "молоко"])

    def test_without_an_analysis_model_the_more_accurate_one_wins(self):
        res = tm.merge({"large-v3": version("купи хлеб и малако"), "gigaam-v3": version("купи хлеб и молоко")}, "ru")
        self.assertEqual(texts(res), ["купи хлеб и молоко"])
        self.assertIn("alts", res["segments"][0]["words"][-1])

    def test_an_answer_out_of_range_keeps_the_accurate_variant(self):
        res = tm.merge({"gigaam-v3": version("купи хлеб и молоко"), "large-v3": version("купи хлеб и малако")}, "ru",
                       lambda s, u: '{"choices": [7]}')
        self.assertEqual(texts(res), ["купи хлеб и молоко"])
        self.assertEqual(res["by_llm"], 0)

    def test_phrases_split_differently_are_lined_up_by_time(self):
        base = version("раз два три", "четыре пять шесть")
        other = version("раз два три четыре пять шесть")
        # the same words at about the same times, one phrase instead of two
        for w, b in zip(other["segments"][0]["words"], [w for s in base["segments"] for w in s["words"]]):
            w["start"], w["end"] = b["start"] + 0.1, b["end"] + 0.1
        third = json.loads(json.dumps(base))
        res = tm.merge({"gigaam-v3": base, "large-v3": other, "parakeet-v3": third}, "ru")
        self.assertEqual(texts(res), ["раз два три", "четыре пять шесть"])
        self.assertEqual(res["disputed"], 0)

    def test_punctuation_and_case_do_not_make_a_dispute(self):
        res = tm.merge({"gigaam-v3": version("Привет, мир."), "large-v3": version("привет мир")}, "ru")
        self.assertEqual(texts(res), ["Привет, мир."])
        self.assertEqual(res["disputed"], 0)

    def test_one_model_is_not_enough(self):
        with self.assertRaises(ValueError):
            tm.merge({"gigaam-v3": version("текст"), "merged": version("текст")}, "ru")

    def test_english_takes_parakeet_as_the_base(self):
        res = tm.merge({"large-v3": version("hello there"), "parakeet-v3": version("hello there")}, "en")
        self.assertEqual(res["base"], "parakeet-v3")

    def test_choosing_another_variant(self):
        res = tm.merge({"gigaam-v3": version("купи хлеб и молоко"), "large-v3": version("купи хлеб и малако")}, "ru")
        seg = res["segments"][0]
        tm.choose_alternative(seg, 3, "малако")
        self.assertEqual(seg["text"], "купи хлеб и малако")
        self.assertNotIn("alts", seg["words"][3])
        with self.assertRaises(ValueError):
            tm.choose_alternative(seg, 3, "кефир")


try:
    from app import transcriber
except ImportError:  # app dependencies are not installed (e.g. in the release CI job)
    transcriber = None


@unittest.skipIf(transcriber is None, "app dependencies not installed")
class MergeJobTest(unittest.TestCase):
    """The merge job: the versions of a recording become the version "merged", shown; they all stay."""

    def setUp(self):
        from unittest import mock

        from app import database

        self.db = database
        database.init_db()
        self.mid = database.create_meeting(title="Сведение", filename="s.wav", media_path="s.wav")["id"]
        self.addCleanup(database.delete_meeting, self.mid)
        for model, text in (("large-v3", "купи хлеб и малако"), ("gigaam-v3", "купи хлеб и молоко")):
            v = version(text)
            database.stash_transcript_for(self.mid, model)
            database.save_meeting_results(self.mid, speakers=v["speakers"], segments=v["segments"], language="ru",
                                          transcript_model=model)
        p = mock.patch("app.llm.target", side_effect=RuntimeError("Ollama не запущена"))
        p.start()
        self.addCleanup(p.stop)

    def test_the_merged_text_is_shown_and_the_versions_stay(self):
        done = transcriber._merge_runner(self.mid)
        m = self.db.get_meeting(self.mid)
        self.assertEqual(m["transcript_model"], "merged")
        self.assertEqual([s["text"] for s in m["segments"]], ["купи хлеб и молоко"])
        self.assertEqual(sorted(v["model"] for v in m["transcript_versions"]), ["gigaam-v3", "large-v3"])
        self.assertIn("спорных мест 1", done)
        self.assertIn("Модель анализа недоступна", done)
        # merged again (from the merged one shown): the versions of the models are used, not itself
        transcriber._merge_runner(self.mid)
        m = self.db.get_meeting(self.mid)
        self.assertEqual(sorted(v["model"] for v in m["transcript_versions"]), ["gigaam-v3", "large-v3"])

    def test_a_disputed_word_can_take_the_other_variant(self):
        from fastapi.testclient import TestClient

        from app import main

        transcriber._merge_runner(self.mid)
        self.db.update_meeting_progress(self.mid, status="completed")
        seg = self.db.get_meeting(self.mid)["segments"][0]
        # Without "with": the app's start-up (queue restart, background loops) is not needed here
        client = TestClient(main.app, base_url="http://127.0.0.1:8790")
        client.get(f"/?token={main.SESSION_TOKEN}")
        r = client.post(f"/api/meetings/{self.mid}/segments/{seg['id']}/alternative", json={"word": 3, "text": "малако"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["segments"][0]["text"], "купи хлеб и малако")


if __name__ == "__main__":
    unittest.main()
