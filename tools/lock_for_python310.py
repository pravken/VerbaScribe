"""
Makes requirements.txt (the lock for Python 3.11) installable in a Python 3.10 environment too.

Why: an update is installed by the code of the version being replaced (app/updater.py runs in the
old environment), and before 0.6 that code puts requirements.txt into the existing .venv, which is
Python 3.10 there. The lock must install there without errors; the new version then moves its own
environment to Python 3.11 at the next start (app/envmove.py).

How: the same compile for Python 3.10, fresh, with the versions of the 3.11 lock as constraints.
A package of the same version gets the hashes of its 3.10 wheels added (they differ per Python);
a package whose version differs, or which only one Python needs, gets an environment marker:
python_version < "3.11" for the 3.10 lines, >= "3.11" for the 3.11 lines. Run it right after the
compile command at the top of requirements.in:

    python tools/lock_for_python310.py
"""

import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCK = ROOT / "requirements.txt"
OLD, NEW = 'python_version < "3.11"', 'python_version >= "3.11"'
HASH = re.compile(r"--hash=sha256:[0-9a-f]{64}")


def blocks(text: str):
    """{name: (version, lines of the entry)} of a uv lock; the comment lines stay with their package."""
    out, cur = {}, None
    for line in text.splitlines():
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^ ;\\]+)", line)
        if m:
            cur = m.group(1).lower()
            out[cur] = (m.group(2), [line])
        elif cur and (line.startswith(" ") or not line.strip()):
            out[cur][1].append(line)
        else:
            cur = None
    return out


def entry(first: str, hashes, comments=()):
    lines = [first.rstrip(" \\") + " \\"] + [f"    {h} \\" for h in hashes]
    lines[-1] = lines[-1][:-2]
    return lines + list(comments)


def main() -> int:
    new_text = LOCK.read_text(encoding="utf-8")
    if OLD in new_text:
        print("requirements.txt already has the Python 3.10 lines: compile it again first")
        return 1
    new = blocks(new_text)
    with tempfile.TemporaryDirectory() as tmp:
        old_lock, pins = Path(tmp) / "requirements-py310.txt", Path(tmp) / "pins.txt"
        pins.write_text("\n".join(f"{n}=={v}" for n, (v, _l) in new.items() if n != "filelock") + "\n")
        subprocess.run(["uv", "pip", "compile", str(ROOT / "requirements.in"), "-c", str(pins), "-o", str(old_lock),
                        "--generate-hashes", "--python-version", "3.10", "--python-platform", "x86_64-pc-windows-msvc",
                        "--index-strategy", "unsafe-best-match", "--extra-index-url",
                        "https://download.pytorch.org/whl/cu126", "--emit-index-url", "--quiet"], check=True, cwd=ROOT)
        old = blocks(old_lock.read_text(encoding="utf-8"))

    out = new_text.rstrip("\n").splitlines()
    changed = []
    for name, (version, lines) in new.items():
        i = out.index(lines[0])
        hashes = [m.group(0) for ln in lines for m in HASH.finditer(ln)]
        comments = [ln for ln in lines[1:] if not HASH.search(ln)]
        if old.get(name, (None,))[0] != version:  # a 3.11 version 3.10 cannot take, or 3.11 only
            out[i:i + len(lines)] = entry(f"{name}=={version} ; {NEW}", hashes, comments)
            changed.append(f"{name}=={version} (3.11 only)")
            continue
        extra = [m.group(0) for ln in old[name][1] for m in HASH.finditer(ln) if m.group(0) not in hashes]
        if extra:  # the 3.10 wheels of the same version
            out[i:i + len(lines)] = entry(lines[0], hashes + extra, comments)
            changed.append(f"{name}=={version} (+{len(extra)} hashes of 3.10 wheels)")
    tail = ["", "# Python 3.10 environments (an older version's updater installs this file there; the app then",
            "# moves to Python 3.11, app/envmove.py): tools/lock_for_python310.py"]
    for name, (version, lines) in sorted(old.items()):
        if new.get(name, (None,))[0] != version:
            tail += entry(f"{name}=={version} ; {OLD}", [m.group(0) for ln in lines for m in HASH.finditer(ln)])
            changed.append(f"{name}=={version} (3.10 only)")
    LOCK.write_text("\n".join(out + tail) + "\n", encoding="utf-8")
    print("\n".join(changed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
