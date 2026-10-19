"""
Profiles a dub on a slice of an existing recording: wall time per stage and GPU utilization.

    set VERBASCRIBE_DATA_DIR=<temp copy of the data folder>
    set VERBASCRIBE_SKIP_MIGRATION=1
    .venv\\Scripts\\python.exe tools\\profile_dub.py --meeting 4 --start 0 --length 300 --lang ru

The slice is cut into the temp folder; the recording in the database is not changed. Never run this
against the real data folder: the dub output is written to <data>/dubs.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _sample_gpu(stop: threading.Event, out: list) -> None:
    proc = subprocess.Popen(
        ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used", "--format=csv,noheader,nounits", "-lms", "500"],
        stdout=subprocess.PIPE, text=True,
    )
    try:
        for line in proc.stdout:
            if stop.is_set():
                break
            try:
                util, mem = (float(x) for x in line.split(","))
            except ValueError:
                continue
            out.append((time.perf_counter(), util, mem))
    finally:
        proc.kill()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--meeting", type=int, required=True)
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--length", type=float, default=300.0)
    ap.add_argument("--lang", default="ru")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    if not os.environ.get("VERBASCRIBE_DATA_DIR"):
        sys.exit("Set VERBASCRIBE_DATA_DIR to a temp copy of the data folder first")

    from app.database import get_meeting
    from app.dubbing import run_dub_job

    m = get_meeting(args.meeting)
    if not m:
        sys.exit(f"Recording {args.meeting} not found")
    a, b = args.start, args.start + args.length
    slice_dir = Path(tempfile.mkdtemp(prefix="dubprof_"))
    media = slice_dir / f"slice{Path(m['media_path']).suffix}"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(a), "-t", str(args.length),
                    "-i", m["media_path"], "-c", "copy", str(media)], check=True)
    segs = []
    for s in m.get("segments") or []:
        if s["start"] >= a and s["end"] <= b:
            words = [{**w, "start": w["start"] - a, "end": w["end"] - a} for w in s.get("words") or []]
            segs.append({**s, "start": s["start"] - a, "end": s["end"] - a, "words": words})
    meeting = {**m, "media_path": str(media), "segments": segs, "title": f"profile_{args.meeting}"}

    events = []
    t0 = time.perf_counter()

    def progress(pct: int, text: str) -> None:
        events.append((time.perf_counter(), pct, text))
        print(f"{time.perf_counter() - t0:7.1f}s {pct:3d}% {text}", flush=True)

    samples: list = []
    stop = threading.Event()
    threading.Thread(target=_sample_gpu, args=(stop, samples), daemon=True).start()
    entry = run_dub_job(meeting, args.lang, progress, lambda: None)
    stop.set()
    total = time.perf_counter() - t0

    # GPU utilization per stage: a stage lasts from its first progress message to the next stage's
    def stage_of(text: str) -> str:
        for key, name in (("Перевод", "translate"), ("Демукс", "separate"), ("Demucs", "separate"),
                          ("Запуск", "tts_spawn"), ("Загрузка Chatterbox", "tts"), ("Синтез речи", "tts"),
                          ("Повторный", "resynth"), ("Сокращ", "shorten"), ("Синтез сокращ", "shorten"),
                          ("Подгонка", "fit_mix"), ("Сборка", "mux"), ("Подготовка", "prepare")):
            if key in text:
                return name
        return "other"

    marks = [(t, stage_of(txt)) for t, _p, txt in events]
    per_stage: dict = {}
    for ts, util, mem in samples:
        name = "other"
        for t, st in marks:
            if t <= ts:
                name = st
        d = per_stage.setdefault(name, {"n": 0, "util": 0.0, "mem_max": 0.0})
        d["n"] += 1
        d["util"] += util
        d["mem_max"] = max(d["mem_max"], mem)
    gpu = {k: {"avg_util": round(v["util"] / v["n"], 1), "max_mem_mb": v["mem_max"], "samples": v["n"]}
           for k, v in per_stage.items()}
    report = {
        "slice": [a, b], "units": entry["stats"].get("units"), "total_sec": round(total, 1),
        "timing": entry["stats"].get("timing"), "gpu_by_stage": gpu,
        "avg_util": round(sum(s[1] for s in samples) / max(1, len(samples)), 1),
        "stats": entry["stats"], "output": entry["path"],
    }
    print(json.dumps(report, ensure_ascii=False, indent=1))
    if args.out:
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
