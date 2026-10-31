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
            z.writestr(f"Verbaliox-v9.0.0/{name}", text)


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


class BetaChannelTest(unittest.TestCase):
    """"Get beta versions": the newest of the recent releases, pre-releases too; off: releases/latest."""

    def _check(self, beta):
        import httpx

        from app import database

        database.init_db()
        database.update_settings({"update_beta": "true" if beta else "false", "update_check": "true", "update_auto": "false"})
        self.addCleanup(database.update_settings, {"update_beta": "false"})
        asked = []

        def answer(req):
            asked.append(str(req.url))
            if req.url.path.endswith("/latest"):
                return httpx.Response(200, json={"tag_name": "v0.5.0", "assets": []})
            return httpx.Response(200, json=[
                {"tag_name": "v0.5.0", "assets": []},
                {"tag_name": "v0.6.0-beta.2", "prerelease": True, "assets": []},
                {"tag_name": "v0.6.0-beta.3", "draft": True, "assets": []},  # not published yet
                {"tag_name": "v0.6.0-beta.1", "prerelease": True, "assets": []},
            ])

        real = httpx.Client
        with mock.patch("httpx.Client", side_effect=lambda **kw: real(transport=httpx.MockTransport(answer), **kw)):
            latest = updater.check(force=True)["latest"]
        return latest["version"], asked

    def test_beta_takes_the_newest_published_pre_release(self):
        version, asked = self._check(beta=True)
        self.assertEqual(version, "0.6.0-beta.2")
        self.assertIn("/releases?per_page=30", asked[0])

    def test_without_beta_only_the_latest_release(self):
        version, asked = self._check(beta=False)
        self.assertEqual(version, "0.5.0")
        self.assertTrue(asked[0].endswith("/releases/latest"))

    def test_a_beta_tester_is_offered_the_final_release(self):
        self.assertTrue(updater.is_newer("0.6.0", "0.6.0-beta.2"))


class ReleaseNotesTest(unittest.TestCase):
    """The text of a release page comes from the version's sections of CHANGELOG.md."""

    def test_a_version_and_its_betas_get_both_languages_english_first(self):
        import importlib.util

        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location("release_notes", root / "tools" / "release_notes.py")
        rn = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(rn)
        text = "# Log\n\n## 1.2.0\nру\n\n---\n\n## 1.2.0 — English\nen\n\n## 1.1.0\nold\n"
        self.assertEqual(rn.notes("v1.2.0-beta.2", text), "## 1.2.0 — English\nen\n\n## 1.2.0\nру")
        self.assertEqual(rn.notes("v1.3.0", text), "")
        # the changelog of this repository has the section of the version in app/__init__.py
        from app import __version__

        self.assertIn(f"## {__version__.split('-')[0]}", rn.notes(__version__, (root / "CHANGELOG.md").read_text(encoding="utf-8")))


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
        # requirements.txt did not change: no packages installed (the queue of other tests may run FFmpeg
        # meanwhile through the same patched subprocess.run, so only the updater's own calls count)
        self.assertFalse([c for c in run.call_args_list if "requirements.txt" in " ".join(map(str, c.args[0] if c.args else []))])
        self.assertEqual((self.app / "app" / "main.py").read_text(), "new main")
        self.assertEqual((self.app / "app" / "new.py").read_text(), "added")
        self.assertEqual((self.app / ".venv" / "keep.txt").read_text(), "venv")
        self.assertEqual((self.app / "models" / "model.bin").read_text(), "weights")
        self.assertFalse((self.app.parent / "outside.txt").exists())

    def test_names_outside_the_program_folder_are_left_out(self):
        names = ["Verbaliox-v9/app/main.py", "Verbaliox-v9/C:/Users/x/Startup/evil.bat", "Verbaliox-v9//etc/evil",
                 "Verbaliox-v9/../evil.txt", "Verbaliox-v9/app/../../evil.txt", "Verbaliox-v9/.VENV/x.py",
                 "Verbaliox-v9\\app\\new.py", "Verbaliox-v9/app/x.py:stream", "Verbaliox-v9/models/m.bin"]
        self.assertEqual(updater.archive_files(names, self.app),
                         [("Verbaliox-v9/app/main.py", "app/main.py"), ("Verbaliox-v9\\app\\new.py", "app/new.py")])

    def test_failed_package_install_restores_the_old_version(self):
        _release_zip(self.zip, {"app/main.py": "new main", "app/new.py": "added", "requirements.txt": "changed"})
        failed = mock.Mock(returncode=1)
        with mock.patch.object(updater.subprocess, "run", return_value=failed):
            with self.assertRaises(RuntimeError):
                updater.apply_update(self.zip, self.app, self.data, lambda _m: None)
        self.assertEqual((self.app / "app" / "main.py").read_text(), "old main")
        self.assertEqual((self.app / "requirements.txt").read_text(), "same")
        self.assertFalse((self.app / "app" / "new.py").exists())

    def test_failed_restore_is_reported_as_broken(self):
        _release_zip(self.zip, {"app/main.py": "new main", "requirements.txt": "changed"})
        with mock.patch.object(updater.subprocess, "run", return_value=mock.Mock(returncode=1)), \
                mock.patch.object(updater.zipfile.ZipFile, "extractall", side_effect=PermissionError("locked")):
            with self.assertRaises(updater.UpdateFailed) as cm:
                updater.apply_update(self.zip, self.app, self.data, lambda _m: None)
        self.assertEqual(cm.exception.outcome, "broken")

    def test_files_the_new_version_dropped_are_removed_and_come_back_on_failure(self):
        (self.app / "app" / "gone.py").write_text("old module")
        (self.app / "app" / "notes.txt").write_text("not code")
        _release_zip(self.zip, {"app/main.py": "new main", "requirements.txt": "same"})
        with mock.patch.object(updater.subprocess, "run"):
            updater.apply_update(self.zip, self.app, self.data, lambda _m: None)
        self.assertFalse((self.app / "app" / "gone.py").exists())
        self.assertTrue((self.app / "app" / "notes.txt").exists())

        (self.app / "app" / "gone.py").write_text("old module")
        _release_zip(self.zip, {"app/main.py": "newer main", "requirements.txt": "changed"})
        with mock.patch.object(updater.subprocess, "run", return_value=mock.Mock(returncode=1)):
            with self.assertRaises(updater.UpdateFailed) as cm:
                updater.apply_update(self.zip, self.app, self.data, lambda _m: None)
        self.assertEqual(cm.exception.outcome, "restored")
        self.assertEqual((self.app / "app" / "gone.py").read_text(), "old module")
        self.assertEqual((self.app / "app" / "main.py").read_text(), "new main")

    def test_full_disk_stops_before_any_file_changes(self):
        _release_zip(self.zip, {"app/main.py": "new main", "requirements.txt": "same"})
        with mock.patch.object(updater.shutil, "disk_usage", return_value=mock.Mock(free=1000)):
            with self.assertRaises(updater.UpdateFailed) as cm:
                updater.apply_update(self.zip, self.app, self.data, lambda _m: None)
        self.assertEqual(cm.exception.outcome, "untouched")
        self.assertIn("места", str(cm.exception))
        self.assertEqual((self.app / "app" / "main.py").read_text(), "old main")

    def test_changed_dub_requirements_mark_the_dub_environment_outdated(self):
        from app import dub_setup

        (self.app / "requirements-dub.txt").write_text("torch==1")
        marker = self.app / ".venv-dub" / "verbaliox-dub-ready.txt"
        marker.parent.mkdir()
        marker.write_text("Verified 2026-01-01: cuda=True\n")  # made before marks recorded requirements
        _release_zip(self.zip, {"app/main.py": "new main", "requirements.txt": "same", "requirements-dub.txt": "torch==2"})
        with mock.patch.object(updater.subprocess, "run"):
            updater.apply_update(self.zip, self.app, self.data, lambda _m: None)
        with mock.patch.object(dub_setup, "DUB_READY_MARKER", marker), \
                mock.patch.object(dub_setup, "REQUIREMENTS", self.app / "requirements-dub.txt"):
            self.assertTrue(dub_setup.is_outdated())
            self.assertFalse(dub_setup.is_installed())
            marker.write_text(f"Verified\nrequirements={dub_setup._requirements_hash()}\n")
            self.assertFalse(dub_setup.is_outdated())
            self.assertTrue(dub_setup.is_installed())

    def test_an_old_mark_without_voxcpm_is_outdated(self):
        """A folder updated with git keeps the mark of its Chatterbox environment, with no
        requirements in it: without the voxcpm package the environment is not ready."""
        from app import dub_setup

        venv = self.app / ".venv-dub"
        marker = venv / "verbaliox-dub-ready.txt"
        marker.parent.mkdir()
        marker.write_text("Verified 2026-01-01: cuda=True\n")
        with mock.patch.object(dub_setup, "DUB_READY_MARKER", marker), mock.patch.object(dub_setup, "DUB_VENV", venv):
            self.assertTrue(dub_setup.is_outdated())
            self.assertFalse(dub_setup.is_installed())
            (venv / "Lib" / "site-packages" / "voxcpm").mkdir(parents=True)
            self.assertFalse(dub_setup.is_outdated())



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
             mock.patch.object(updater, "is_git_checkout", return_value=False), \
             mock.patch.object(updater, "launch_installer") as li, \
             mock.patch.dict(updater._state, {"download": {"done": True, "path": "x.zip"}}):
            self.assertTrue(updater.install_on_exit())
            li.assert_called_once_with(restart=False)
        with mock.patch.object(updater, "launch_installer") as li, \
             mock.patch.dict(updater._state, {"download": {"done": False, "path": ""}}):
            self.assertFalse(updater.install_on_exit())
            li.assert_not_called()
        # A git clone is never overwritten by an archive (it is updated with git pull)
        with mock.patch.object(updater, "auto_enabled", return_value=True), \
             mock.patch.object(updater, "is_git_checkout", return_value=True), \
             mock.patch.object(updater, "launch_installer") as li, \
             mock.patch.dict(updater._state, {"download": {"done": True, "path": "x.zip"}}):
            self.assertFalse(updater.install_on_exit())
            li.assert_not_called()


class LauncherTitleTest(unittest.TestCase):
    def test_splash_waits_for_the_app_window_title(self):
        import re
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        run_title = re.search(r'^WINDOW_TITLE = "(.+?)"', (root / "run.py").read_text(encoding="utf-8"), re.M).group(1)
        exe_src = (root / "build_exe.py").read_text(encoding="utf-8")
        self.assertIn(f'const string WindowTitle = "{run_title}";', exe_src)
        self.assertNotIn(f'splash.Text = "{run_title}";', exe_src)
        # Explorer showing the program folder carries the same title: neither side may take it for the app
        run_src = (root / "run.py").read_text(encoding="utf-8")
        self.assertIn('"CabinetWClass"', exe_src)
        self.assertIn('"CabinetWClass"', run_src)
        self.assertNotIn("FindWindowW(None, WINDOW_TITLE)", run_src)
        self.assertNotIn("FindWindow(null, WindowTitle)", exe_src)


class StartupFailureTest(unittest.TestCase):
    def test_broken_environment_is_reported(self):
        """An incomplete .venv (a library missing or blocked) says so instead of ending silently."""
        import os
        import subprocess
        import sys

        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "uvicorn"
            fake.mkdir()
            (fake / "__init__.py").write_text("raise ImportError('DLL load failed: blocked by policy')\n")
            env = {**os.environ, "PYTHONPATH": os.pathsep.join([tmp, str(root), os.environ.get("PYTHONPATH", "")]),
                   "VERBALIOX_DATA_DIR": str(Path(tmp) / "data")}
            res = subprocess.run([sys.executable, str(root / "run.py"), "--headless"], capture_output=True, text=True,
                                 encoding="utf-8", env=env, timeout=120, cwd=tmp)
        self.assertEqual(res.returncode, 1)
        self.assertIn("Не удалось запустить Verbaliox", res.stderr)
        self.assertIn("setup.bat", res.stderr)
        self.assertIn("blocked by policy", res.stderr)


class PortChoiceTest(unittest.TestCase):
    """A port taken by another program is skipped; our own running server is reused."""

    def _serve(self, status: int, cookie: str = ""):
        import http.server
        import threading

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(status)
                if cookie:
                    self.send_header("Set-Cookie", cookie)
                self.end_headers()

            def log_message(self, *a):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return srv.server_address[1]

    def test_foreign_program_is_skipped_and_our_server_reused(self):
        import run

        foreign = self._serve(200)
        ours = self._serve(303, "vs_session_1=abc; HttpOnly")
        with mock.patch.object(run, "read_session_token", return_value="tok"):
            self.assertFalse(run.probe_server("127.0.0.1", foreign))
            self.assertTrue(run.probe_server("127.0.0.1", ours))
            port, running = run.choose_port("127.0.0.1", foreign, tries=1)
            self.assertFalse(running)
        with mock.patch.object(run, "read_session_token", return_value="tok"), \
                mock.patch.object(run, "is_port_in_use", side_effect=lambda h, p: p in (foreign, foreign + 1)), \
                mock.patch.object(run, "probe_server", side_effect=lambda h, p: False):
            self.assertEqual(run.choose_port("127.0.0.1", foreign), (foreign + 2, False))
        with mock.patch.object(run, "read_session_token", return_value="tok"):
            self.assertEqual(run.choose_port("127.0.0.1", ours), (ours, True))


class PyEnvTest(unittest.TestCase):
    def test_missing_pip_is_bootstrapped_with_ensurepip(self):
        from app import pyenv

        calls = []
        answers = iter([False, True, True])  # no pip, ensurepip runs, then pip works
        with mock.patch.object(pyenv, "_ok", side_effect=lambda cmd: calls.append(cmd[2]) or next(answers)):
            self.assertTrue(pyenv.ensure_pip("python"))
        self.assertEqual(calls, ["pip", "ensurepip", "pip"])

    def test_uv_is_found_outside_path(self):
        from app import pyenv

        with tempfile.TemporaryDirectory() as home:
            uv = Path(home) / ".cargo" / "bin" / "uv.exe"
            uv.parent.mkdir(parents=True)
            uv.write_text("")
            with mock.patch.object(pyenv.shutil, "which", return_value=None), \
                    mock.patch.dict(pyenv.os.environ, {"USERPROFILE": home, "LOCALAPPDATA": home}):
                self.assertEqual(pyenv.find_uv(), str(uv))


class NativeDialogTest(unittest.TestCase):
    def test_dialog_runs_in_the_program_folder(self):
        from app import native_dialog
        from app.paths import APP_DIR

        done = mock.Mock(returncode=0, stdout="C:/out/file.docx\n", stderr="")
        with mock.patch("subprocess.run", return_value=done) as run:
            self.assertEqual(native_dialog.run(["--mode", "save"]), "C:/out/file.docx")
        # other tests' background jobs may call subprocess.run meanwhile: only the dialog's call counts
        ours = [c for c in run.call_args_list if c.args and "app.native_dialog" in c.args[0]]
        self.assertEqual(ours[0].kwargs["cwd"], str(APP_DIR))
        with mock.patch("subprocess.run", return_value=mock.Mock(returncode=1, stdout="", stderr="No module named app")):
            self.assertIsNone(native_dialog.run(["--mode", "save"]))


class PidFileTest(unittest.TestCase):
    """A stuck instance is ended only when the PID in app.pid still runs the program that wrote it."""

    def test_reused_or_old_pid_is_not_ended(self):
        import run

        with tempfile.TemporaryDirectory() as tmp:
            pid_file = Path(tmp) / "app.pid"
            with mock.patch.object(run, "_pid_file", return_value=pid_file), \
                    mock.patch.object(run, "_process_image", side_effect=lambda pid: {4242: "C:\\py\\python.exe", 77: "C:\\Games\\game.exe"}.get(pid, "")):
                pid_file.write_text("4242\nC:\\py\\python.exe")
                self.assertEqual(run.stuck_instance_pid(), 4242)
                pid_file.write_text("77\nC:\\py\\python.exe")  # the PID now belongs to another program
                self.assertEqual(run.stuck_instance_pid(), 0)
                pid_file.write_text("4242")  # an older app.pid without the program file
                self.assertEqual(run.stuck_instance_pid(), 0)
                pid_file.write_text("999\nC:\\py\\python.exe")  # the process ended
                self.assertEqual(run.stuck_instance_pid(), 0)

                run.write_pid_file()
                self.assertTrue(pid_file.read_text().startswith(f"{run.os.getpid()}\n"))
                run.remove_pid_file()
                self.assertFalse(pid_file.exists())



class ReleaseSignatureTest(unittest.TestCase):
    """With the author's key in the program, an update installs only with its signature for that release."""

    def setUp(self):
        from app import release_sign

        self.rs = release_sign
        self.seed, self.kid = release_sign.new_key()
        self.pub = release_sign.public_key_text(self.seed, self.kid)
        self.sums = b"aa11  Verbaliox-v9.0.0.zip\n"

    def _sig(self, data=None, tag="v9.0.0", seed=None, kid=None):
        return self.rs.sign_file(seed or self.seed, kid or self.kid, data or self.sums, f"tag:{tag} file:SHA256SUMS.txt")

    def test_only_this_release_signed_by_this_key_passes(self):
        updater.check_signature(self.sums, self._sig(), "v9.0.0", key=self.pub)
        other_seed, other_kid = self.rs.new_key()
        for sig, tag, why in ((self._sig(), "v9.0.1", "another release"),
                              ("", "v9.0.0", "unsigned"),
                              (self._sig(data=b"bb22  Verbaliox-v9.0.0.zip\n"), "v9.0.0", "other checksums"),
                              (self._sig(seed=other_seed, kid=other_kid), "v9.0.0", "another key"),
                              (self._sig().replace("tag:v9.0.0", "tag:v9.0.1"), "v9.0.1", "edited comment")):
            with self.assertRaises(RuntimeError, msg=why):
                updater.check_signature(self.sums, sig, tag, key=self.pub)

    def test_download_refuses_an_unsigned_release_once_the_key_is_set(self):
        import hashlib

        body = b"PK fake archive"
        sums = f"{hashlib.sha256(body).hexdigest()}  Verbaliox-v9.0.0.zip\n".encode()
        latest = {"tag": "v9.0.0", "zip_name": "Verbaliox-v9.0.0.zip", "zip_url": "z", "sums_url": "s", "sig_url": "g"}
        sig = self.rs.sign_file(self.seed, self.kid, sums, "tag:v9.0.0 file:SHA256SUMS.txt")

        class Resp:
            def __init__(self, data):
                self.content, self.text, self.headers = data, data.decode(), {"content-length": str(len(data))}

            def raise_for_status(self):
                pass

            def iter_bytes(self, n):
                yield self.content

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def client(served):
            c = mock.MagicMock()
            c.__enter__.return_value.get.side_effect = lambda url: Resp(served[url])
            c.__enter__.return_value.stream.side_effect = lambda method, url: Resp(served[url])
            return c

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(updater, "_updates_dir", return_value=Path(tmp)), \
                mock.patch.object(updater, "RELEASE_PUBLIC_KEY", self.pub):
            for served, ok in (({"s": sums, "g": b"", "z": body}, False),
                               ({"s": sums, "g": sig.encode(), "z": body}, True)):
                with mock.patch("httpx.Client", return_value=client(served)):
                    updater._download(latest if ok else {**latest, "sig_url": None})
                st = updater._state["download"]
                self.assertEqual(st["done"], ok, st)
                self.assertEqual((Path(tmp) / "Verbaliox-v9.0.0.zip").exists(), ok)
                if not ok:
                    self.assertIn("не подписан", st["error"])

    def test_the_app_holds_a_valid_release_key(self):
        from app import release_sign

        key_id, pk = release_sign._key_parts(updater.RELEASE_PUBLIC_KEY)
        self.assertEqual((len(key_id), len(pk)), (8, 32))

    def test_release_key_tool_makes_signatures_the_updater_accepts(self):
        import os
        import subprocess
        import sys

        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            key = Path(tmp) / "release.key"
            out = subprocess.run([sys.executable, str(root / "tools" / "release_key.py"), "generate", "--out", str(key)],
                                 capture_output=True, text=True, check=True)
            pub = out.stdout.strip().splitlines()[-1]
            sums = Path(tmp) / "SHA256SUMS.txt"
            sums.write_bytes(self.sums)
            env = {**os.environ, "RELEASE_SIGNING_KEY": key.read_text().strip()}
            res = subprocess.run([sys.executable, str(root / "tools" / "release_key.py"), "sign", str(sums), "--tag", "v9.0.0"],
                                 capture_output=True, text=True, env=env)
            # The signature is made and checks out with its own key, but this key is not the one in
            # app/updater.py: the release workflow stops instead of publishing what nobody could install
            updater.check_signature(self.sums, (Path(tmp) / "SHA256SUMS.txt.minisig").read_text(), "v9.0.0", key=pub)
            self.assertNotEqual(res.returncode, 0)
            self.assertIn("does not belong to the public key", res.stderr)
            again = subprocess.run([sys.executable, str(root / "tools" / "release_key.py"), "generate", "--out", str(key)],
                                   capture_output=True, text=True)
            self.assertNotEqual(again.returncode, 0)  # a key is never overwritten


if __name__ == "__main__":
    unittest.main()


class AutostartTest(unittest.TestCase):
    """Start with Windows: the Run key starts run.py --minimized with the environment's pythonw."""

    def test_command_and_sync(self):
        from app import autostart, database

        cmd = autostart.command()
        self.assertTrue(cmd.endswith('run.py" --minimized'))
        self.assertIn("pythonw.exe", cmd)
        database.init_db()
        calls = []
        with mock.patch.object(autostart, "set_enabled", side_effect=calls.append), \
                mock.patch.object(autostart, "current", return_value='"D:\\old\\pythonw.exe" "D:\\old\\run.py" --minimized'):
            database.update_settings({"start_with_windows": "true"})
            autostart.sync()  # the folder moved: the key is set again
            database.update_settings({"start_with_windows": "false"})
            autostart.sync()  # turned off: the key goes
        self.assertEqual(calls, [True, False])

    def test_background_install_restarts_in_the_tray(self):
        with mock.patch.object(updater, "_state", {"download": {"path": __file__}}), \
                mock.patch.object(updater.subprocess, "Popen") as popen:
            updater.launch_installer(restart=True, minimized=True)
        self.assertIn("--restart-minimized", popen.call_args.args[0])
