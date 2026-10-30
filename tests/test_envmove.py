import tests  # noqa: F401 - keeps the real user data out of reach (see tests/__init__.py)
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from app import envmove


class _Dirs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app, self.data = root / "app", root / "data"
        (self.app / ".venv").mkdir(parents=True)
        self.data.mkdir()
        (self.app / "requirements.txt").write_text("x==1\n")

    def tearDown(self):
        self.tmp.cleanup()


class NeededTest(_Dirs):
    """Only the program's own .venv on Python 3.10 moves, and not again right after a failure."""

    def _needed(self, version=(3, 10), prefix=None):
        with mock.patch.object(sys, "platform", "win32"), mock.patch.object(sys, "version_info", version), \
                mock.patch.object(sys, "prefix", str(prefix or self.app / ".venv")), \
                mock.patch.dict(os.environ, {"VERBASCRIBE_NO_ENV_MOVE": ""}):
            return envmove.needed(self.app, self.data)

    def test_the_apps_environment_on_310_moves(self):
        self.assertTrue(self._needed())

    def test_311_and_other_environments_stay(self):
        self.assertFalse(self._needed(version=(3, 11)))
        self.assertFalse(self._needed(prefix=Path(self.tmp.name) / "dev-venv"))

    def test_a_failure_waits_a_day(self):
        (self.data / "updates").mkdir()
        mark = self.data / "updates" / envmove.FAILED_MARK
        mark.write_text("now")
        self.assertFalse(self._needed())
        old = time.time() - envmove.RETRY_AFTER - 60
        os.utime(mark, (old, old))
        self.assertTrue(self._needed())


class MoveTest(_Dirs):
    """The new environment replaces the old one only when every package is installed."""

    def setUp(self):
        super().setUp()
        (self.app / ".venv" / "old.txt").write_text("3.10")
        (self.app / ".venv-new" / "Scripts").mkdir(parents=True)
        (self.app / ".venv-new" / "Scripts" / "python.exe").write_text("")
        (self.data / "logs").mkdir()

    def _move(self, rc):
        with mock.patch("app.envmove.subprocess.run", return_value=mock.Mock(returncode=rc)), \
                mock.patch("app.pyenv.find_uv", return_value="uv"), \
                mock.patch("app.updater._preflight"):
            envmove.move(self.app, self.data, lambda _t: None)

    def test_success_swaps_the_folders(self):
        self._move(0)
        self.assertTrue((self.app / ".venv" / "Scripts" / "python.exe").exists())
        self.assertEqual((self.app / ".venv-old" / "old.txt").read_text(), "3.10")
        self.assertFalse((self.app / ".venv-new").exists())

    def test_a_failed_install_keeps_the_old_environment(self):
        with self.assertRaises(RuntimeError):
            self._move(1)
        self.assertEqual((self.app / ".venv" / "old.txt").read_text(), "3.10")
        self.assertFalse((self.app / ".venv-old").exists())

    def test_cleanup_removes_the_old_environment_on_311(self):
        (self.app / ".venv-old").mkdir()
        with mock.patch.object(sys, "version_info", (3, 11)), \
                mock.patch("app.envmove.threading.Thread") as thread:
            envmove.cleanup(self.app, self.data)
        self.assertEqual(thread.call_args.kwargs["args"], (self.app / ".venv-old",))


class UpdaterKeepsTheEnvironmentsTest(unittest.TestCase):
    def test_new_and_old_environments_are_never_written_by_an_update(self):
        from app import updater

        files = updater.archive_files(["V/.venv-new/x.py", "V/.venv-old/y.py", "V/app/z.py"], Path("C:/app"))
        self.assertEqual([rel for _n, rel in files], ["app/z.py"])


class LockForPython310Test(unittest.TestCase):
    """requirements.txt installs in the Python 3.10 environment an older updater puts it into."""

    def test_the_lock_has_lines_for_both_pythons(self):
        text = (Path(__file__).resolve().parent.parent / "requirements.txt").read_text(encoding="utf-8")
        self.assertIn('; python_version < "3.11"', text)
        self.assertRegex(text, r'\nfilelock==4\.[2-9][.\d]* ; python_version >= "3\.11"')


if __name__ == "__main__":
    unittest.main()
