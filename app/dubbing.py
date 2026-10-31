"""
Translate & Dub (prototype).

Pipeline for a completed recording:
  1. split diarized segments into short phrase units using word timestamps
  2. translate the units with the local Ollama model, asking for a similar spoken length
  3. separate voice and background with Demucs                     (GPU worker, .venv-dub)
  4. synthesize every unit in the original speaker's voice with
     VoxCPM2, using a reference cut from the clean vocals (GPU worker)
  5. re-translate units that came out far too long, then time-fit each clip to its slot (FFmpeg atempo)
  6. mix the dubbed voice over the background and mux it into a new, clearly labelled file

The heavy models live in a separate process (app/dub_worker.py running in .venv-dub): their pinned
library versions conflict with the main app, and killing the process is the cleanest way to cancel a
job and give all VRAM back.
"""

import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import httpx
import numpy as np
import soundfile as sf

from app.audio_utils import NOISE_REDUCTION_FILTER, format_timestamp, get_media_duration
from app.database import get_all_settings
from app.dub_text import (
    build_dub_units,
    expected_speech_sec,
    TTS_WORKER_VRAM_GB,
    has_foreign_script,
    extra_tts_workers,
    plan_placement,
    speech_free_gaps,
)
from app.ollama_client import ensure_ollama_running, get_ollama_status, raise_for_ollama
from app.paths import APP_DIR, DATA_DIR, MODELS_DIR

# The optional environment sits next to the program; outputs go to the per-user data folder
DUB_PYTHON = APP_DIR / ".venv-dub" / "Scripts" / "python.exe"
DUB_WORKER = Path(__file__).resolve().parent / "dub_worker.py"
# The sound is mixed in memory at 44.1 kHz float32: the background (stereo), the voices, the mix
# (stereo) and the spoken phrases, about 1.25 MB per second of the recording, plus the program
DUB_BYTES_PER_SEC = 1.25e6
DUB_BASE_GB = 2.0


def dub_memory_gb(duration_sec: float) -> float:
    return DUB_BASE_GB + max(0.0, float(duration_sec or 0)) * DUB_BYTES_PER_SEC / 2**30
DUBS_DIR = DATA_DIR / "dubs"

# Languages of the dub (code -> English name used in LLM prompts): VoxCPM2 speaks 30, these are
# the ones checked with it (bench-report/tts-candidates.md)
DUB_LANGUAGES: Dict[str, str] = {
    "ar": "Arabic", "da": "Danish", "de": "German", "el": "Greek", "en": "English",
    "es": "Spanish", "fi": "Finnish", "fr": "French", "he": "Hebrew", "hi": "Hindi",
    "it": "Italian", "ja": "Japanese", "ko": "Korean", "ms": "Malay", "nl": "Dutch",
    "no": "Norwegian", "pl": "Polish", "pt": "Portuguese", "ru": "Russian", "sv": "Swedish",
    "sw": "Swahili", "tr": "Turkish", "zh": "Chinese",
}

MIX_SR = 44100
REF_TARGET_SEC = 10.0    # length of the voice reference cut per speaker
TEMPO_RETRANSLATE = 1.35 # clips needing more speed-up than this are re-translated shorter once
TEMPO_MAX = 1.6          # hard cap for speed-up; whatever is left is trimmed with a fade
BLOAT_RESYNTH = 1.7      # synthesized audio this much longer than the text needs: generate it again
ROOM_TONE_MAX_RATIO = 0.1  # room tone must be at least 20 dB below the speech

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0

ProgressFn = Callable[[int, str], None]
CancelFn = Callable[[], None]



def _tool(name: str) -> str:
    """FFmpeg as the rest of the app finds it (the copy the app downloaded is not on PATH)."""
    from app.ffmpeg_manager import tool_cmd

    return tool_cmd(name)

def dub_environment_status() -> Dict[str, Any]:
    from app.dub_setup import is_installed, setup_status

    return {
        "installed": is_installed(),
        "python": str(DUB_PYTHON),
        "languages": list(DUB_LANGUAGES.keys()),
        "setup": setup_status(),
    }


# ---------------------------------------------------------------- 2. translation (Ollama)
def _ollama_chat(base_url: str, model: str, system: str, user: str, keep_alive: str = "5m") -> str:
    from app.llm_text import strip_think, thinking_off

    with httpx.Client(timeout=600.0) as client:
        resp = client.post(
            f"{base_url}/api/chat",
            json={
                "model": model,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "stream": False,
                "keep_alive": keep_alive,
                "options": {"temperature": 0.2, "top_p": 0.9, "num_ctx": 8192},
                **thinking_off(model),
            },
        )
        raise_for_ollama(resp)
        return strip_think(str((resp.json().get("message") or {}).get("content") or ""))


def _unload_ollama(base_url: str, model: str, wait: bool = False) -> None:
    """Frees the LLM's VRAM for the TTS processes. With wait=True returns once Ollama lists no
    loaded model (at most a few seconds), so the free VRAM measured next is accurate."""
    try:
        with httpx.Client(timeout=30.0) as client:
            client.post(f"{base_url}/api/generate", json={"model": model, "keep_alive": 0})
            for _ in range(20 if wait else 0):
                if not (client.get(f"{base_url}/api/ps").json().get("models")):
                    break
                time.sleep(0.25)
    except Exception:
        pass


_LINE_RE = re.compile(r"^\s*(\d+)\s*[.):]\s*(.+?)\s*$")


def _translate_batch(base_url: str, model: str, texts: List[str], src: str, tgt: str) -> List[Optional[str]]:
    system = (
        f"You are a professional dubbing translator from {src} to {tgt}. "
        "Each numbered line is one spoken phrase of a video. The translation will be voiced over the "
        "original timing, so keep every line about as long as the original when spoken (similar number "
        "of syllables), natural and conversational. Never merge, split, skip or explain lines. "
        f"Answer only in {tgt}, with exactly the same numbering, one line per phrase: `N. translation`."
    )
    user = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(texts))
    out: List[Optional[str]] = [None] * len(texts)
    for line in _ollama_chat(base_url, model, system, user).splitlines():
        m = _LINE_RE.match(line)
        if m:
            idx = int(m.group(1)) - 1
            if 0 <= idx < len(texts) and out[idx] is None:
                out[idx] = m.group(2).strip().strip("`")
    return out


def _translate_single(base_url: str, model: str, text: str, src: str, tgt: str) -> str:
    system = (
        f"Translate the user's {src} phrase into natural spoken {tgt} of about the same length. "
        "Output only the translation, nothing else."
    )
    return _ollama_chat(base_url, model, system, text).strip().strip('"«»`')


def _shorten(base_url: str, model: str, source: str, current: str, max_chars: int, src: str, tgt: str) -> str:
    system = (
        f"You shorten dubbing lines. Rewrite the {tgt} line so it is at most {max_chars} characters long "
        f"and still conveys the meaning of the {src} original. Output only the new {tgt} line."
    )
    user = f"Original ({src}): {source}\nCurrent ({tgt}): {current}"
    res = _ollama_chat(base_url, model, system, user).strip().strip('"«»`')
    return res if 0 < len(res) < len(current) else current


def _ollama_model(base_url: str, preferred: str) -> str:
    """The model the dub talks to: the one of the settings if Ollama has it, else the first it has
    (the settings may name a model that was never downloaded)."""
    st = get_ollama_status(base_url)
    usable = st.get("chat_models", st["models"])
    if not st["running"] or not usable:
        raise RuntimeError("Ollama не запущена или в ней нет моделей — перевод невозможен")
    return preferred if preferred in usable else usable[0]


def translate_units(
    units: List[Dict[str, Any]],
    src_code: str,
    tgt_code: str,
    progress: ProgressFn,
    check_cancel: CancelFn,
    batch_size: int = 12,
    on_batch: Optional[Callable[[List[Dict[str, Any]]], None]] = None,
) -> None:
    settings = get_all_settings()
    base_url = (settings.get("ollama_url") or "http://127.0.0.1:11434").replace("localhost", "127.0.0.1").rstrip("/")
    model = settings.get("ollama_model") or "qwen3.5:4b"
    ensure_ollama_running(base_url)
    model = _ollama_model(base_url, model)
    src = DUB_LANGUAGES.get(src_code, src_code)
    tgt = DUB_LANGUAGES[tgt_code]

    for b in range(0, len(units), batch_size):
        check_cancel()
        chunk = units[b : b + batch_size]
        res = _translate_batch(base_url, model, [u["text"] for u in chunk], src, tgt)
        for u, t in zip(chunk, res):
            if not t or has_foreign_script(t, tgt_code):
                t = _translate_single(base_url, model, u["text"], src, tgt)
            if has_foreign_script(t, tgt_code):
                t = re.sub(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]+", "", t).strip()
            u["translation"] = t
        if on_batch:
            on_batch(chunk)
        progress(5 + int(10 * min(1.0, (b + batch_size) / max(1, len(units)))), f"Перевод: {min(len(units), b + batch_size)} из {len(units)} фраз")


# ---------------------------------------------------------------- GPU worker process
_live_lock = threading.Lock()
_live_workers: Dict[int, str] = {}  # pid -> what the process is used for, shown in the VRAM chips


def live_workers() -> List[Dict[str, Any]]:
    with _live_lock:
        items = list(_live_workers.items())
    return [{"pid": pid, "purpose": purpose} for pid, purpose in items]


class DubWorker:
    """Talks to app/dub_worker.py in .venv-dub. Cancelling kills the process (and frees VRAM)."""

    def __init__(self, check_cancel: CancelFn, purpose: str = "dub", threads: Optional[int] = None):
        if not DUB_PYTHON.exists():
            raise RuntimeError("Окружение дубляжа не установлено. Установите его в окне «Дубляж».")
        env = {
            **__import__("os").environ,
            "HF_HOME": str(MODELS_DIR / "dub" / "hf"),
            "TORCH_HOME": str(MODELS_DIR / "dub" / "torch"),
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "PYTHONIOENCODING": "utf-8",
        }
        if threads:
            # Several processes on one machine: each library thread pool would otherwise use all cores
            env.update({k: str(threads) for k in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS")})
        self._check_cancel = check_cancel
        self._cancelled = False
        self._stderr: deque = deque(maxlen=60)
        from app.shutdown import track

        self.proc = track(subprocess.Popen(
            [str(DUB_PYTHON), "-I", str(DUB_WORKER)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            creationflags=_NO_WINDOW,
        ))
        with _live_lock:
            _live_workers[self.proc.pid] = purpose
        threading.Thread(target=self._drain_stderr, daemon=True).start()
        self._stop = threading.Event()
        threading.Thread(target=self._watch_cancel, daemon=True).start()
        self._read()  # "ready"

    def _drain_stderr(self) -> None:
        for line in self.proc.stderr:
            self._stderr.append(line.rstrip())
        # stderr closes when the process exits, however it ended
        with _live_lock:
            _live_workers.pop(self.proc.pid, None)

    def _watch_cancel(self) -> None:
        while not self._stop.wait(0.3):
            try:
                self._check_cancel()
            except Exception:
                self._cancelled = True
                self.proc.kill()
                return

    def _read(self) -> Dict[str, Any]:
        line = self.proc.stdout.readline()
        if not line:
            self.proc.wait(timeout=10)
            if self._cancelled:
                self._check_cancel()  # raises JobCancelled
            raise RuntimeError("Процесс дубляжа завершился неожиданно:\n" + "\n".join(list(self._stderr)[-15:]))
        msg = json.loads(line)
        if not msg.get("ok"):
            if "No module named" in str(msg.get("error")):  # an environment made for another version
                raise RuntimeError("Окружение дубляжа устарело: переустановите его в окне «Дубляж» "
                                   f"(кнопка «Установить»). {msg.get('error')}")
            raise RuntimeError(f"Ошибка процесса дубляжа: {msg.get('error')}\n{msg.get('trace', '')}")
        return msg

    def call(self, req: Dict[str, Any], on_progress: Optional[Callable[[Dict[str, Any]], None]] = None) -> Dict[str, Any]:
        self.proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()
        while True:
            msg = self._read()
            if msg.get("event") == "progress":
                if on_progress:
                    on_progress(msg)
                continue
            return msg

    def close(self) -> None:
        self._stop.set()
        if self.proc.poll() is None:
            try:
                self.proc.stdin.write('{"cmd": "quit"}\n')
                self.proc.stdin.flush()
                self.proc.wait(timeout=15)
            except Exception:
                self.proc.kill()


def _free_vram_gb() -> float:
    from app.vram import gpu_memory

    mem = gpu_memory()
    return float(mem["free_gb"]) if mem else 0.0


def _free_vram_for_tts(pool: "TtsPool") -> float:
    """Free VRAM minus what the processes that are still starting will take."""
    return _free_vram_gb() - pool.starting() * TTS_WORKER_VRAM_GB


def _worker_threads(cpu_count: int) -> Optional[int]:
    """CPU threads per dub process: by default each library thread pool would use every core, and
    up to three TTS processes plus the main app then compete for them. VERBALIOX_DUB_THREADS
    overrides (0 = library default)."""
    value = os.environ.get("VERBALIOX_DUB_THREADS", "")
    if value.isdigit():
        return int(value) or None
    return max(2, cpu_count // 4)


class TtsStream:
    """
    One synthesis run on a TtsPool. Items can be submitted while it runs (e.g. as the translation
    produces them) and processes can join later; every process takes small batches from one queue,
    so all of them finish at about the same time. A batch of a failing process (e.g. out of memory)
    goes back to the queue for the others; cancelling stops everything.
    """

    def __init__(self, pool: "TtsPool", req: Dict[str, Any], on_item_done: Callable[[], None]):
        self._pool = pool
        self._req = req
        self._on_item_done = on_item_done
        self._pending: "queue.Queue[List[Dict[str, Any]]]" = queue.Queue()
        self._cond = threading.Condition()
        self._closed = False
        self._in_flight = 0
        self._expected = 0
        self._threads: List[threading.Thread] = []
        self.results: Dict[Any, Dict[str, Any]] = {}
        self.peak = 0.0
        self.fatal: List[BaseException] = []
        self.failures: List[BaseException] = []

    def submit(self, items: List[Dict[str, Any]], batch: Optional[int] = None) -> None:
        batch = batch or self._pool.BATCH
        with self._cond:
            self._expected += len(items)
            for i in range(0, len(items), batch):
                self._pending.put(items[i: i + batch])
            self._cond.notify_all()

    def close(self) -> None:
        """No more items will be submitted."""
        with self._cond:
            self._closed = True
            self._cond.notify_all()

    def attach(self, w: "DubWorker") -> None:
        t = threading.Thread(target=self._loop, args=(w,), daemon=True)
        self._threads.append(t)
        t.start()

    def _next(self) -> Optional[List[Dict[str, Any]]]:
        with self._cond:
            while True:
                if self.fatal:
                    return None
                try:
                    batch = self._pending.get_nowait()
                    self._in_flight += 1
                    return batch
                except queue.Empty:
                    pass
                # Done only when nothing can come back: a failing process returns its batch
                if self._closed and self._in_flight == 0:
                    return None
                self._cond.wait(0.5)

    def _loop(self, w: "DubWorker") -> None:
        while True:
            batch = self._next()
            if batch is None:
                return
            try:
                res = w.call({**self._req, "cmd": "tts", "items": batch}, lambda _p: self._on_item_done())
            except BaseException as e:  # noqa: BLE001 - re-raised in wait() or retried by others
                with self._cond:
                    self._in_flight -= 1
                    if getattr(w, "_cancelled", False):
                        self.fatal.append(e)
                    else:
                        self._pending.put(batch)  # another process takes it
                        self.failures.append(e)
                    self._cond.notify_all()
                return
            with self._cond:
                self._in_flight -= 1
                self.results.update({r["id"]: r for r in res["results"]})
                self.peak = max(self.peak, float(res.get("peak_vram_gb") or 0.0))
                self._cond.notify_all()

    def wait(self) -> Dict[str, Any]:
        """Waits until all submitted items are done; returns {"results": {id: result}, "peak_vram_gb"}."""
        self.close()
        with self._cond:
            while not self.fatal and not (self._in_flight == 0 and self._pending.empty()):
                alive = any(t.is_alive() for t in self._threads) or self._pool._spawning()
                if not alive and self._in_flight == 0:
                    break  # every process failed: nobody is left to take the queued batches
                self._cond.wait(0.5)
        if self.fatal:
            raise self.fatal[0]
        if any(getattr(w, "_cancelled", False) for w in self._pool.workers):
            check = getattr(self._pool, "_check_cancel", None)
            if check:
                check()  # raises JobCancelled
            raise RuntimeError("Синтез речи отменён")
        if not self._pending.empty() or len(self.results) < self._expected:
            raise self.failures[0] if self.failures else RuntimeError("Синтез речи не завершён")
        return {"results": self.results, "peak_vram_gb": round(self.peak, 2)}


class TtsPool:
    """
    Several DubWorker processes synthesizing phrases side by side (see TtsStream). Processes can be
    added while a stream runs, e.g. when the translation model leaves the GPU.
    """

    BATCH = 6

    def __init__(self, first: DubWorker, count: int, check_cancel: CancelFn, preload: bool = False,
                 threads: Optional[int] = None):
        self.workers: List[DubWorker] = [first]
        self.errors: List[str] = []
        self._check_cancel = check_cancel
        self._preload = preload
        self._threads = threads
        self._lock = threading.Lock()
        self._stream: Optional[TtsStream] = None
        self._spawn_threads: List[threading.Thread] = []
        self._closed = False
        self._limit: Optional[int] = None  # set by shrink(): processes still starting then close
        if count > 1:
            self.grow(count - 1)
            for t in list(self._spawn_threads):
                t.join()
            check_cancel()

    def _spawning(self) -> bool:
        return any(t.is_alive() for t in getattr(self, "_spawn_threads", []))

    def grow(self, n: int) -> None:
        """Starts `n` more processes in the background; each joins the running stream when ready.
        They start in parallel: each needs several seconds to load the model."""

        def _spawn() -> None:
            try:
                w = DubWorker(self._check_cancel, threads=self._threads)
                if self._preload:
                    w.call({"cmd": "load"})
            except Exception as e:  # fewer processes is fine; the job continues
                with self._lock:
                    self.errors.append(str(e)[:300])
                return
            with self._lock:
                if self._closed or (self._limit is not None and len(self.workers) >= self._limit):
                    w.close()
                    return
                self.workers.append(w)
                stream = self._stream
            if stream is not None:
                stream.attach(w)

        self._limit = None
        for _ in range(max(0, n)):
            t = threading.Thread(target=_spawn, daemon=True)
            self._spawn_threads.append(t)
            t.start()

    def shrink(self, keep: int) -> None:
        """Closes all processes but the first `keep` (call between streams)."""
        with self._lock:
            self._limit = keep
            extra = self.workers[keep:]
            self.workers = self.workers[:keep]
        for w in extra:
            w.close()

    def starting(self) -> int:
        """Processes still starting (they will join the running stream)."""
        return sum(1 for t in getattr(self, "_spawn_threads", []) if t.is_alive())

    def size(self) -> int:
        """Processes running or starting."""
        with self._lock:
            return len(self.workers) + sum(1 for t in self._spawn_threads if t.is_alive())

    def stream(self, req: Dict[str, Any], on_item_done: Callable[[], None]) -> TtsStream:
        s = TtsStream(self, req, on_item_done)
        lock = getattr(self, "_lock", None) or threading.Lock()
        with lock:
            self._stream = s
            workers = list(self.workers)
        for w in workers:
            s.attach(w)
        return s

    def run(self, items: List[Dict[str, Any]], req: Dict[str, Any], on_item_done: Callable[[], None]) -> Dict[str, Any]:
        """Synthesizes all items; returns {"results": {id: result}, "peak_vram_gb": float}."""
        s = self.stream(req, on_item_done)
        # A few phrases (re-synthesis) are spread over all processes instead of one batch
        s.submit(items, batch=max(1, min(self.BATCH, -(-len(items) // max(1, len(self.workers))))))
        try:
            return s.wait()
        finally:
            self._stream = None

    def close(self) -> None:
        with getattr(self, "_lock", threading.Lock()):
            self._closed = True
            workers = list(self.workers)
        for w in workers:
            w.close()


# ---------------------------------------------------------------- audio helpers
def _ffmpeg(args: List[str]) -> None:
    proc = subprocess.run(
        [_tool("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error", *args],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        encoding="utf-8", errors="replace", creationflags=_NO_WINDOW,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg: {proc.stderr[-600:]}")


def _has_video(path: Path) -> bool:
    proc = subprocess.run(
        [_tool("ffprobe"), "-v", "error", "-select_streams", "v", "-show_entries", "stream=codec_type,disposition=attached_pic",
         "-of", "json", str(path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
        creationflags=_NO_WINDOW,
    )
    try:
        streams = json.loads(proc.stdout or "{}").get("streams", [])
    except Exception:
        return False
    return any(not (s.get("disposition") or {}).get("attached_pic") for s in streams)


def _read_mono(path: Path, sr: int) -> np.ndarray:
    data, file_sr = sf.read(str(path), dtype="float32", always_2d=True)
    if file_sr != sr:
        raise RuntimeError(f"{path.name}: {file_sr} Hz, ожидалось {sr} Hz")
    return data.mean(axis=1)


def _trim_silence(x: np.ndarray, sr: int, thresh_db: float = -42.0) -> np.ndarray:
    if x.size == 0:
        return x
    win = max(1, int(sr * 0.01))
    frames = x[: len(x) // win * win].reshape(-1, win)
    if frames.size == 0:
        return x
    rms_db = 20 * np.log10(np.sqrt((frames ** 2).mean(axis=1)) + 1e-9)
    active = np.where(rms_db > thresh_db)[0]
    if active.size == 0:
        return x
    a = max(0, (active[0] - 2) * win)
    b = min(len(x), (active[-1] + 3) * win)
    return x[a:b]


def _cut_trailing_artifacts(x: np.ndarray, sr: int, expected_sec: float,
                            gap_sec: float = 0.4, silent_db: float = -35.0) -> np.ndarray:
    """
    A voice model sometimes keeps generating after the sentence: a pause, then breaths, mumbling or
    noise. Cuts at the first long pause that starts after most of the expected speech, so that tail
    is neither played nor counted when the clip is fitted into its time slot.
    """
    step = 0.05
    win = max(1, int(sr * step))
    frames = x[: len(x) // win * win].reshape(-1, win)
    if frames.shape[0] == 0:
        return x
    db = 20 * np.log10(np.sqrt((frames ** 2).mean(axis=1)) + 1e-9)
    need = int(round(gap_sec / step))
    min_start = 0.7 * expected_sec
    run = 0
    for i, value in enumerate(db):
        run = run + 1 if value < silent_db else 0
        if run == need:
            gap_start = (i - need + 1) * step
            if gap_start >= min_start:
                cut = x[: int((gap_start + 0.08) * sr)].copy()
                fade = min(cut.size, int(0.03 * sr))
                if fade:
                    cut[-fade:] *= np.linspace(1, 0, fade, dtype=np.float32)
                return cut
    return x


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt((x ** 2).mean())) if x.size else 0.0


def _stretch_clip(src: Path, dst: Path, tempo: float, use_rubberband: bool) -> None:
    """Time-stretches a clip without changing pitch and resamples it to the mix rate."""
    filters = []
    if abs(tempo - 1.0) > 0.01:
        if use_rubberband:
            filters.append(f"rubberband=tempo={tempo:.4f}:pitchq=quality")
        else:
            t = tempo
            while t > 2.0:  # atempo accepts 0.5..2.0 per instance
                filters.append("atempo=2.0")
                t /= 2.0
            filters.append(f"atempo={t:.4f}")
    filters.append(f"aresample={MIX_SR}")
    _ffmpeg(["-i", str(src), "-af", ",".join(filters), "-ac", "1", "-c:a", "pcm_f32le", str(dst)])


def _ffmpeg_has_filter(name: str) -> bool:
    try:
        proc = subprocess.run([_tool("ffmpeg"), "-hide_banner", "-filters"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              text=True, encoding="utf-8", errors="replace", creationflags=_NO_WINDOW)
        return f" {name} " in proc.stdout
    except Exception:
        return False


def _build_speaker_refs(units: List[Dict[str, Any]], vocals: np.ndarray, out_dir: Path) -> Dict[str, Path]:
    """Cuts ~10 s of the speaker's clean (Demucs-separated) voice from their longest phrases."""
    refs: Dict[str, Path] = {}
    gap = np.zeros(int(0.15 * MIX_SR), dtype=np.float32)
    for spk in sorted({u["speaker"] for u in units}):
        parts, total = [], 0.0
        for u in sorted((u for u in units if u["speaker"] == spk), key=lambda u: u["end"] - u["start"], reverse=True):
            a, b = int(u["start"] * MIX_SR), int(min(u["end"], u["start"] + REF_TARGET_SEC + 2) * MIX_SR)
            piece = vocals[a:b]
            if piece.size < MIX_SR * 0.8:
                continue
            parts += [piece, gap]
            total += piece.size / MIX_SR
            if total >= REF_TARGET_SEC:
                break
        if not parts:
            continue
        ref = np.concatenate(parts)[: int((REF_TARGET_SEC + 2) * MIX_SR)]
        peak = float(np.abs(ref).max()) or 1.0
        path = out_dir / f"ref_{spk}.wav"
        sf.write(str(path), (ref / peak * 0.9).astype(np.float32), MIX_SR)
        refs[spk] = path
    return refs


def _room_tone(vocals: np.ndarray, units: List[Dict[str, Any]], total_dur: float, n: int,
               seed: int = 0) -> Optional[np.ndarray]:
    """
    A noise bed of `n` samples made from the separated voice track between phrases: the mic hiss and
    room sound that disappear when the voice is replaced by clean synthetic speech. Random pieces of
    it are cross-faded with power-complementary windows, so the bed has no loop clicks or level dips.
    Returns None when the recording has too little speech-free audio to learn from.
    """
    sr = MIX_SR
    pieces = [vocals[int(a * sr): int(b * sr)] for a, b in speech_free_gaps(units, total_dur)]
    pieces = [p for p in pieces if p.size]
    if not pieces:
        return None
    bed = np.concatenate(pieces)
    # Drop breaths and speech the transcript missed (crosstalk): keep 50 ms frames near the floor.
    # The floor is a low percentile, so gaps that are mostly speech do not raise it.
    win = int(0.05 * sr)
    frames = bed[: bed.size // win * win].reshape(-1, win)
    if frames.shape[0] < 20:  # under a second
        return None
    rms = np.sqrt((frames ** 2).mean(axis=1)) + 1e-9
    floor = np.percentile(rms, 20)
    frames = frames[rms < floor * 2.0]
    if frames.shape[0] < 20:
        return None
    bed = frames.reshape(-1).astype(np.float32)
    if _rms(bed) < 1e-5:  # digital silence: nothing to add
        return None
    # Only a real noise floor qualifies: well below the speech. Otherwise the "gaps" hold voice the
    # transcript does not cover (crosstalk, singing), which must not be layered under the dub.
    speech = [vocals[int(float(u["start"]) * sr): int(float(u["end"]) * sr)] for u in units]
    speech = [x for x in speech if x.size]
    if speech and _rms(bed) > _rms(np.concatenate(speech)) * ROOM_TONE_MAX_RATIO:
        return None

    chunk = min(bed.size, int(4.0 * sr))
    chunk -= chunk % 2
    hop = chunk // 2
    window = np.sqrt(np.hanning(chunk + 1)[:chunk]).astype(np.float32)  # sqrt-Hann: squares sum to 1 at 50 %
    out = np.zeros(n + chunk, dtype=np.float32)
    rng = np.random.default_rng(seed)
    for pos in range(-hop, n, hop):
        start = int(rng.integers(0, max(1, bed.size - chunk + 1)))
        a = max(0, pos)
        seg = bed[start: start + chunk] * window
        out[a: pos + chunk] += seg[a - pos:]
    return out[:n]


def _denoise_file(src: Path, dst: Path) -> None:
    _ffmpeg(["-i", str(src), "-af", NOISE_REDUCTION_FILTER, "-c:a", "pcm_f32le", str(dst)])


# MP4/MKV store track languages as ISO 639-2 codes; two-letter codes are silently dropped
_ISO639_2 = {
    "ar": "ara", "da": "dan", "de": "deu", "el": "ell", "en": "eng", "es": "spa", "fi": "fin", "fr": "fra",
    "he": "heb", "hi": "hin", "it": "ita", "ja": "jpn", "ko": "kor", "ms": "msa", "nl": "nld", "no": "nor",
    "pl": "pol", "pt": "por", "ru": "rus", "sv": "swe", "sw": "swa", "tr": "tur", "zh": "zho", "uk": "ukr",
}


def _track_tags(index: int, lang: str, title: str) -> List[str]:
    spec = f"-metadata:s:a:{index}"
    tags = [spec, f"title={title}", spec, f"handler_name={title}"]  # MP4 players show handler_name
    if lang in _ISO639_2:
        tags += [spec, f"language={_ISO639_2[lang]}"]
    return tags


def _safe_name(title: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", title).strip(" .") or "recording"
    return name[:80]


def _write_srt(units: List[Dict[str, Any]], path: Path) -> None:
    lines = []
    for i, u in enumerate(units, 1):
        lines += [str(i), f"{format_timestamp(u['start'], srt=True)} --> {format_timestamp(u['end'], srt=True)}",
                  u.get("translation") or "", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------- main job
def run_dub_job(meeting: Dict[str, Any], target_lang: str, progress: ProgressFn, check_cancel: CancelFn) -> Dict[str, Any]:
    if target_lang not in DUB_LANGUAGES:
        raise ValueError(f"Язык {target_lang!r} не поддерживается для дубляжа")
    media = Path(meeting["media_path"])
    if not media.exists():
        raise RuntimeError(f"Исходный файл не найден: {media}")
    src_lang = meeting.get("language") or "ru"
    settings = get_all_settings()
    use_rubberband = settings.get("dub_stretch", "atempo") == "rubberband" and _ffmpeg_has_filter("rubberband")
    keep_original = settings.get("dub_keep_original_track", "true").lower() == "true"
    # VoxCPM2 guidance: higher follows the reference voice and manner closer, lower is freer
    cfg_value = float(settings.get("dub_voice_cfg", "2.0"))
    # "Убрать шум": steady noise is removed from the dub's background and from the original track.
    # Otherwise the background stays as recorded and, optionally, the synthetic voice gets the
    # recording's own noise floor (room tone) so it does not sound pasted onto the scene.
    denoise = settings.get("dub_denoise", "false").lower() == "true"
    room_tone = not denoise and settings.get("dub_room_tone", "true").lower() == "true"
    tts_setting = settings.get("dub_tts_workers", "auto")
    cpu_count = os.cpu_count() or 1
    worker_threads = _worker_threads(cpu_count)

    work = DUBS_DIR / f"work_meeting_{meeting['id']}_{target_lang}"
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    (work / "tts").mkdir(parents=True, exist_ok=True)
    (work / "fit").mkdir(parents=True, exist_ok=True)
    stats: Dict[str, Any] = {"started": datetime.now().isoformat(timespec="seconds")}
    t_start = datetime.now()
    # Wall time per stage, kept in the dub's stats (used to find where a long dub spends its time)
    timing: Dict[str, float] = {}
    stats["timing"] = timing
    lap = [time.perf_counter()]

    def _lap(name: str) -> None:
        now = time.perf_counter()
        timing[name] = round(timing.get(name, 0.0) + now - lap[0], 2)
        lap[0] = now

    try:
        # 1. phrases + original audio
        progress(2, "Подготовка фраз и аудио...")
        units = build_dub_units(meeting.get("segments") or [])
        if not units:
            raise RuntimeError("В записи нет реплик для дубляжа")
        orig_wav = work / "original.wav"
        _ffmpeg(["-i", str(media), "-vn", "-ac", "2", "-ar", str(MIX_SR), "-c:a", "pcm_s16le", str(orig_wav)])
        total_dur = get_media_duration(orig_wav)
        _lap("prepare")

        # 2-4. Translation (Ollama), voice/background separation (Demucs) and synthesis (VoxCPM2)
        # overlap: Demucs runs while the first phrases are translated, and every translated batch is
        # voiced right away. The LLM and one or two TTS processes share the GPU until the translation
        # ends; then the LLM is unloaded and more TTS processes join for the rest.
        check_cancel()
        progress(5, f"Перевод {len(units)} фраз (Ollama) и отделение голоса от фона (Demucs)...")
        settings_now = get_all_settings()
        ollama_url = (settings_now.get("ollama_url") or "http://127.0.0.1:11434").replace("localhost", "127.0.0.1").rstrip("/")
        n_units = len(units)
        translated_q: "queue.Queue[Optional[List[Dict[str, Any]]]]" = queue.Queue()
        tx_error: List[BaseException] = []
        tx_count = [0]
        tx_stop = threading.Event()  # set when the job ends early, so the translation stops too
        t_phase = time.perf_counter()

        def _tx_check() -> None:
            check_cancel()
            if tx_stop.is_set():
                raise RuntimeError("Дубляж прерван")

        def _translate() -> None:
            try:
                translate_units(units, src_lang, target_lang, lambda *_a: None, _tx_check, on_batch=translated_q.put)
            except BaseException as e:  # noqa: BLE001 - re-raised in the job thread
                tx_error.append(e)
            finally:
                translated_q.put(None)
                timing["translate"] = round(time.perf_counter() - t_phase, 2)

        tx_thread = threading.Thread(target=_translate, daemon=True, name="dub_translate")
        tx_thread.start()

        worker = DubWorker(check_cancel, threads=worker_threads)
        pool: Optional[TtsPool] = None
        try:
            sep = worker.call({"cmd": "separate", "input": str(orig_wav), "out_dir": str(work)})
            vocals = _read_mono(Path(sep["vocals"]), MIX_SR)
            background, _ = sf.read(sep["background"], dtype="float32", always_2d=True)
            # FFmpeg denoising of the background (~10 s per 5 min on the CPU) runs during synthesis
            bg_denoise: Optional[threading.Thread] = None
            if denoise:
                bg_denoise = threading.Thread(
                    target=_denoise_file, args=(Path(sep["background"]), work / "background_dn.wav"), daemon=True)
                bg_denoise.start()
            timing["separate"] = round(time.perf_counter() - t_phase, 2)
            refs = _build_speaker_refs(units, vocals, work)
            if not refs:
                raise RuntimeError("Не удалось вырезать образец голоса ни для одного участника")
            fallback_ref = next(iter(refs.values()))
            worker.call({"cmd": "load"})
            timing["tts_load"] = round(time.perf_counter() - t_phase, 2)

            def _items(batch: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
                items = [{"id": u["id"], "text": u["translation"], "lang": target_lang,
                          "ref": str(refs.get(u["speaker"], fallback_ref)),
                          "out": str(work / "tts" / f"u{u['id']:04d}.wav")} for u in batch]
                items.sort(key=lambda it: it["ref"])  # group by speaker: fewer conditioning switches
                return items

            def _apply(batch: List[Dict[str, Any]], res: Dict[str, Any]) -> None:
                by_id = res["results"]
                for u in batch:
                    u["tts_path"] = by_id[u["id"]]["out"]
                    if by_id[u["id"]].get("failed"):
                        u["tts_failed"] = by_id[u["id"]]["failed"]
                    else:
                        u.pop("tts_failed", None)
                stats["tts_peak_vram_gb"] = max(float(stats.get("tts_peak_vram_gb") or 0.0), float(res.get("peak_vram_gb") or 0.0))
                stats["tts_gen_sec"] = round(stats.get("tts_gen_sec", 0.0) + sum(by_id[u["id"]]["gen_sec"] for u in batch), 1)

            tts_req = {"cfg_value": cfg_value}
            pool = TtsPool(worker, 1, check_cancel, preload=True, threads=worker_threads)
            voiced = [0]
            voiced_lock = threading.Lock()

            def _on_voiced() -> None:
                with voiced_lock:
                    voiced[0] += 1
                    n = voiced[0]
                pct = 16 + int(68 * n / max(1, n_units))
                if tx_thread.is_alive():
                    progress(pct, f"Перевод: {tx_count[0]} из {n_units} фраз · синтез речи: {n}")
                else:
                    progress(pct, f"Синтез речи: {n} из {len(units_ok)}")

            units_ok: List[Dict[str, Any]] = []
            stream = pool.stream(tts_req, _on_voiced)
            grown_during_tx = False
            while True:
                try:
                    batch = translated_q.get(timeout=0.3)
                except queue.Empty:
                    check_cancel()
                    continue
                if batch is None:
                    break
                tx_count[0] += len(batch)
                # A phrase the LLM returned empty stays silent in the dub (the background still plays)
                ok = [u for u in batch if (u.get("translation") or "").strip()]
                units_ok += ok
                if ok:
                    stream.submit(_items(ok))
                if not grown_during_tx:
                    # The LLM is loaded by now: start as many extra processes as fit next to it
                    grown_during_tx = True
                    pool.grow(extra_tts_workers(tts_setting, pool.size(), _free_vram_for_tts(pool), cpu_count, respect_vram=True))
                progress(16 + int(68 * voiced[0] / max(1, n_units)), f"Перевод: {tx_count[0]} из {n_units} фраз · синтез речи: {voiced[0]}")
            if tx_error:
                raise tx_error[0]
            units = sorted(units_ok, key=lambda u: u["id"])
            if not units:
                raise RuntimeError("Модель Ollama не вернула перевод ни для одной фразы")
            try:  # the model the translation used, not one the settings name and Ollama lacks
                used = _ollama_model(ollama_url, settings_now.get("ollama_model") or "qwen3.5:4b")
            except RuntimeError:
                used = settings_now.get("ollama_model") or ""
            _unload_ollama(ollama_url, used, wait=True)
            # Its VRAM is free now: more processes for the remaining phrases
            pool.grow(extra_tts_workers(tts_setting, pool.size(), _free_vram_for_tts(pool), cpu_count))
            res = stream.wait()
            pool._stream = None
            _apply(units, res)
            stats["tts_workers"] = len(pool.workers)
            timing["tts"] = round(time.perf_counter() - t_phase, 2)
            lap[0] = time.perf_counter()

            def _tts(batch: List[Dict[str, Any]], lo: int, hi: int, label: str) -> None:
                done = [0]
                done_lock = threading.Lock()
                items = _items(batch)

                def _on() -> None:
                    with done_lock:
                        done[0] += 1
                        n = done[0]
                    progress(lo + int((hi - lo) * n / max(1, len(items))), f"{label}: {n} из {len(items)}")

                _apply(batch, pool.run(items, tts_req, _on))

            # 5a. measure, re-translate the clips that would need too much speed-up
            def _measure() -> None:
                for i, u in enumerate(units):
                    clip, sr = sf.read(u["tts_path"], dtype="float32")
                    clip = _trim_silence(clip if clip.ndim == 1 else clip.mean(axis=1), sr)
                    expected = expected_speech_sec(u["translation"], target_lang)
                    cut = _cut_trailing_artifacts(clip, sr, expected)
                    if cut.size < clip.size:
                        u["tail_cut"] = True
                    clip = cut
                    u["bloat"] = (clip.size / sr) / expected
                    u["_clip"], u["_sr"] = clip, sr
                    nxt = units[i + 1]["start"] if i + 1 < len(units) else total_dur
                    u["slot"] = max(0.3, min(nxt - 0.05, max(u["end"], u["start"] + 0.3)) - u["start"])
                    u["room"] = max(u["slot"], nxt - 0.05 - u["start"])
                    u["tempo"] = (clip.size / sr) / u["room"]

            _measure()
            _lap("measure")
            # Audio much longer than its text needs is a TTS glitch (rambling, long pauses):
            # generate the same text once more instead of shortening the translation
            bloated = [u for u in units if u["bloat"] > BLOAT_RESYNTH and u["tempo"] > 1.0]
            stats["resynthesized"] = len(bloated)
            if bloated:
                check_cancel()
                progress(85, f"Повторный синтез {len(bloated)} неудачных фраз...")
                _tts(bloated, 85, 88, "Повторный синтез")
                _measure()
                _lap("resynth")
            too_long = [u for u in units if u["tempo"] > TEMPO_RETRANSLATE and u["bloat"] <= BLOAT_RESYNTH]
            stats["retranslated"] = len(too_long)
            if too_long:
                check_cancel()
                progress(88, f"Сокращение {len(too_long)} слишком длинных фраз...")
                src_name, tgt_name = DUB_LANGUAGES.get(src_lang, src_lang), DUB_LANGUAGES[target_lang]
                model = _ollama_model(ollama_url, settings_now.get("ollama_model") or "qwen3.5:4b")
                # The LLM is loaded again. Next to three TTS processes it may not fit in VRAM and then
                # runs on the CPU, many times slower (measured: 203 s instead of 19 s for 7 phrases),
                # so only one process stays. Each shortened phrase is voiced while the next is shortened.
                pool.shrink(1)
                done = [0]
                done_lock = threading.Lock()

                def _on_short() -> None:
                    with done_lock:
                        done[0] += 1
                        n = done[0]
                    progress(89 + int(2 * n / len(too_long)), f"Синтез сокращённых фраз: {n} из {len(too_long)}")

                short_stream = pool.stream(tts_req, _on_short)
                for u in too_long:
                    max_chars = max(8, int(len(u["translation"]) / u["tempo"] * 1.1))
                    u["translation"] = _shorten(ollama_url, model, u["text"], u["translation"], max_chars, src_name, tgt_name)
                    short_stream.submit(_items([u]))
                _unload_ollama(ollama_url, model)
                _apply(too_long, short_stream.wait())
                pool._stream = None
                _measure()
                _lap("shorten")
        finally:
            tx_stop.set()
            if pool is not None:
                pool.close()
            else:
                worker.close()

        # 5b. time-fit + 6. mix
        check_cancel()
        if bg_denoise is not None:
            bg_denoise.join()
            if not (work / "background_dn.wav").is_file():
                raise RuntimeError("Не удалось убрать шум из фона (FFmpeg)")
            background, _ = sf.read(str(work / "background_dn.wav"), dtype="float32", always_2d=True)
        progress(92, "Подгонка длительности и сведение...")
        n = background.shape[0]
        voice = np.zeros(n, dtype=np.float32)
        gains = []
        tempos = []
        plan = plan_placement(
            [{**u, "dur": u["_clip"].size / u["_sr"]} for u in units], total_dur, TEMPO_MAX
        )

        def _fit(u: Dict[str, Any], place: Dict[str, float]) -> np.ndarray:
            tmp_src = work / "fit" / f"t{u['id']:04d}.wav"
            sf.write(str(tmp_src), u["_clip"], u["_sr"])
            fit_path = work / "fit" / f"f{u['id']:04d}.wav"
            _stretch_clip(tmp_src, fit_path, place["tempo"], use_rubberband)
            return _read_mono(fit_path, MIX_SR)

        # One FFmpeg process per phrase: run them side by side, then mix in order
        with ThreadPoolExecutor(max_workers=max(1, min(8, cpu_count // 2))) as ex:
            fitted = list(ex.map(_fit, units, plan))
        for u, place, clip in zip(units, plan, fitted):
            tempos.append(place["tempo"])
            max_len = int(place["room"] * MIX_SR)
            # A few ms of overshoot after stretching is harmless (the slot ends 50 ms before the next phrase)
            if clip.size > max_len + int(0.12 * MIX_SR):  # still too long after the speed cap: fade out
                clip = clip[:max_len]
                fade = min(clip.size, int(0.15 * MIX_SR))
                clip[-fade:] *= np.linspace(1, 0, fade, dtype=np.float32)
                stats["trimmed"] = stats.get("trimmed", 0) + 1
            a = int(place["start"] * MIX_SR)
            if place["start"] < u["start"] - 0.01:
                stats["pulled_earlier"] = stats.get("pulled_earlier", 0) + 1
            o = int(u["start"] * MIX_SR)
            orig_rms = _rms(vocals[o : o + int(u["slot"] * MIX_SR)])
            g = float(np.clip(orig_rms / (_rms(clip) + 1e-9), 0.25, 4.0)) if orig_rms > 1e-4 else 1.0
            gains.append(g)
            seg = clip[: max(0, n - a)] * g
            voice[a : a + seg.size] += seg

        if room_tone:
            bed = _room_tone(vocals, units, total_dur, n)
            stats["room_tone_db"] = round(20 * np.log10(_rms(bed) + 1e-9), 1) if bed is not None else None
            if bed is not None:
                voice += bed
        stats["denoise"] = denoise
        mix = background + voice[:, None]
        peak = float(np.abs(mix).max())
        if peak > 0.98:
            mix *= 0.98 / peak
        dub_wav = work / "dub_mix.wav"
        sf.write(str(dub_wav), mix.astype(np.float32), MIX_SR)
        _lap("fit_mix")

        # 7. mux into a clearly labelled output file
        check_cancel()
        progress(97, "Сборка итогового файла...")
        DUBS_DIR.mkdir(parents=True, exist_ok=True)
        is_video = _has_video(media)
        ext = (".mkv" if media.suffix.lower() in (".mkv", ".webm") else ".mp4") if is_video else ".m4a"
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        out_path = DUBS_DIR / f"{_safe_name(meeting.get('title') or media.stem)}.AI-dub.{target_lang}.{stamp}{ext}"
        notice = (
            f"AI-GENERATED DUB ({target_lang.upper()}): translated by a local LLM and voiced with a cloned "
            "synthetic voice (VoxCPM2, Perth watermark). Not the speakers' own words or voice. Made with Verbaliox."
        )
        args = ["-i", str(media), "-i", str(dub_wav)]
        maps = (["-map", "0:v:0", "-c:v", "copy"] if is_video else ["-vn"]) + ["-map", "1:a:0"]
        if keep_original:
            maps += ["-map", "0:a:0?"]
        args += maps + [
            "-c:a", "aac", "-b:a", "192k",
            "-metadata", f"title={meeting.get('title') or media.stem} [AI dub {target_lang.upper()}]",
            "-metadata", f"comment={notice}",
            "-metadata", "encoded_by=Verbaliox AI dub",
            *_track_tags(0, target_lang, f"AI dub ({target_lang.upper()}) - synthetic cloned voice"),
            "-disposition:a:0", "default",
        ]
        if keep_original:
            title = "Original (noise reduced)" if denoise else "Original"
            args += [*_track_tags(1, src_lang, title), "-disposition:a:1", "0"]
            if denoise:
                args += ["-filter:a:1", NOISE_REDUCTION_FILTER]
        if ext == ".mp4":
            args += ["-movflags", "+faststart"]
        _ffmpeg(args + [str(out_path)])
        srt_path = out_path.with_suffix(".srt")
        _write_srt(units, srt_path)
        _lap("mux")

        stats.update({
            "units": len(units),
            "duration_sec": total_dur,
            "elapsed_sec": round((datetime.now() - t_start).total_seconds(), 1),
            "avg_tempo": round(float(np.mean(tempos)), 3),
            "max_tempo": round(float(np.max(tempos)), 3),
            "stretch": "rubberband" if use_rubberband else "atempo",
            "tails_cut": sum(1 for u in units if u.get("tail_cut")),
            "failed_phrases": sum(1 for u in units if u.get("tts_failed")),
        })
        return {
            "lang": target_lang,
            "source_lang": src_lang,
            "path": str(out_path),
            "srt_path": str(srt_path),
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "stats": stats,
        }
    finally:
        if settings.get("dub_keep_workdir", "false").lower() != "true":
            shutil.rmtree(work, ignore_errors=True)
