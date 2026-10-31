"""
The notes of a release page, from CHANGELOG.md: every section of the version, both languages, the
English one first ("## 0.5.0 — English", then "## 0.5.0"). A beta (v0.5.0-beta.1) gets the sections
of 0.5.0.

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
    sections, inside = [], False
    for line in text.splitlines():
        if line.startswith("## "):
            inside = re.match(rf"^## {re.escape(base)}(\s|$)", line) is not None
            if inside:
                sections.append([])
        if inside and line.strip() != "---":
            sections[-1].append(line)
    english = [s for s in sections if s[0].rstrip().endswith("English")]
    blocks = english + [s for s in sections if s not in english]  # the English page first, always
    return "\n\n".join("\n".join(s).strip() for s in blocks).strip()


if __name__ == "__main__":
    text = notes(sys.argv[1], (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))
    Path(sys.argv[2]).write_text(text + "\n" if text else "", encoding="utf-8")
