"""
Model comparison on the user's own recordings (Settings -> Models -> Comparison).

Speech recognition: the reference is a recording whose transcript the user has checked and
corrected; a fragment of it (whole phrases, up to the chosen length) is recognized by every chosen
model, and each gets
- WER: the share of words it got wrong against the transcript (substitutions + insertions +
  deletions / reference words; case, "ё" and punctuation do not count),
- the time it took (model loading apart) and how much faster than real time that is,
- the memory it needed (RAM of this program, and video memory with an NVIDIA GPU).

Analysis: every chosen local (Ollama) model writes the protocol of the same recording; each gets the
time, the length, and the share of points the transcript does not support (app/grounding.py).

Nothing leaves the computer: the cloud model is not compared. One comparison at a time, and not
while recordings are being transcribed or live captions run (they would share the GPU and the
times would mean nothing). The latest results are kept in bench.json in the data folder.
"""

import json
import re
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.paths import DATA_DIR

CLIP_CHOICES = (60, 180, 600, 0)  # seconds of the recording; 0: all of it
HISTORY = 20
_FILE = DATA_DIR / "bench.json"

_lock = threading.Lock()
_state: Dict[str, Any] = {"running": False, "kind": "", "meeting_id": None, "title": "", "model": "", "done": 0,
                          "total": 0, "stage": "", "error": "", "results": [], "cancel": False, "started_at": ""}


class Cancelled(Exception):
    pass


# ---------------------------------------------------------------- text and WER
def normalize_words(text: str) -> List[str]:
    """Words for WER: lower case, "ё" as "е", no punctuation; "5" and "пять" stay different words."""
    t = (text or "").casefold().replace("ё", "е")
    t = re.sub(r"(?<=\d)[ ,.](?=\d{3}\b)", "", t)  # "1 000" / "1,000" -> "1000"
    return re.findall(r"[^\W_]+(?:['’-][^\W_]+)*", t)


def wer(reference: List[str], hypothesis: List[str]) -> Dict[str, Any]:
    """Word error rate with the counts behind it (Levenshtein distance over words)."""
    n, m = len(reference), len(hypothesis)
    if not n:
        return {"wer": 0.0 if not m else 1.0, "sub": 0, "ins": m, "del": 0, "ref_words": 0}
    # each cell: (errors, substitutions, insertions, deletions)
    prev = [(j, 0, j, 0) for j in range(m + 1)]
    for i in range(1, n + 1):
        cur = [(i, 0, 0, i)] + [None] * m  # type: ignore[list-item]
        for j in range(1, m + 1):
            if reference[i - 1] == hypothesis[j - 1]:
                cur[j] = prev[j - 1]
                continue
            s, ins, dele = prev[j - 1], cur[j - 1], prev[j]
            best = min(s, ins, dele, key=lambda c: c[0])
            if best is s:
                cur[j] = (s[0] + 1, s[1] + 1, s[2], s[3])
            elif best is ins:
                cur[j] = (ins[0] + 1, ins[1], ins[2] + 1, ins[3])
            else:
                cur[j] = (dele[0] + 1, dele[1], dele[2], dele[3] + 1)
        prev = cur
    e, sub, ins, dele = prev[m]
    return {"wer": round(e / n, 4), "sub": sub, "ins": ins, "del": dele, "ref_words": n}


def reference_fragment(segments: List[Dict[str, Any]], clip_sec: float, start: float = 0.0) -> Tuple[float, float, str]:
    """(start, end, text) of the whole phrases from `start` that fit in clip_sec (0: all of them)."""
    picked: List[Dict[str, Any]] = []
    for s in segments:
        if float(s.get("end", 0)) <= start or not str(s.get("text") or "").strip():
            continue
        if clip_sec and picked and float(s["end"]) - float(picked[0]["start"]) > clip_sec:
            break
        picked.append(s)
    if not picked:
        return 0.0, 0.0, ""
    return float(picked[0]["start"]), float(picked[-1]["end"]), " ".join(str(s["text"]).strip() for s in picked)


# ---------------------------------------------------------------- memory
def _rss_bytes() -> int:
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class _Counters(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

            c = _Counters()
            c.cb = ctypes.sizeof(c)
            # The pseudo handle -1 has to go as a 64-bit HANDLE: as a plain int it is cut to 32 bits and the call fails
            ctypes.windll.psapi.GetProcessMemoryInfo(wintypes.HANDLE(-1), ctypes.byref(c), c.cb)
            return int(c.WorkingSetSize)
        with open("/proc/self/statm") as f:
            import os

            return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except Exception:
        return 0


def _vram_bytes() -> int:
    try:
        from app.vram import nvml_device

        d = nvml_device()
        return int(d[1]) if d else 0
    except Exception:
        return 0


class _Peak:
    """The highest RAM and video memory use while the block runs, above what was used before."""

    def __init__(self) -> None:
        self.ram0, self.vram0 = _rss_bytes(), _vram_bytes()
        self.ram, self.vram = self.ram0, self.vram0
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._poll, daemon=True, name="bench_memory")

    def _poll(self) -> None:
        while not self._stop.wait(0.25):
            self.ram, self.vram = max(self.ram, _rss_bytes()), max(self.vram, _vram_bytes())

    def __enter__(self) -> "_Peak":
        self._t.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self._t.join(timeout=1)
        self.ram, self.vram = max(self.ram, _rss_bytes()), max(self.vram, _vram_bytes())

    def result(self) -> Dict[str, int]:
        mb = 1024 * 1024
        return {"ram_mb": max(0, (self.ram - self.ram0) // mb), "vram_mb": max(0, (self.vram - self.vram0) // mb)}


# ---------------------------------------------------------------- state
def status() -> Dict[str, Any]:
    with _lock:
        out = {k: v for k, v in _state.items() if k not in ("cancel", "names")}
        out["results"] = list(_state["results"])
    out["history"] = history()
    return out


def history() -> List[Dict[str, Any]]:
    try:
        return json.loads(_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _save_run(run: Dict[str, Any]) -> None:
    runs = [run] + history()
    try:
        _FILE.write_text(json.dumps(runs[:HISTORY], ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        traceback.print_exc()


def clear_history() -> None:
    _FILE.unlink(missing_ok=True)


def cancel() -> None:
    with _lock:
        _state["cancel"] = True


def _check() -> None:
    with _lock:
        if _state["cancel"]:
            raise Cancelled()


def _set(**kw: Any) -> None:
    with _lock:
        _state.update(kw)


def busy_reason() -> str:
    from app import live, transcriber

    if transcriber.running_job_kind() or transcriber.queue_snapshot():
        return "Дождитесь, пока закончатся задачи в очереди: они делят видеокарту и исказят время"
    if live.has_sessions():
        return "Остановите запись с субтитрами: она делит видеокарту и исказит время"
    return ""


# ---------------------------------------------------------------- models to compare
def asr_choices() -> List[Dict[str, Any]]:
    from app import asr_onnx
    from app.model_manager import format_whisper_model_name, is_whisper_model_installed, _WHISPER_REPOS

    out = [{"id": mid, "name": format_whisper_model_name(mid), "engine": "whisper"}
           for mid in _WHISPER_REPOS if is_whisper_model_installed(mid)]
    out += [{"id": mid, "name": m["name"], "engine": "onnx", "languages": m["languages"]}
            for mid, m in asr_onnx.ONNX_MODELS.items() if asr_onnx.is_installed(mid)]
    return out


def llm_choices() -> List[Dict[str, Any]]:
    from app.ollama_client import format_llm_model_name, get_ollama_status

    st = get_ollama_status()
    return [{"id": m, "name": format_llm_model_name(m)} for m in st.get("models", [])
            if not re.search(r"embed|bge|nomic|minilm", m, re.I)]


# ---------------------------------------------------------------- running
def start(kind: str, meeting_id: int, models: List[str], clip_sec: int = 180) -> Dict[str, Any]:
    from app.database import get_meeting

    if kind not in ("asr", "llm"):
        raise ValueError("Неизвестный вид сравнения")
    m = get_meeting(meeting_id)
    if not m or m.get("status") != "completed" or not m.get("segments"):
        raise ValueError("Выберите расшифрованную запись")
    if kind == "asr" and (m.get("media_removed") or not Path(str(m.get("media_path") or "")).is_file()):
        raise ValueError("У этой записи нет звука (файл удалён или перемещён)")
    models = [x for x in dict.fromkeys(models) if x]
    if not models:
        raise ValueError("Выберите хотя бы одну модель")
    reason = busy_reason()
    if reason:
        raise ValueError(reason)
    try:
        names = {c["id"]: c["name"] for c in (asr_choices() if kind == "asr" else llm_choices())}
    except Exception:
        names = {}
    with _lock:
        if _state["running"]:
            raise ValueError("Сравнение уже идёт")
        _state.update(running=True, kind=kind, meeting_id=meeting_id, title=m.get("title") or "", model="", done=0,
                      total=len(models), stage="", error="", results=[], cancel=False,
                      started_at=time.strftime("%Y-%m-%d %H:%M"), names=names)
    runner = _run_asr if kind == "asr" else _run_llm
    threading.Thread(target=_guarded, args=(runner, m, models, int(clip_sec)), daemon=True, name="bench").start()
    return status()


def _guarded(runner: Callable, m: Dict[str, Any], models: List[str], clip_sec: int) -> None:
    try:
        info = runner(m, models, clip_sec)
        with _lock:
            results = list(_state["results"])
        if results:
            _save_run({"kind": _state["kind"], "meeting_id": m["id"], "title": m.get("title") or "",
                       "at": _state["started_at"], "results": results, **info})
    except Cancelled:
        _set(error="Остановлено")
    except Exception as e:
        traceback.print_exc()
        _set(error=str(e)[:300])
    finally:
        _set(running=False, model="", stage="")


def _add_result(r: Dict[str, Any]) -> None:
    with _lock:
        r.setdefault("name", _state.get("names", {}).get(r["model"], r["model"]))
        _state["results"].append(r)
        _state["done"] += 1


def _run_asr(m: Dict[str, Any], models: List[str], clip_sec: int) -> Dict[str, Any]:
    import tempfile

    import numpy as np

    from app.audio_utils import extract_audio_16k_mono

    start, end, ref_text = reference_fragment(m["segments"], clip_sec)
    ref = normalize_words(ref_text)
    if not ref:
        raise ValueError("В расшифровке нет текста для сравнения")
    lang = m.get("language") if m.get("language") not in (None, "", "auto", "mixed") else None  # found by the transcription
    _set(stage="Подготовка звука…")
    with tempfile.TemporaryDirectory(prefix="vs_bench_") as tmp:
        full = Path(tmp) / "full.wav"
        extract_audio_16k_mono(m["media_path"], full, enhance_audio=False)
        import wave

        with wave.open(str(full), "rb") as w:
            w.setpos(min(w.getnframes(), int(start * 16000)))
            frames = w.readframes(int((end - start) * 16000) + 16000 // 2)
        audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        clip = Path(tmp) / "clip.wav"
        with wave.open(str(clip), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(frames)
        dur = len(audio) / 16000
        for mid in models:
            _check()
            _set(model=mid, stage="Загрузка модели…")
            try:
                r = _asr_one(mid, clip, audio, dur, lang)
                r.update(wer(ref, normalize_words(r.pop("text_full"))))
            except Cancelled:
                raise
            except Exception as e:
                traceback.print_exc()
                r = {"model": mid, "error": str(e)[:300]}
            _add_result(r)
    return {"clip_start": round(start, 1), "clip_sec": round(end - start, 1), "ref_words": len(ref), "language": lang or ""}


def _ollama_unload_all(timeout: float = 20.0) -> None:
    """Every model Ollama holds is unloaded (and waited for): the next one gets the whole GPU."""
    try:
        import httpx

        from app.vram import _ollama_base, ollama_loaded

        loaded = ollama_loaded()
        if not loaded:
            return
        with httpx.Client(timeout=15.0) as client:
            for m in loaded:
                client.post(f"{_ollama_base()}/api/generate", json={"model": m["name"], "keep_alive": 0})
        deadline = time.time() + timeout
        while ollama_loaded() and time.time() < deadline:
            time.sleep(0.5)
    except Exception:
        traceback.print_exc()


def clean_slate() -> None:
    """Before each measured model: nothing else in memory (Whisper, GigaAM / Parakeet, the speaker
    model, the caption worker, Ollama's models), so every model starts from the same state and the
    one before it does not take video memory away from it."""
    import gc

    from app import asr_onnx, live, transcriber
    from app.diarizer import ecapa_loaded_on, unload_diarizer
    from app.hardware import clear_vram

    transcriber.release_whisper_model()
    asr_onnx.release()
    if ecapa_loaded_on():
        unload_diarizer()
    worker = live._worker
    if worker is not None and worker.proc is not None:
        with worker.lock:
            worker._kill()
    _ollama_unload_all()
    gc.collect()
    clear_vram()
    time.sleep(1.0)  # the driver returns the memory a moment later


def _asr_one(mid: str, clip: Path, audio: Any, dur: float, lang: Optional[str]) -> Dict[str, Any]:
    from app import asr_onnx

    if asr_onnx.is_onnx_model(mid):
        if lang and not asr_onnx.supports_language(mid, lang):
            raise ValueError(f"Модель не знает язык записи ({lang})")
        from app import onnx_gpu

        clean_slate()
        gpu = onnx_gpu.gpu_for_job(mid)  # as a transcription job would run it, with the GPU free
        with _Peak() as peak:
            t0 = time.perf_counter()
            asr_onnx._load(mid, gpu)
            t1 = time.perf_counter()
            _set(stage="Распознавание…")
            segs = asr_onnx.transcribe(mid, clip, dur, lambda _p: None, _check, gpu=gpu)
            t2 = time.perf_counter()
        asr_onnx.release(mid, gpu=gpu)
        text, device = " ".join(s["text"] for s in segs), "cuda" if gpu else "cpu"
        if gpu:
            batch, vad_gpu = asr_onnx.gpu_tuning()
            device = f"cuda · batch {batch}{' · VAD cuda' if vad_gpu else ''}"
    else:
        from app import transcriber
        from app.database import get_all_settings

        settings = get_all_settings()
        clean_slate()
        with _Peak() as peak:
            t0 = time.perf_counter()
            model = transcriber._load_whisper_with_fallback(mid, settings.get("device", "cuda"),
                                                            settings.get("compute_type", "float16"))
            t1 = time.perf_counter()
            _set(stage="Распознавание…")
            seg_iter, _info = transcriber._start_transcription(model, clip, batch_size=0, duration=dur, language=lang,
                                                               hotwords="", vad_enabled=True)
            parts = []
            for s in seg_iter:
                _check()
                parts.append(s.text.strip())
            t2 = time.perf_counter()
        device = str(getattr(getattr(model, "model", None), "device", "") or settings.get("device", ""))
        del model
        from app.hardware import clear_vram

        clear_vram()
        text = " ".join(parts)
    sec = max(0.001, t2 - t1)
    return {"model": mid, "load_sec": round(t1 - t0, 1), "sec": round(sec, 1), "speed": round(dur / sec, 1),
            "device": device, **peak.result(), "text": text[:4000], "text_full": text}


def _run_llm(m: Dict[str, Any], models: List[str], clip_sec: int) -> Dict[str, Any]:
    from app.grounding import UNVERIFIED_MARK, ground_summary
    from app.ollama_client import generate_meeting_summary

    import httpx

    from app.ollama_client import ensure_ollama_running
    from app.vram import _ollama_base

    ensure_ollama_running(_ollama_base())
    for mid in models:
        _check()
        _set(model=mid, stage="Загрузка модели…")
        try:
            clean_slate()  # the models before it are out of the video memory
            t_load = time.perf_counter()
            with httpx.Client(timeout=600.0) as client:  # loading alone, timed apart from the writing
                client.post(f"{_ollama_base()}/api/generate", json={"model": mid, "keep_alive": "10m"}).raise_for_status()
            load_sec = time.perf_counter() - t_load
            _set(stage="Пишет протокол…")
            t0 = time.perf_counter()
            md = generate_meeting_summary(m["segments"], m.get("speakers") or {}, "protocol", model_name=mid,
                                          answer_lang=_answer_lang(m), local_only=True)
            sec = time.perf_counter() - t0
            grounded = ground_summary(md, m["segments"], m.get("speakers") or {})
            points = [ln for ln in grounded.splitlines() if re.match(r"\s*(?:[-*+]|\d+[.)])\s+\S", ln)]
            unverified = sum(1 for ln in points if UNVERIFIED_MARK in ln)
            r = {"model": mid, "sec": round(sec, 1), "load_sec": round(load_sec, 1), "chars": len(md), "points": len(points), "unverified": unverified,
                 "unverified_share": round(unverified / len(points), 3) if points else 0.0, "text": grounded[:20000]}
        except Cancelled:
            raise
        except Exception as e:
            traceback.print_exc()
            r = {"model": mid, "error": str(e)[:300]}
        _add_result(r)
    _ollama_unload_all()
    return {"transcript_chars": sum(len(str(s.get("text") or "")) for s in m["segments"])}


def _answer_lang(m: Dict[str, Any]) -> str:
    """The protocol is written in the language of the recording: the check compares words."""
    lang = (m.get("language") or "")[:2]
    if lang in ("", "au", "mi"):
        from app.asr_onnx import guess_language

        lang = guess_language(" ".join(str(x.get("text") or "") for x in m.get("segments") or [])[:20000])
    return lang
