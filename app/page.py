"""
The page: static/index.html with its parts (static/parts/*.html) put in place of their
<!-- @include parts/<name>.html --> lines. Kept in memory until one of the files changes.
"""

import re
import threading
from pathlib import Path
from typing import Dict, Tuple

INCLUDE_RE = re.compile(r"^[ \t]*<!-- @include (parts/[a-z0-9_-]+\.html) -->[ \t]*$", re.M)
_cache: Dict[str, Tuple[tuple, str]] = {}
_lock = threading.Lock()


def _stamp(static_dir: Path) -> tuple:
    files = [static_dir / "index.html", *sorted((static_dir / "parts").glob("*.html"))]
    return tuple((f.name, f.stat().st_mtime_ns, f.stat().st_size) for f in files if f.exists())


def render(static_dir: Path) -> str:
    key = str(static_dir)
    stamp = _stamp(static_dir)
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] == stamp:
            return hit[1]

    def part(m: "re.Match") -> str:
        path = static_dir / m.group(1)
        if not path.is_file():
            raise FileNotFoundError(f"static/{m.group(1)} is missing")
        return path.read_text(encoding="utf-8").rstrip("\n")

    html = INCLUDE_RE.sub(part, (static_dir / "index.html").read_text(encoding="utf-8"))
    with _lock:
        _cache[key] = (stamp, html)
    return html
