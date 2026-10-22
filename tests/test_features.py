import tests  # noqa: F401 - keeps the real user data out of reach (see tests/__init__.py)
import unittest

from app.translation import build_cues, cues_to_srt, cues_to_vtt


def _words(text, start, step=0.5):
    return [{"word": " " + w, "start": start + i * step, "end": start + i * step + 0.4} for i, w in enumerate(text.split())]


class SubtitleCueTest(unittest.TestCase):
    def setUp(self):
        text = ("Сегодня мы обсуждаем релиз. Его нужно выпустить до пятницы, иначе клиенты будут недовольны, "
                "а мы потеряем ещё одну неделю на согласования с отделом качества.")
        self.seg = {"id": 1, "start": 10.0, "end": 30.0, "speaker": "S1", "text": text, "words": _words(text, 10.0)}

    def test_original_is_cut_by_word_timings(self):
        cues = build_cues([self.seg], None, variant="original", speakers={"S1": {"name": "Анна"}}, with_speaker=True)
        self.assertGreater(len(cues), 1)
        self.assertTrue(cues[0]["text"].startswith("[Анна] Сегодня"))
        self.assertEqual(cues[0]["start"], 10.0)
        # The second piece starts exactly when its first word is spoken
        first_word = cues[1]["text"].split()[0]
        w = next(w for w in self.seg["words"] if w["word"].strip() == first_word)
        self.assertEqual(cues[1]["start"], w["start"])
        self.assertTrue(all(len(line) <= 50 for c in cues for line in c["text"].split("\n")))

    def test_edited_text_falls_back_to_proportional_split(self):
        seg = {**self.seg, "text": "Совсем другой текст после правки."}
        cues = build_cues([seg], None, variant="original")
        self.assertEqual(cues[0]["start"], 10.0)
        self.assertEqual(cues[-1]["end"], 30.0)

    def test_vtt_and_srt(self):
        cues = [{"start": 1.5, "end": 3.25, "text": "Привет"}]
        vtt = cues_to_vtt(cues)
        self.assertTrue(vtt.startswith("WEBVTT\n\n00:00:01.500 --> 00:00:03.250\nПривет"))
        self.assertIn("1\n00:00:01,500 --> 00:00:03,250\nПривет", cues_to_srt(cues))


class HotkeyTest(unittest.TestCase):
    def test_parse(self):
        from app.hotkey import MOD_ALT, MOD_CONTROL, MOD_SHIFT, parse_hotkey

        self.assertEqual(parse_hotkey("Ctrl+Alt+R"), (MOD_CONTROL | MOD_ALT, ord("R")))
        self.assertEqual(parse_hotkey("ctrl + shift + f9"), (MOD_CONTROL | MOD_SHIFT, 0x78))
        self.assertEqual(parse_hotkey("Alt+5"), (MOD_ALT, ord("5")))
        for bad in ("", "R", "Shift+R", "Ctrl+Alt", "Ctrl+Я", "Hyper+R", "Ctrl+F25"):
            self.assertIsNone(parse_hotkey(bad), bad)


class CallWatchTest(unittest.TestCase):
    def test_matching(self):
        from app.call_watch import matching_app

        self.assertEqual(matching_app(["msedgewebview2.exe", "zoom.exe"], ["teams", "zoom"]), "zoom")
        self.assertEqual(matching_app(["msteams_8wekyb3d8bbwe"], ["zoom", "teams"]), "teams")
        self.assertEqual(matching_app(["telegrammessengerllp.telegramdesktop_t4vj0pshhgkwm"], ["telegram"]), "telegram")
        self.assertIsNone(matching_app(["zoom.exe"], ["teams"]))  # not watched

    def test_start_after_delay_stop_after_grace(self):
        from app.call_watch import START_DELAY_SEC, STOP_GRACE_SEC, CallWatcher

        sent = []
        w = CallWatcher(publish=sent.append)
        w.tick("telegram", 0)
        w.tick(None, 2)  # a short voice message: no call
        w.tick("zoom", 10)
        w.tick("zoom", 10 + START_DELAY_SEC - 1)
        self.assertEqual(sent, [])
        w.tick("zoom", 10 + START_DELAY_SEC)
        self.assertEqual([(e["state"], e["app"]) for e in sent], [("started", "zoom")])
        t = 20.0
        w.tick(None, t)                          # muted or switching devices for a moment
        w.tick("zoom", t + STOP_GRACE_SEC - 1)   # back: the call goes on
        w.tick(None, t + STOP_GRACE_SEC)
        w.tick(None, t + 2 * STOP_GRACE_SEC - 0.5)
        self.assertEqual(len(sent), 1)
        w.tick(None, t + 2 * STOP_GRACE_SEC + 1)
        self.assertEqual([(e["state"], e["app"]) for e in sent], [("started", "zoom"), ("ended", "zoom")])
        self.assertIsNone(w.active)


class DigestTest(unittest.TestCase):
    def setUp(self):
        try:
            from app import digest  # noqa: F401
        except ImportError:
            self.skipTest("app dependencies not installed")
        from app import database as db

        self.db = db
        db.init_db()
        self.folder = db.create_folder("Дайджест")
        self.ids = []
        for i, (title, when) in enumerate([("Старая", "2001-01-01 10:00:00"), ("Свежая", "2999-01-01 10:00:00")]):
            mid = db.create_meeting(title, "a.wav", "/tmp/a.wav", folder_id=self.folder["id"], recorded_at=when)["id"]
            db.save_meeting_results(mid, segments=[{"id": 1, "start": 0, "end": 2, "speaker": "S", "text": f"текст {title}"}],
                                    summaries={"protocol": f"## Обзор {title}"} if i else None)
            db.update_meeting_progress(mid, status="completed", progress=100)
            self.ids.append(mid)

    def tearDown(self):
        for mid in self.ids:
            self.db.delete_meeting(mid)
        self.db.delete_folder(self.folder["id"])

    def _fake(self, cloud=False):
        from app.llm import Target

        sent = []
        tgt = Target("anthropic" if cloud else "ollama", "m", 20000, lambda sys_, user: sent.append(user) or "## Главное\n- x [1]")
        return tgt, sent

    def test_folder_digest_uses_overviews_and_links(self):
        from unittest import mock

        from app.digest import make_digest

        tgt, sent = self._fake()
        with mock.patch("app.llm.target", return_value=tgt):
            out = make_digest(folder_id=self.folder["id"])
        self.assertEqual(out["count"], 2)
        self.assertEqual([s["title"] for s in out["sources"]], ["Старая", "Свежая"])  # oldest first
        self.assertIn("Обзор Свежая", sent[0])          # an overview when there is one
        self.assertIn("текст Старая", sent[0])          # otherwise the transcript
        with mock.patch("app.llm.target", return_value=tgt):
            self.assertEqual(make_digest(folder_id=self.folder["id"], days=30)["count"], 1)  # "future" one only

    def test_local_only_left_out_of_cloud(self):
        from unittest import mock

        from app.digest import make_digest

        self.db.update_meeting_fields(self.ids[0], local_only=1)
        tgt, sent = self._fake(cloud=True)
        with mock.patch("app.llm.target", return_value=tgt):
            out = make_digest(folder_id=self.folder["id"])
        self.assertEqual((out["count"], out["excluded_local"]), (1, 1))
        self.assertNotIn("Старая", sent[0])


class PeopleTest(unittest.TestCase):
    def setUp(self):
        try:
            from app import voice_profiles as vp
        except ImportError:
            self.skipTest("app dependencies not installed")
        from app import database as db

        self.db, self.vp = db, vp
        db.init_db()
        db.update_settings({"voice_profiles": "true"})
        self.voice = [1.0, 0.0, 0.0, 0.0]
        vp.learn("Иван Тестов", self.voice)
        self.mid = db.create_meeting("Старая встреча", "a.wav", "/tmp/a.wav")["id"]
        db.save_meeting_results(self.mid, speakers={
            "SPEAKER_00": {"name": "Участник 1", "embedding": self.voice},
            "SPEAKER_01": {"name": "Мария Тестова", "embedding": [0.0, 1.0, 0.0, 0.0]},
        }, segments=[
            {"id": 1, "start": 0, "end": 30, "speaker": "SPEAKER_00", "text": "a"},
            {"id": 2, "start": 30, "end": 40, "speaker": "SPEAKER_01", "text": "b"},
        ])
        db.update_meeting_progress(self.mid, status="completed", progress=100)

    def tearDown(self):
        self.db.delete_meeting(self.mid)
        for p in self.vp.list_profiles():
            if p["name"] == "Иван Тестов":
                self.vp.delete_profile(p["id"])

    def test_old_recordings_get_known_voices_and_people_are_listed(self):
        out = self.vp.apply_to_library()
        self.assertGreaterEqual(out["speakers"], 1)
        speakers = self.db.get_meeting(self.mid)["speakers"]
        self.assertEqual(speakers["SPEAKER_00"]["name"], "Иван Тестов")
        self.assertEqual(speakers["SPEAKER_01"]["name"], "Мария Тестова")  # a name given by the user stays
        people = {p["name"]: p for p in self.vp.people()}
        ivan = people["Иван Тестов"]
        self.assertTrue(ivan["profile"])
        rec = next(r for r in ivan["recordings"] if r["meeting_id"] == self.mid)
        self.assertEqual(rec["seconds"], 30.0)
        self.assertFalse(people["Мария Тестова"]["profile"])
        self.assertEqual(self.vp.apply_to_library()["meetings"], 0)  # nothing left to name


if __name__ == "__main__":
    unittest.main()


class DeleteWithSourceTest(unittest.TestCase):
    def test_source_file_only_on_request(self):
        import tempfile
        from pathlib import Path

        from app import database as db

        db.init_db()
        for with_source in (False, True):
            src = Path(tempfile.mkdtemp()) / "talk.mp3"
            src.write_bytes(b"x")
            mid = db.create_meeting("Локальная", "talk.mp3", str(src), source="local")["id"]
            db.delete_meeting(mid, with_source=with_source)
            self.assertEqual(src.exists(), not with_source)


class FileNamingTest(unittest.TestCase):
    def test_recording_renamed_by_template(self):
        import tempfile
        from pathlib import Path

        from app import database as db
        from app.naming import base_name, rename_recording_file

        db.init_db()
        d = Path(tempfile.mkdtemp())
        f = d / "a1b2c3d4_Запись.m4a"
        f.write_bytes(b"x")
        mid = db.create_meeting("Планёрка: релиз?", f.name, str(f), source="recording", owns_media=True,
                                recorded_at="2026-10-20 14:05:00")["id"]
        self.assertEqual(base_name(db.get_meeting(mid), "{date} {time} {title}"), "2026-10-20 14-05 Планёрка релиз")
        db.update_settings({"file_name_template": "{date} {title}"})
        new = rename_recording_file(mid)
        self.assertEqual(new.name, "2026-10-20 Планёрка релиз.m4a")
        self.assertTrue(new.exists() and not f.exists())
        self.assertEqual(db.get_meeting(mid)["media_path"], str(new))
        db.delete_meeting(mid)


class OnnxAsrTest(unittest.TestCase):
    def test_tokens_become_timed_words(self):
        from app.asr_onnx import words_from_tokens

        words = words_from_tokens([" При", "вет", ",", " мир", "▁ok"], [0.0, 0.2, 0.3, 0.6, 1.0], 10.0, 11.5)
        self.assertEqual([w["word"] for w in words], [" Привет,", " мир", " ok"])
        self.assertEqual([w["start"] for w in words], [10.0, 10.6, 11.0])
        self.assertEqual(words[-1]["end"], 11.5)

    def test_transcribe_shapes_segments_like_whisper(self):
        from types import SimpleNamespace
        from unittest import mock

        from app import asr_onnx

        res = [SimpleNamespace(start=1.0, end=3.0, text=" Добрый  день ", tokens=[" Добрый", " день"], timestamps=[0.0, 0.8]),
               SimpleNamespace(start=4.0, end=5.0, text="", tokens=[], timestamps=[])]
        import tempfile
        import wave
        from pathlib import Path

        wav = Path(tempfile.mkdtemp()) / "a.wav"
        with wave.open(str(wav), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b"\0\0" * 16000)
        fake = SimpleNamespace(recognize=lambda audio, sample_rate=16000: iter(res))
        seen = []
        with mock.patch.object(asr_onnx, "_load", return_value=fake):
            segs = asr_onnx.transcribe("gigaam-v3", wav, 10.0, seen.append, lambda: None)
        # a word ends a little after its last token (WORD_TAIL_SEC), the last one with the phrase
        self.assertEqual(segs, [{"start": 1.0, "end": 3.0, "text": "Добрый день",
                                 "words": [{"word": " Добрый", "start": 1.0, "end": 1.25, "prob": 1.0},
                                           {"word": " день", "start": 1.8, "end": 3.0, "prob": 1.0}]}])
        self.assertEqual(seen, [0.3])
        self.assertFalse(asr_onnx.supports_language("gigaam-v3", "en"))
        self.assertTrue(asr_onnx.supports_language("parakeet-v3", "de"))


class ThinkingModelTest(unittest.TestCase):
    def test_qwen3_does_not_think_aloud(self):
        from app.llm_text import strip_think, thinking_off

        self.assertEqual(thinking_off("qwen3:8b"), {"think": False})
        self.assertEqual(thinking_off("qwen2.5:7b"), {})
        self.assertEqual(strip_think("<think>hmm, let me see</think>\nОтвет"), "Ответ")
        self.assertEqual(strip_think("<think>cut off in the middle"), "")


class AnalyticsTest(unittest.TestCase):
    def test_talk_time_turns_interruptions(self):
        from app.analytics import conversation_analytics

        seg = lambda s, e, spk, text: {"start": s, "end": e, "speaker": spk, "text": text}
        m = {"duration": 100, "speakers": {"A": {"name": "Анна"}, "B": {"name": "Борис"}},
             "segments": [seg(0, 30, "A", "Начнём. Всё готово?"), seg(31, 40, "A", "Да."), seg(39, 50, "B", "Нет, подожди"),
                          seg(60, 70, "A", "Ладно")]}
        a = conversation_analytics(m)
        anna, boris = a["speakers"]
        self.assertEqual((anna["name"], anna["seconds"], anna["turns"], anna["questions"]), ("Анна", 49.0, 2, 1))
        self.assertEqual((boris["interruptions"], boris["turns"]), (1, 1))
        self.assertEqual(a["turns"], 3)
        self.assertEqual(anna["longest"], 40.0)  # 0-40: one turn across a 1 s pause
        self.assertEqual(a["silence_share"], 40.0)


class WatchFolderTest(unittest.TestCase):
    def test_only_new_files_once_they_stop_growing(self):
        import tempfile
        from pathlib import Path

        from app import database as db
        from app import watch_folder as wf

        try:
            import app.main  # noqa: F401 (SUPPORTED_EXTENSIONS)
        except ImportError:
            self.skipTest("app dependencies not installed")
        db.init_db()
        d = Path(tempfile.mkdtemp())
        (d / "old.mp3").write_bytes(b"x" * 10)
        db.update_settings({"watch_folder": str(d), "watch_folder_baseline": ""})
        added = []
        self.assertEqual(wf.scan_once(added.append, now=0), 0)  # first look: what is there is the baseline
        new = d / "new.m4a"
        new.write_bytes(b"x" * 10)
        (d / "notes.txt").write_text("no")
        self.assertEqual(wf.scan_once(added.append, now=100), 0)  # just appeared: wait until it stops growing
        new.write_bytes(b"x" * 20)
        self.assertEqual(wf.scan_once(added.append, now=110), 0)  # still growing
        self.assertEqual(wf.scan_once(added.append, now=110 + wf.STABLE_SEC), 1)
        self.assertEqual([p.name for p in added], ["new.m4a"])
        self.assertEqual(wf.scan_once(added.append, now=1000), 0)  # never twice
        db.update_settings({"watch_folder": "", "watch_folder_baseline": ""})


class MediaCutTest(unittest.TestCase):
    def test_speech_intervals_shorten_pauses(self):
        from app.media_cut import PAD_SEC, speech_intervals

        w = lambda a, b: {"word": " x", "start": a, "end": b}
        segs = [{"text": "a b", "words": [w(1.0, 1.4), w(1.6, 2.0)]}, {"text": "c", "words": [w(10.0, 10.5)]}]
        self.assertEqual(speech_intervals(segs, 20.0), [(1.0 - PAD_SEC, 2.0 + PAD_SEC), (10.0 - PAD_SEC, 10.5 + PAD_SEC)])

    def test_parse_moments(self):
        from app.media_cut import parse_moments

        got = parse_moments("1. 01:05-01:40 | Решили перенести релиз\n- 1:02:00 - 1:02:03 | Задача\nмусор", 4000)
        self.assertEqual(got[0], {"start": 65.0, "end": 100.0, "title": "Решили перенести релиз"})
        self.assertEqual((got[1]["start"], got[1]["end"]), (3720.0, 3728.0))  # stretched to the minimum length

    @unittest.skipUnless(__import__("shutil").which("ffmpeg"), "no ffmpeg on PATH")
    def test_cut_joins_the_kept_parts(self):
        import subprocess
        import tempfile
        from pathlib import Path

        from app.media_cut import run_cut

        d = Path(tempfile.mkdtemp())
        src = d / "talk.wav"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440", "-t", "10", str(src)], check=True)
        seen = []
        out = run_cut("ffmpeg", src, [(1.0, 3.0), (6.0, 7.5)], False, d / "out.m4a", seen.append, lambda: None)
        dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)],
                                   capture_output=True, text=True).stdout)
        self.assertAlmostEqual(dur, 3.5, delta=0.15)


class LiveAskTest(unittest.TestCase):
    def test_question_answered_from_live_captions(self):
        from unittest import mock

        try:
            from fastapi.testclient import TestClient

            from app import live, main
            from app.llm import Target
        except ImportError:
            self.skipTest("fastapi not installed")
        s = live.LiveSession("ru", None)
        s.lines = [{"id": 1, "start": 65.0, "end": 70.0, "text": "Переносим релиз на пятницу", "translation": None}]
        with live._sessions_lock:
            live._sessions[s.id] = s
        sent = []
        tgt = Target("ollama", "m", 20000, lambda system, user: sent.append(user) or "На пятницу [01:05]")
        with mock.patch.object(main, "enqueue_meeting_job"), mock.patch("app.llm.target", return_value=tgt), \
                TestClient(main.app, base_url="http://127.0.0.1:8794") as c:
            c.get(f"/?token={main.SESSION_TOKEN}")
            r = c.post(f"/api/live/{s.id}/ask", json={"question": "Когда релиз?"})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["answer"], "На пятницу [01:05]")
            self.assertIn("[01:05] Переносим релиз на пятницу", sent[0])
            self.assertEqual(c.post("/api/live/nope/ask", json={"question": "x"}).status_code, 404)
        live.stop_session(s.id)


class SideJobStatusTest(unittest.TestCase):
    """A job on a finished recording gives back its status: a cancelled one must not become completed."""

    def test_status_is_returned(self):
        from app import transcriber
        from app.database import create_meeting, init_db, meeting_status, update_meeting_progress

        init_db()
        for status in ("cancelled", "failed", "completed"):
            m = create_meeting(title=f"Задание после {status}", filename="a.wav", media_path="/nonexistent/a.wav")
            update_meeting_progress(m["id"], status=status)
            # a cut of a missing file fails: whatever the outcome, the status is the one before
            self.assertTrue(transcriber.enqueue_cut_job(m["id"], "denoise", [(0.0, 1.0)], False, "/nonexistent/out.wav"))
            transcriber._executor.submit(lambda: None).result(timeout=30)
            self.assertEqual(meeting_status(m["id"]), status)
