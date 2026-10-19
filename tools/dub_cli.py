"""
Runs the Translate & Dub pipeline for an existing recording without starting the UI.

    .venv\\Scripts\\python.exe -m tools.dub_cli <meeting_id> <target_lang>

Useful for testing and benchmarking; the result is stored in the database exactly like a UI job.
"""

import sys
import time

from app.database import get_meeting, init_db, save_meeting_dub
from app.dubbing import run_dub_job


def main() -> None:
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    meeting_id, lang = int(sys.argv[1]), sys.argv[2]
    init_db()
    meeting = get_meeting(meeting_id)
    if not meeting:
        sys.exit(f"Recording {meeting_id} not found")
    t0 = time.time()

    def progress(pct: int, text: str) -> None:
        print(f"[{time.time() - t0:6.1f}s] {pct:3d}%  {text}", flush=True)

    entry = run_dub_job(meeting, lang, progress, lambda: None)
    save_meeting_dub(meeting_id, entry)
    print("\nOutput:", entry["path"])
    print("Subtitles:", entry["srt_path"])
    for k, v in entry["stats"].items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
