import tests  # noqa: F401 - keeps the real user data out of reach (see tests/__init__.py)
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from app import updater


def _release_zip(path: Path, files: dict) -> None:
    with zipfile.ZipFile(path, "w") as z:
        for name, text in files.items():
            z.writestr(f"VerbaScribe-v9.0.0/{name}", text)


class VersionTest(unittest.TestCase):
    def test_versions_compare_as_numbers(self):
        self.assertTrue(updater.is_newer("v1.10.0", "1.9.3"))
        self.assertTrue(updater.is_newer("2.0", "1.99.99"))
        self.assertFalse(updater.is_newer("v1.0.0", "1.0.0"))
        self.assertFalse(updater.is_newer("garbage", "0.1.0"))

    def test_pre_releases_sort_below_their_release(self):
        self.assertTrue(updater.is_newer("0.2.0-beta.1", "0.1.0"))
        self.assertTrue(updater.is_newer("v0.2.0", "0.2.0-beta.1"))  # beta testers get the final version
        self.assertTrue(updater.is_newer("0.2.0-beta.2", "0.2.0-beta.1"))
        self.assertTrue(updater.is_newer("0.2.0-rc.1", "0.2.0-beta.3"))
        self.assertFalse(updater.is_newer("0.1.0", "0.2.0-beta.1"))  # no downgrade from a beta
        self.assertEqual(updater.parse_version("v1.10.2"), (1, 10, 2, 1, 0, 0))


class ApplyUpdateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app, self.data, self.zip = root / "app", root / "data", root / "update.zip"
        (self.app / "app").mkdir(parents=True)
        (self.app / ".venv").mkdir()
        (self.app / "models").mkdir()
        (self.data / "updates").mkdir(parents=True)
        (self.app / "app" / "main.py").write_text("old main")
        (self.app / ".venv" / "keep.txt").write_text("venv")
        (self.app / "models" / "model.bin").write_text("weights")
        (self.app / "requirements.txt").write_text("same")

    def tearDown(self):
        self.tmp.cleanup()

    def test_program_files_are_replaced_and_user_folders_kept(self):
        _release_zip(self.zip, {"app/main.py": "new main", "app/new.py": "added", "requirements.txt": "same",
                                ".venv/keep.txt": "evil", "models/model.bin": "evil", "../outside.txt": "evil"})
        with mock.patch.object(updater.subprocess, "run") as run:
            updater.apply_update(self.zip, self.app, self.data, lambda _m: None)
        run.assert_not_called()  # requirements.txt did not change
        self.assertEqual((self.app / "app" / "main.py").read_text(), "new main")
        self.assertEqual((self.app / "app" / "new.py").read_text(), "added")
        self.assertEqual((self.app / ".venv" / "keep.txt").read_text(), "venv")
        self.assertEqual((self.app / "models" / "model.bin").read_text(), "weights")
        self.assertFalse((self.app.parent / "outside.txt").exists())

    def test_failed_package_install_restores_the_old_version(self):
        _release_zip(self.zip, {"app/main.py": "new main", "app/new.py": "added", "requirements.txt": "changed"})
        failed = mock.Mock(returncode=1)
        with mock.patch.object(updater.subprocess, "run", return_value=failed):
            with self.assertRaises(RuntimeError):
                updater.apply_update(self.zip, self.app, self.data, lambda _m: None)
        self.assertEqual((self.app / "app" / "main.py").read_text(), "old main")
        self.assertEqual((self.app / "requirements.txt").read_text(), "same")
        self.assertFalse((self.app / "app" / "new.py").exists())


if __name__ == "__main__":
    unittest.main()


class AutoUpdateTest(unittest.TestCase):
    def test_found_update_downloads_and_installs_on_exit(self):
        from unittest import mock
        from app import updater

        st = {"can_install": True, "auto": True, "download": {"active": False, "done": False}}
        with mock.patch.object(updater, "start_download") as dl:
            updater._auto_download(st)
            dl.assert_called_once()
            updater._auto_download({**st, "auto": False})
            dl.assert_called_once()
        with mock.patch.object(updater, "auto_enabled", return_value=True), \
             mock.patch.object(updater, "launch_installer") as li, \
             mock.patch.dict(updater._state, {"download": {"done": True, "path": "x.zip"}}):
            self.assertTrue(updater.install_on_exit())
            li.assert_called_once_with(restart=False)
        with mock.patch.object(updater, "launch_installer") as li, \
             mock.patch.dict(updater._state, {"download": {"done": False, "path": ""}}):
            self.assertFalse(updater.install_on_exit())
            li.assert_not_called()
