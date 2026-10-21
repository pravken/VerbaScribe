"""
Model comparison without the window: the same measurements as Settings -> Comparison (app/bench.py),
on public test sets and on recordings of the library, with one report at the end.

  python tools/model_bench.py fleurs  --langs ru,en --n 80            # speech models, FLEURS test set
  python tools/model_bench.py library --meetings 12,15 --clip 600     # speech models, corrected recordings
  python tools/model_bench.py llm     --meetings 12,15                # Ollama models, protocol of the recordings
  python tools/model_bench.py report                                  # bench-report/report.md from all results

--models: ids separated by commas (default: every downloaded model of the kind).
Results are appended to bench-report/results.json next to this repository.

FLEURS (Google, CC-BY-4.0) is read speech: good for comparing models against each other, kinder than
real meetings. Recordings of the library are the real test: their transcripts must be corrected by
hand first, they are the reference.
"""

import argparse
import json
import platform
import sys
import tarfile
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import bench  # noqa: E402

OUT = ROOT / "bench-report"
FLEURS = {"ru": "ru_ru", "en": "en_us", "de": "de_de", "uk": "uk_ua"}
GAP = b"\0\0" * 8000  # half a second of silence between the phrases


def _save(entry):
    OUT.mkdir(exist_ok=True)
    f = OUT / "results.json"
    runs = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else []
    runs.append(entry)
    f.write_text(json.dumps(runs, ensure_ascii=False, indent=1), encoding="utf-8")


def _hardware():
    try:
        from app.hardware import get_hardware_info

        hw = get_hardware_info()
        return {"gpu": hw.get("device_name"), "vram_gb": hw.get("vram_total_gb"), "cuda": hw.get("cuda_available"),
                "cpu": platform.processor()}
    except Exception as e:
        return {"error": str(e)[:200], "cpu": platform.processor()}


def _pick(models, kind):
    if models:
        return [m.strip() for m in models.split(",") if m.strip()]
    return [c["id"] for c in (bench.asr_choices() if kind == "asr" else bench.llm_choices())]


# ---------------------------------------------------------------- FLEURS
def fleurs_clip(lang, n):
    """One wav of the first n phrases of the FLEURS test set (concatenated) and its reference text."""
    from huggingface_hub import hf_hub_download

    cfg = FLEURS[lang]
    cache = OUT / "data" / cfg
    cache.mkdir(parents=True, exist_ok=True)
    clip, ref = cache / f"test_{n}.wav", cache / f"test_{n}.txt"
    if clip.is_file() and ref.is_file():
        return clip, ref.read_text(encoding="utf-8")
    tsv = hf_hub_download("google/fleurs", f"data/{cfg}/test.tsv", repo_type="dataset")
    rows, seen = [], set()
    for line in Path(tsv).read_text(encoding="utf-8").splitlines():
        cols = line.split("\t")
        if len(cols) > 2 and cols[0] not in seen:  # several speakers read the same sentence: one of each
            seen.add(cols[0])
            rows.append((cols[1], cols[2]))
        if len(rows) >= n:
            break
    tar = hf_hub_download("google/fleurs", f"data/{cfg}/audio/test.tar.gz", repo_type="dataset")
    wanted = {name for name, _ in rows}
    frames = {}
    with tarfile.open(tar) as t:
        for member in t:
            name = Path(member.name).name
            if name in wanted:
                with wave.open(t.extractfile(member), "rb") as w:
                    if w.getframerate() != 16000 or w.getnchannels() != 1:
                        raise RuntimeError(f"{name}: {w.getframerate()} Hz, {w.getnchannels()} channels")
                    frames[name] = w.readframes(w.getnframes())
    with wave.open(str(clip), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(GAP.join(frames[name] for name, _ in rows if name in frames))
    text = " ".join(t for name, t in rows if name in frames)
    ref.write_text(text, encoding="utf-8")
    return clip, text


def run_fleurs(args):
    import numpy as np

    for lang in args.langs.split(","):
        clip, ref_text = fleurs_clip(lang, args.n)
        with wave.open(str(clip), "rb") as w:
            audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
        dur = len(audio) / 16000
        ref = bench.normalize_words(ref_text)
        print(f"\nFLEURS {lang}: {args.n} phrases, {dur / 60:.1f} min, {len(ref)} words")
        for mid in _pick(args.models, "asr"):
            print(f"  {mid} ...", flush=True)
            try:
                r = bench._asr_one(mid, clip, audio, dur, lang)
                hyp = r.pop("text_full")
                r.update(bench.wer(ref, bench.normalize_words(hyp)))
                r["text"] = hyp[:1500]
            except Exception as e:
                r = {"model": mid, "error": str(e)[:300]}
            print("   ", {k: v for k, v in r.items() if k != "text"}, flush=True)
            _save({"kind": "asr", "set": f"fleurs-{lang}", "audio_sec": round(dur), "ref_words": len(ref),
                   "at": time.strftime("%Y-%m-%d %H:%M"), "hw": _hardware(), **r})


# ---------------------------------------------------------------- library
def _meetings(ids):
    from app.database import get_meeting, init_db

    init_db()
    out = []
    for i in ids.split(","):
        m = get_meeting(int(i))
        if not m:
            print(f"recording {i} not found")
            continue
        out.append(m)
    return out


def _run_kind(kind, m, models, clip):
    bench._state.update(results=[], done=0, cancel=False, names={})
    info = (bench._run_asr if kind == "asr" else bench._run_llm)(m, models, clip)
    return info, list(bench._state["results"])


def run_library(args, kind):
    models = _pick(args.models, kind)
    for m in _meetings(args.meetings):
        print(f"\n{kind}: «{m.get('title')}» ({m['id']}), language {m.get('language')}")
        info, results = _run_kind(kind, m, models, args.clip)
        for r in results:
            print("   ", {k: v for k, v in r.items() if k != "text"}, flush=True)
            _save({"kind": kind, "set": f"library-{m['id']}", "title": m.get("title"), "language": m.get("language"),
                   "at": time.strftime("%Y-%m-%d %H:%M"), "hw": _hardware(), **info, **r})


# ---------------------------------------------------------------- report
def report(_args=None):
    f = OUT / "results.json"
    runs = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else []
    lines = ["# VerbaScribe model comparison", ""]
    if runs:
        lines += [f"Hardware: `{json.dumps(runs[-1].get('hw'), ensure_ascii=False)}`", ""]
    for kind, title in (("asr", "Speech recognition"), ("llm", "Analysis (protocol)")):
        sets = sorted({r["set"] for r in runs if r["kind"] == kind})
        if not sets:
            continue
        lines += [f"## {title}", ""]
        for s in sets:
            rows = [r for r in runs if r["kind"] == kind and r["set"] == s]
            latest = {}
            for r in rows:  # the latest run of each model
                latest[r["model"]] = r
            first = rows[0]
            lines += [f"### {s}" + (f" — {first.get('title')}" if first.get("title") else "")
                      + (f" ({first.get('audio_sec') or first.get('clip_sec')} s, {first.get('ref_words')} words)" if kind == "asr" else ""), ""]
            if kind == "asr":
                lines += ["| Model | WER | sub / ins / del | Time | ×real time | Load | RAM | VRAM | Device |",
                          "|---|---|---|---|---|---|---|---|---|"]
                for r in sorted(latest.values(), key=lambda r: r.get("wer", 9)):
                    if r.get("error"):
                        lines.append(f"| {r['model']} | — | {r['error']} | | | | | | |")
                    else:
                        lines.append(f"| {r['model']} | {r['wer'] * 100:.1f}% | {r['sub']} / {r['ins']} / {r['del']} | {r['sec']} s "
                                     f"| ×{r['speed']} | {r['load_sec']} s | {r['ram_mb']} MB | {r['vram_mb']} MB | {r.get('device', '')} |")
            else:
                lines += ["| Model | Unverified | Points | Time | Chars |", "|---|---|---|---|---|"]
                for r in sorted(latest.values(), key=lambda r: r.get("unverified_share", 9)):
                    if r.get("error"):
                        lines.append(f"| {r['model']} | — | {r['error']} | | |")
                    else:
                        lines.append(f"| {r['model']} | {r['unverified_share'] * 100:.0f}% ({r['unverified']}) | {r['points']} "
                                     f"| {r['sec']} s | {r['chars']} |")
            lines.append("")
    OUT.mkdir(exist_ok=True)
    (OUT / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("what", choices=["fleurs", "library", "llm", "report"])
    p.add_argument("--langs", default="ru,en")
    p.add_argument("--n", type=int, default=80, help="FLEURS phrases per language")
    p.add_argument("--meetings", default="", help="recording ids, comma separated")
    p.add_argument("--clip", type=int, default=600, help="seconds of each recording, 0: all of it")
    p.add_argument("--models", default="")
    a = p.parse_args()
    if a.what == "fleurs":
        run_fleurs(a)
    elif a.what == "library":
        run_library(a, "asr")
    elif a.what == "llm":
        run_library(a, "llm")
    report()


if __name__ == "__main__":
    main()
