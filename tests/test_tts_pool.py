import tests  # noqa: F401 - keeps the real user data out of reach (see tests/__init__.py)
import threading
import unittest

from app.dub_text import TTS_MAX_WORKERS, TTS_WORKER_VRAM_GB, extra_tts_workers

try:
    from app.dubbing import TtsPool
except ImportError:  # numpy/httpx are not installed (e.g. in the release CI job)
    TtsPool = None


class ExtraWorkersTest(unittest.TestCase):
    def test_auto_fills_free_vram_up_to_the_limit(self):
        one = TTS_WORKER_VRAM_GB + 1.5  # room for one more process (1 GB is kept free)
        self.assertEqual(extra_tts_workers("auto", 1, 3 * TTS_WORKER_VRAM_GB, 32), TTS_MAX_WORKERS - 1)
        self.assertEqual(extra_tts_workers("auto", 1, one, 32), 1)  # one more fits next to the LLM
        self.assertEqual(extra_tts_workers("auto", 1, TTS_WORKER_VRAM_GB - 1, 32), 0)
        self.assertEqual(extra_tts_workers("auto", TTS_MAX_WORKERS, 20.0, 32), 0)
        self.assertEqual(extra_tts_workers("auto", 1, 20.0, 4), 0)  # few cores

    def test_explicit_number_waits_for_vram_only_while_the_llm_is_loaded(self):
        self.assertEqual(extra_tts_workers("3", 1, TTS_WORKER_VRAM_GB + 1.5, 32, respect_vram=True), 1)
        self.assertEqual(extra_tts_workers("3", 2, 2.0, 32), 1)
        self.assertEqual(extra_tts_workers("1", 1, 20.0, 32), 0)


class _FakeWorker:
    def __init__(self, fail_after=None, cancelled=False):
        self.calls = 0
        self.fail_after = fail_after
        self._cancelled = cancelled
        self.closed = False

    def call(self, req, on_progress):
        self.calls += 1
        if self._cancelled:
            raise RuntimeError("cancelled")
        if self.fail_after is not None and self.calls > self.fail_after:
            raise RuntimeError("CUDA out of memory")
        for _ in req["items"]:
            on_progress({"event": "progress"})
        return {"ok": True, "results": [{"id": it["id"], "out": f"{it['id']}.wav", "gen_sec": 0.1} for it in req["items"]], "peak_vram_gb": 3.7}

    def close(self):
        self.closed = True


def _pool(workers):
    pool = TtsPool.__new__(TtsPool)
    pool.workers = workers
    pool.errors = []
    return pool


@unittest.skipIf(TtsPool is None, "app dependencies not installed")
class TtsPoolTest(unittest.TestCase):
    def setUp(self):
        self.items = [{"id": i, "text": f"фраза {i}", "ref": "a.wav"} for i in range(20)]

    def test_all_items_are_synthesized_once(self):
        workers = [_FakeWorker(), _FakeWorker(), _FakeWorker()]
        done = []
        lock = threading.Lock()

        def on_done():
            with lock:
                done.append(1)

        res = _pool(workers).run(self.items, {}, on_done)
        self.assertEqual(sorted(res["results"]), list(range(20)))
        self.assertEqual(len(done), 20)

    def test_failed_process_batch_is_taken_over(self):
        workers = [_FakeWorker(fail_after=1), _FakeWorker()]
        res = _pool(workers).run(self.items, {}, lambda: None)
        self.assertEqual(sorted(res["results"]), list(range(20)))

    def test_all_processes_failing_raises(self):
        with self.assertRaises(RuntimeError):
            _pool([_FakeWorker(fail_after=0)]).run(self.items, {}, lambda: None)

    def test_cancel_stops_the_job(self):
        with self.assertRaises(RuntimeError):
            _pool([_FakeWorker(cancelled=True), _FakeWorker()]).run(self.items, {}, lambda: None)


@unittest.skipIf(TtsPool is None, "app dependencies not installed")
class TtsStreamTest(unittest.TestCase):
    def test_items_submitted_later_and_late_workers_are_used(self):
        pool = _pool([_FakeWorker()])
        stream = pool.stream({}, lambda: None)
        stream.submit([{"id": i, "ref": "a"} for i in range(7)])
        late = _FakeWorker()
        pool.workers.append(late)
        stream.attach(late)
        stream.submit([{"id": i, "ref": "b"} for i in range(7, 30)])
        res = stream.wait()
        self.assertEqual(sorted(res["results"]), list(range(30)))

    def test_wait_without_items_returns_at_once(self):
        stream = _pool([_FakeWorker()]).stream({}, lambda: None)
        self.assertEqual(stream.wait()["results"], {})


if __name__ == "__main__":
    unittest.main()


class DubVoiceModelTest(unittest.TestCase):
    def test_numbers_are_read_in_the_target_language(self):
        try:
            import num2words  # noqa: F401 - in the dubbing environment only
        except ImportError:
            self.skipTest("num2words is installed in .venv-dub only")
        from app.dub_worker import _spell_numbers

        self.assertEqual(_spell_numbers("в 14:30, 3 марта", "ru"), "в четырнадцать тридцать, третьего марта")
        self.assertEqual(_spell_numbers("в 2026 году, к 2030 году", "ru"),
                         "в две тысячи двадцать шестом году, к две тысячи тридцатому году")
        self.assertEqual(_spell_numbers("с 1999 года, 2500 рублей", "ru"),
                         "с тысяча девятьсот девяносто девятого года, две тысячи пятьсот рублей")
        self.assertEqual(_spell_numbers("2026 год, перед 2026 годом", "ru"),
                         "две тысячи двадцать шестой год, перед две тысячи двадцать шестым годом")
        self.assertEqual(_spell_numbers("at 9:05", "en"), "at nine oh five")
        self.assertEqual(_spell_numbers("saa 3", "sw"), "saa 3")  # no such language there: the digits stay

    def test_the_worker_imports_without_its_environment(self):
        """The main app reads its constants; the stream setup happens only when it runs as the worker."""
        import sys

        from app import dub_worker

        self.assertIsNot(sys.stdout, sys.stderr)  # main() alone sends library output to stderr
        self.assertTrue(dub_worker.VOXCPM_REVISION)

    def test_chatterbox_weights_are_offered_for_deletion_only(self):
        import tempfile
        from pathlib import Path
        from unittest import mock

        from app import dub_models

        hub = Path(tempfile.mkdtemp())
        (hub / "models--ResembleAI--chatterbox" / "snapshots" / "x").mkdir(parents=True)
        with mock.patch.object(dub_models, "HF_HUB_DIR", hub):
            items = {i["id"]: i for i in dub_models.weights_status()["items"]}
            self.assertTrue(items["chatterbox"]["legacy"])
            self.assertFalse(items["voxcpm2"]["legacy"])
            with mock.patch("app.dub_setup.is_installed", return_value=True), \
                    mock.patch.object(dub_models.threading, "Thread") as thread:
                dub_models.start_download(["chatterbox"], dub_job_running=False)
            thread.assert_not_called()  # never downloaded again
        with mock.patch.object(dub_models, "HF_HUB_DIR", Path(tempfile.mkdtemp())):
            self.assertNotIn("chatterbox", {i["id"] for i in dub_models.weights_status()["items"]})
