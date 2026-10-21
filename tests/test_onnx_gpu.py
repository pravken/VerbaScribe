import tests  # noqa: F401 - keeps the real user data out of reach (see tests/__init__.py)
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from app import asr_onnx, onnx_gpu


class OnnxGpuTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.target = self.tmp / "onnxruntime-gpu"
        self.patches = [mock.patch.object(onnx_gpu, "TARGET", self.target),
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
        (self.target / "onnxruntime" / "capi").mkdir(parents=True)
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
            tmp = Path(cmd[cmd.index("--target") + 1])
            (tmp / "onnxruntime" / "capi").mkdir(parents=True)
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


if __name__ == "__main__":
    unittest.main()
