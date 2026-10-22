import tests  # noqa: F401 - keeps the real user data out of reach (see tests/__init__.py)
import time
import unittest
import wave
from pathlib import Path
from unittest import mock

from app import bench


class WerTest(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(bench.normalize_words("Ёлка, «зелёная»! Цена — 1 000 руб."), ["елка", "зеленая", "цена", "1000", "руб"])

    def test_wer_counts(self):
        r = bench.wer("мама мыла раму утром".split(), "мама мыла рамку".split())
        self.assertEqual((r["sub"], r["del"], r["ins"]), (1, 1, 0))
        self.assertAlmostEqual(r["wer"], 0.5)
        self.assertEqual(bench.wer(["a"], ["a"])["wer"], 0.0)
        self.assertEqual(bench.wer(["a"], ["b", "a", "c"])["ins"], 2)

    def test_fragment_takes_whole_phrases(self):
        segs = [{"start": 0, "end": 50, "text": "раз"}, {"start": 50, "end": 100, "text": "два"},
                {"start": 100, "end": 200, "text": "три"}]
        self.assertEqual(bench.reference_fragment(segs, 120), (0.0, 100.0, "раз два"))
        self.assertEqual(bench.reference_fragment(segs, 0)[2], "раз два три")


class BenchRunTest(unittest.TestCase):
    def test_asr_run_saves_results(self):
        from app.database import create_meeting, get_meeting, init_db, save_meeting_results

        init_db()
        tmp = Path(bench.DATA_DIR) / "bench_src.wav"
        with wave.open(str(tmp), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b"\0\0" * 16000 * 3)
        m = create_meeting(title="Эталон", filename="a.wav", media_path=str(tmp), language="ru", model_name="x",
                           num_speakers_hint=0, source="upload")
        save_meeting_results(m["id"], segments=[{"start": 0.0, "end": 2.0, "text": "Привет, мир. Как дела?", "speaker": "S1"}])
        done = {**get_meeting(m["id"]), "status": "completed"}

        def fake_extract(src, dst, enhance_audio=True):
            Path(dst).write_bytes(Path(src).read_bytes())

        def fake_one(mid, clip, audio, dur, lang):
            text = "привет мир как дела" if mid == "good" else "привет мирр"
            return {"model": mid, "sec": 0.1, "load_sec": 0, "speed": 20, "text": text, "text_full": text}

        bench.clear_history()
        with mock.patch("app.audio_utils.extract_audio_16k_mono", fake_extract), \
             mock.patch.object(bench, "_asr_one", fake_one), mock.patch.object(bench, "busy_reason", return_value=""), \
             mock.patch("app.database.get_meeting", return_value=done):
            bench.start("asr", m["id"], ["good", "bad"], 60)
            for _ in range(100):
                if not bench.status()["running"]:
                    break
                time.sleep(0.05)
        st = bench.status()
        self.assertEqual(st["error"], "")
        by = {r["model"]: r for r in st["results"]}
        self.assertEqual(by["good"]["wer"], 0.0)
        self.assertAlmostEqual(by["bad"]["wer"], 0.75)
        self.assertEqual(st["history"][0]["ref_words"], 4)

    def test_rejects_unfinished_recording(self):
        from app.database import create_meeting, init_db

        init_db()
        m = create_meeting(title="x", filename="a.wav", media_path="a.wav", language="ru", model_name="x",
                           num_speakers_hint=0, source="upload")
        with self.assertRaises(ValueError):
            bench.start("asr", m["id"], ["a"])



class GroundingLanguageTest(unittest.TestCase):
    def test_other_alphabet_is_not_marked(self):
        from app.grounding import UNVERIFIED_MARK, ground_summary

        segs = [{"start": 10.0, "end": 15.0, "text": "We will ship the release on Friday and update the docs"}]
        ru = "- Команда выпустит релиз в пятницу и обновит документацию"
        self.assertEqual(ground_summary(ru, segs), ru)
        en = "- The team will ship the release on Friday\n- Penguins invaded the office kitchen yesterday"
        out = ground_summary(en, segs)
        self.assertIn("[00:10]", out.splitlines()[0])
        self.assertIn(UNVERIFIED_MARK, out.splitlines()[1])


class FfmpegScriptOptionTest(unittest.TestCase):
    def test_old_and_new_ffmpeg(self):
        from app import media_cut

        for help_text, want in (("-filter_complex_script <filename>  deprecated", "-filter_complex_script"),
                                ("-/filter_complex  read from file", "-/filter_complex")):
            media_cut._SCRIPT_OPTION.clear()
            with mock.patch.object(media_cut.subprocess, "run", return_value=mock.Mock(stdout=help_text, stderr="")):
                self.assertEqual(media_cut.script_option("ffmpeg"), want)
        media_cut._SCRIPT_OPTION.clear()


class CleanSlateTest(unittest.TestCase):
    def test_everything_is_released_and_ollama_emptied(self):
        import threading

        loaded = [[{"name": "qwen3:8b"}], []]
        here = threading.get_ident()
        mine, onnx_calls = [], []  # the queue of other tests may release models in its own thread meanwhile
        with mock.patch("app.transcriber.release_whisper_model",
                        side_effect=lambda: mine.append(1) if threading.get_ident() == here else None), \
             mock.patch("app.asr_onnx.release",
                        side_effect=lambda *a, **k: onnx_calls.append((a, k)) if threading.get_ident() == here else None), \
             mock.patch("app.vram.ollama_loaded", side_effect=lambda: loaded[0] if loaded else []) as ol, \
             mock.patch("httpx.Client") as client, mock.patch("app.hardware.clear_vram"), mock.patch.object(bench.time, "sleep",
                                                                                                         side_effect=lambda _s: loaded.pop(0) if loaded and threading.get_ident() == here else None):
            bench.clean_slate()
        self.assertEqual(len(mine), 1)
        self.assertEqual(onnx_calls, [((), {})])
        body = client.return_value.__enter__.return_value.post.call_args.kwargs["json"]
        self.assertEqual(body, {"model": "qwen3:8b", "keep_alive": 0})


if __name__ == "__main__":
    unittest.main()
