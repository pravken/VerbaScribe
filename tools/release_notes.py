"""
The notes of a release page, from CHANGELOG.md: every section of the version, both languages
("## 0.5.0" and "## 0.5.0 — English"). A beta (v0.5.0-beta.1) gets the sections of 0.5.0.

  python tools/release_notes.py v0.5.0-beta.1 notes.md

Writes the file in UTF-8 (a Windows console would not take the Cyrillic), empty when CHANGELOG.md
has no section for the version: the workflow then lets GitHub list the commits.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def notes(tag: str, text: str) -> str:
    base = re.sub(r"^v", "", tag.strip()).split("-")[0]
    out, inside = [], False
    for line in text.splitlines():
        if line.startswith("## "):
            inside = re.match(rf"^## {re.escape(base)}(\s|$)", line) is not None
        if inside and line.strip() != "---":
            out.append(line)
    return "\n".join(out).strip()


if __name__ == "__main__":
    text = notes(sys.argv[1], (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))
    Path(sys.argv[2]).write_text(text + "\n" if text else "", encoding="utf-8")
