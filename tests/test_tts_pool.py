import threading
import unittest

from app.dub_text import TTS_MAX_WORKERS, choose_tts_workers

try:
    from app.dubbing import TtsPool
except ImportError:  # numpy/httpx are not installed (e.g. in the release CI job)
    TtsPool = None


class ChooseWorkersTest(unittest.TestCase):
    def test_auto_follows_free_vram(self):
        self.assertEqual(choose_tts_workers("auto", 20.0, 32), TTS_MAX_WORKERS)
        self.assertEqual(choose_tts_workers("auto", 10.5, 32), 2)
        self.assertEqual(choose_tts_workers("auto", 3.0, 32), 1)
        self.assertEqual(choose_tts_workers("auto", 0.0, 32), 1)

    def test_auto_is_limited_by_cpu(self):
        self.assertEqual(choose_tts_workers("auto", 20.0, 4), 1)

    def test_explicit_setting_is_clamped(self):
        self.assertEqual(choose_tts_workers("2", 0.0, 1), 2)
        self.assertEqual(choose_tts_workers("9", 0.0, 1), TTS_MAX_WORKERS)
        self.assertEqual(choose_tts_workers("0", 0.0, 1), 1)


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


if __name__ == "__main__":
    unittest.main()
