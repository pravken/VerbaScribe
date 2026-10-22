import tests  # noqa: F401 - keeps the real user data out of reach (see tests/__init__.py)
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from app import asr_onnx, onnx_gpu


def _fake_build(d: Path) -> None:
    (d / "onnxruntime" / "capi").mkdir(parents=True)
    (d / "onnxruntime" / "__init__.py").write_text("", encoding="utf-8")


class OnnxGpuTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.target = self.tmp / "onnxruntime-gpu"
        self.patches = [mock.patch.object(onnx_gpu, "TARGET", self.target),
                        mock.patch.object(onnx_gpu, "NEW", self.tmp / "onnxruntime-gpu.new"),
                        mock.patch.object(onnx_gpu, "REMOVE_MARK", self.tmp / "onnxruntime-gpu.remove"),
                        mock.patch.dict(onnx_gpu._state, {"installing": False, "error": "", "activated": False})]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        while str(self.target) in sys.path:
            sys.path.remove(str(self.target))

    def test_activate_only_before_import(self):
        self.assertFalse(onnx_gpu.activate())  # not installed
        _fake_build(self.target)
        with mock.patch.dict(sys.modules, {"onnxruntime": types.ModuleType("onnxruntime")}):
            self.assertFalse(onnx_gpu.activate())  # the CPU build is loaded already
        saved = sys.modules.pop("onnxruntime", None)
        try:
            self.assertTrue(onnx_gpu.activate())
            self.assertEqual(sys.path[0], str(self.target))
        finally:
            if saved is not None:
                sys.modules["onnxruntime"] = saved

    def test_install_goes_to_its_own_folder(self):
        def fake_run(cmd):
            _fake_build(Path(cmd[cmd.index("--target") + 1]))
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(onnx_gpu, "_cpu_version", return_value="1.23.2"), \
             mock.patch.object(asr_onnx, "_run", side_effect=fake_run) as run, \
             mock.patch.object(asr_onnx, "_install_commands", side_effect=lambda args: iter([["uv", "pip", "install", *args]])), \
             mock.patch.object(onnx_gpu, "activate"):
            onnx_gpu._install()
        cmd = run.call_args[0][0]
        self.assertIn("onnxruntime-gpu==1.23.2", cmd)
        self.assertIn("--no-deps", cmd)
        self.assertTrue(onnx_gpu.installed())
        self.assertEqual(onnx_gpu._state["error"], "")

    def test_remove_of_a_loaded_build_finishes_at_start(self):
        _fake_build(self.target)
        with mock.patch.object(onnx_gpu, "_rmtree", return_value=False):  # its DLLs are locked
            onnx_gpu.remove()
        self.assertTrue(onnx_gpu.REMOVE_MARK.exists())
        self.assertFalse(onnx_gpu.installed())
        saved = sys.modules.pop("onnxruntime", None)
        try:
            self.assertFalse(onnx_gpu.activate())  # the next start: deleted, not put on sys.path
        finally:
            if saved is not None:
                sys.modules["onnxruntime"] = saved
        self.assertFalse(self.target.exists())
        self.assertFalse(onnx_gpu.REMOVE_MARK.exists())

    def test_job_uses_gpu_only_with_room(self):
        with mock.patch.object(onnx_gpu, "enabled", return_value=True), \
             mock.patch("app.vram.gpu_memory", return_value={"free_gb": 2.0, "used_gb": 1, "total_gb": 3}):
            self.assertTrue(onnx_gpu.gpu_for_job("gigaam-v3"))     # needs 1.5 GB
            self.assertFalse(onnx_gpu.gpu_for_job("parakeet-v3"))  # needs 3 GB
        with mock.patch.object(onnx_gpu, "enabled", return_value=False):
            self.assertFalse(onnx_gpu.gpu_for_job("gigaam-v3"))


class OnnxLoadProvidersTest(unittest.TestCase):
    def test_gpu_and_cpu_copies(self):
        fake = types.ModuleType("onnx_asr")
        model = mock.Mock()
        model.with_vad.return_value.with_timestamps.return_value = "asr"
        fake.load_model = mock.Mock(return_value=model)
        fake.load_vad = mock.Mock(return_value="vad")
        with mock.patch.dict(sys.modules, {"onnx_asr": fake}), mock.patch.object(asr_onnx, "ensure_package"), \
             mock.patch.object(asr_onnx, "fetch"), mock.patch.object(onnx_gpu, "prepare_cuda") as prep, \
             mock.patch.dict(asr_onnx._loaded, {}, clear=True):
            asr_onnx._load("gigaam-v3", gpu=True)
            asr_onnx._load("gigaam-v3")
            self.assertEqual(asr_onnx.loaded_models(), ["gigaam-v3"])
            self.assertTrue(asr_onnx.on_gpu("gigaam-v3"))
            asr_onnx.release_gpu()
            self.assertFalse(asr_onnx.on_gpu("gigaam-v3"))
            self.assertEqual(asr_onnx.loaded_models(), ["gigaam-v3"])
        prep.assert_called_once()
        gpu_providers = fake.load_model.call_args_list[0].kwargs["providers"]
        self.assertEqual(gpu_providers[0][0], "CUDAExecutionProvider")
        self.assertEqual(fake.load_model.call_args_list[1].kwargs["providers"], ["CPUExecutionProvider"])
        self.assertEqual(fake.load_vad.call_args_list[0].kwargs["providers"], ["CPUExecutionProvider"])


class OnnxTextTest(unittest.TestCase):
    def test_unk_is_cleaned(self):
        self.assertEqual(asr_onnx.clean_text("Всё ид<unk>т по плану <unk> да"), "Всё идет по плану да")
        self.assertEqual(asr_onnx.clean_text("обычный текст"), "обычный текст")

    def test_quiet_sound_is_raised(self):
        import numpy as np

        quiet = (np.sin(np.linspace(0, 200, 16000)) * 0.05).astype(np.float32)
        self.assertAlmostEqual(float(np.abs(asr_onnx.level(quiet)).max()), 0.9, places=1)
        loud = quiet * 10
        self.assertTrue(np.array_equal(asr_onnx.level(loud), loud))
        silence = np.zeros(16000, dtype=np.float32) + 0.0005
        self.assertTrue(np.array_equal(asr_onnx.level(silence), silence))

    def test_word_ends_keep_pauses(self):
        # "да" at 0.0, a pause, "нет" at 3.0: the first word ends soon after its token, not at 3.0
        words = asr_onnx.words_from_text("да нет", ["▁да", "▁нет"], [0.0, 3.0], 0.0, 4.0)
        self.assertLess(words[0]["end"], 1.0)


class ClearResultsTest(unittest.TestCase):
    def test_transcript_and_analysis_go_sound_and_notes_stay(self):
        from app.database import clear_results, create_meeting, get_meeting, init_db, save_meeting_results, update_meeting_fields

        init_db()
        m = create_meeting("Встреча", "c.m4a", "/tmp/c.m4a")
        save_meeting_results(m["id"], segments=[{"id": 0, "start": 0, "end": 1, "text": "текст", "speaker": "S1"}],
                             speakers={"S1": {"name": "Анна"}}, summaries={"protocol": "## Итоги"})
        update_meeting_fields(m["id"], user_notes="мои заметки")
        self.assertTrue(clear_results(m["id"]))
        got = get_meeting(m["id"])
        self.assertEqual((got["segments"], got["speakers"], got["summaries"], got["status"]), ([], {}, {}, "cleared"))
        self.assertEqual((got["user_notes"], got["media_path"]), ("мои заметки", "/tmp/c.m4a"))


if __name__ == "__main__":
    unittest.main()
