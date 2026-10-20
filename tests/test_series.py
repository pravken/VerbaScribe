import tests  # noqa: F401 - keeps the real user data out of reach (see tests/__init__.py)
import unittest

from app.series import matching_meeting, people, title_key


def _m(id, title, when, names=()):
    return {"id": id, "title": title, "recorded_at": when,
            "speakers": {f"S{i}": {"name": n} for i, n in enumerate(names, 1)}}


class SeriesMatchTest(unittest.TestCase):
    def test_title_key_ignores_dates(self):
        self.assertEqual(title_key("Планёрка 12.05"), title_key("Планёрка 19.05"))

    def test_people_skip_default_names(self):
        self.assertEqual(people(_m(1, "", "", ["Анна", "Speaker 2"])) - {"speaker 2"}, {"анна"})

    def test_match_by_title_picks_latest_earlier(self):
        new = _m(3, "Планёрка команды 19.05", "2026-05-19")
        others = [_m(1, "Планёрка команды 05.05", "2026-05-05"), _m(2, "Планёрка команды 12.05", "2026-05-12"),
                  _m(4, "Планёрка команды 26.05", "2026-05-26"), _m(5, "Другое", "2026-05-18")]
        self.assertEqual(matching_meeting(new, others)["id"], 2)

    def test_no_match(self):
        self.assertIsNone(matching_meeting(_m(2, "Интервью", "2026-05-02"), [_m(1, "Созвон", "2026-05-01")]))


if __name__ == "__main__":
    unittest.main()


class OnnxEnsurePackageTest(unittest.TestCase):
    def test_installs_when_missing(self):
        from unittest import mock
        from app import asr_onnx

        with mock.patch.object(asr_onnx, "_run", return_value=mock.Mock(returncode=0)) as run, \
             mock.patch("shutil.which", return_value="uv"), \
             mock.patch("importlib.import_module", side_effect=ImportError):
            asr_onnx.ensure_package()
        cmd = run.call_args[0][0]
        self.assertEqual(cmd[:3], ["uv", "pip", "install"])
        self.assertIn(asr_onnx.ONNX_ASR_PIN, cmd)

    def test_fetch_downloads_into_existing_folder(self):
        import tempfile
        from pathlib import Path
        from unittest import mock
        from app import asr_onnx
        from onnx_asr.resolver import Resolver

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "gigaam-v2"
            d.mkdir()  # an earlier failed attempt left an empty folder
            calls = []

            def fake_download(self, quantization, *, local_files_only):
                calls.append(local_files_only)
                Path(self.local_dir).mkdir(parents=True, exist_ok=True)
                for f in self.model_type._get_model_files(quantization).values():
                    p = Path(self.local_dir, f.removeprefix("**/").replace("?", "_"))
                    p.write_text("x")
                return self.local_dir

            with mock.patch.object(asr_onnx, "model_dir", return_value=d), \
                 mock.patch.object(asr_onnx, "_vad_dir", return_value=Path(tmp) / "vad"), \
                 mock.patch.object(Resolver, "_download_model", fake_download):
                asr_onnx.fetch("gigaam-v2")
            self.assertIn(False, calls)
            self.assertTrue(list(d.glob("*.onnx")))
