"""
Search by meaning: "what did we agree about the deadline" finds "let's move it to Friday" although
no word is shared.

Transcripts are cut into chunks of a few phrases (about CHUNK_CHARS characters, with the time of
their first phrase). Each chunk gets an embedding vector from a local Ollama embedding model
(bge-m3 by default: multilingual, Russian and English alike), stored in SQLite (semantic_chunks);
nothing leaves the computer. A question is embedded the same way and compared with every chunk by
cosine similarity. The keyword index (segments_fts) stays: app/library_qa.py and the library search
merge both, so exact names and numbers are still found by keywords.

Indexing runs in the background while Ollama is running and nothing else is using the GPU: after a
recording is transcribed or edited (its semantic_meta row is dropped then), and once for the
library when the feature is switched on.
"""

import threading
import time
import traceback
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from app.database import _db_lock, get_all_settings, get_connection, get_meeting

CHUNK_CHARS = 500
EMBED_BATCH = 16
IDLE_RECHECK_SEC = 120.0
MIN_SCORE = 0.35  # below this cosine similarity a chunk is not about the question

_state: Dict[str, Any] = {"running": False, "error": "", "current": None}
_wake = threading.Event()
_started = False
_start_lock = threading.Lock()
_cache: Dict[str, Any] = {"key": None, "ids": None, "starts": None, "matrix": None, "texts": None, "speakers": None}
_cache_lock = threading.Lock()


def enabled(settings: Optional[Dict[str, str]] = None) -> bool:
    settings = settings or get_all_settings()
    return (settings.get("semantic_search") or "false").lower() == "true"


def model_name(settings: Optional[Dict[str, str]] = None) -> str:
    settings = settings or get_all_settings()
    return (settings.get("semantic_model") or "bge-m3").strip()


def _base_url(settings: Dict[str, str]) -> str:
    return (settings.get("ollama_url") or "http://127.0.0.1:11434").replace("localhost", "127.0.0.1").rstrip("/")


# ------------------------------------------------------------------ chunks
def meeting_chunks(meeting: Dict[str, Any]) -> List[Tuple[float, str, str]]:
    """(start, speaker, text) chunks of a recording: consecutive phrases joined up to CHUNK_CHARS,
    each phrase prefixed with its speaker so the model sees who said what."""
    from app.ollama_client import _segment_parts

    speakers = meeting.get("speakers") or {}
    chunks: List[Tuple[float, str, str]] = []
    cur: List[str] = []
    cur_start, cur_speaker, size = 0.0, "", 0
    for seg in meeting.get("segments") or []:
        name = (speakers.get(seg.get("speaker")) or {}).get("name") or seg.get("speaker") or ""
        for start, text in _segment_parts(seg):
            text = (text or "").strip()
            if not text:
                continue
            line = f"{name}: {text}" if name else text
            if cur and size + len(line) > CHUNK_CHARS:
                chunks.append((cur_start, cur_speaker, "\n".join(cur)))
                cur, size = [], 0
            if not cur:
                cur_start, cur_speaker = float(start), name
            cur.append(line)
            size += len(line) + 1
    if cur:
        chunks.append((cur_start, cur_speaker, "\n".join(cur)))
    return chunks


# ------------------------------------------------------------------ embeddings
def embed(texts: List[str], settings: Optional[Dict[str, str]] = None) -> np.ndarray:
    """Unit-length float32 vectors of the texts from Ollama (/api/embed)."""
    import httpx

    settings = settings or get_all_settings()
    out: List[List[float]] = []
    with httpx.Client(timeout=120.0) as c:
        for i in range(0, len(texts), EMBED_BATCH):
            r = c.post(f"{_base_url(settings)}/api/embed", json={"model": model_name(settings), "input": texts[i:i + EMBED_BATCH]})
            if r.status_code == 404:
                raise RuntimeError(f"Модель {model_name(settings)} не скачана в Ollama")
            r.raise_for_status()
            out.extend(r.json().get("embeddings") or [])
    if len(out) != len(texts):
        raise RuntimeError("Ollama вернула не все векторы")
    m = np.asarray(out, dtype=np.float32)
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    return m / np.maximum(norms, 1e-9)


def index_meeting(meeting_id: int, settings: Optional[Dict[str, str]] = None) -> int:
    """Embeds and stores the chunks of one recording; returns how many chunks it has."""
    settings = settings or get_all_settings()
    m = get_meeting(meeting_id)
    if not m:
        return 0
    chunks = meeting_chunks(m)
    vectors = embed([c[2] for c in chunks], settings) if chunks else np.zeros((0, 0), dtype=np.float32)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _db_lock:
        conn = get_connection()
        conn.execute("DELETE FROM semantic_chunks WHERE meeting_id = ?", (meeting_id,))
        conn.executemany(
            "INSERT INTO semantic_chunks (meeting_id, chunk, start, speaker, text, vec) VALUES (?, ?, ?, ?, ?, ?)",
            [(meeting_id, i, c[0], c[1], c[2], vectors[i].tobytes()) for i, c in enumerate(chunks)],
        )
        conn.execute("INSERT OR REPLACE INTO semantic_meta (meeting_id, model, updated_at) VALUES (?, ?, ?)",
                     (meeting_id, model_name(settings), now))
        conn.commit()
        conn.close()
    _invalidate_cache()
    return len(chunks)


def pending_ids(settings: Optional[Dict[str, str]] = None) -> List[int]:
    """Completed recordings with a transcript that are not indexed with the current model."""
    model = model_name(settings)
    with _db_lock:
        conn = get_connection()
        rows = conn.execute(
            """
            SELECT id FROM meetings
            WHERE status = 'completed' AND has_transcript = 1
              AND id NOT IN (SELECT meeting_id FROM semantic_meta WHERE model = ?)
            ORDER BY id DESC
            """,
            (model,),
        ).fetchall()
        conn.close()
    return [int(r["id"]) for r in rows]


def status() -> Dict[str, Any]:
    settings = get_all_settings()
    model = model_name(settings)
    with _db_lock:
        conn = get_connection()
        total = conn.execute("SELECT COUNT(*) FROM meetings WHERE status = 'completed' AND has_transcript = 1").fetchone()[0]
        done = conn.execute(
            "SELECT COUNT(*) FROM semantic_meta s JOIN meetings m ON m.id = s.meeting_id WHERE s.model = ? AND m.status = 'completed'",
            (model,),
        ).fetchone()[0]
        conn.close()
    return {"enabled": enabled(settings), "model": model, "indexed": int(done), "total": int(total),
            "running": _state["running"], "error": _state["error"]}


# ------------------------------------------------------------------ background indexer
def _gpu_busy() -> bool:
    try:
        from app import live, transcriber

        with transcriber._jobs_lock:
            jobs = bool(transcriber._active_jobs)
        return jobs or bool(live.active_session_count())
    except Exception:
        return False


def _ollama_up(settings: Dict[str, str]) -> bool:
    from app.ollama_client import _is_ollama_port_open

    return bool(_is_ollama_port_open())


_skip: set = set()  # recordings that failed to index in this run of the app


def _run() -> None:
    while True:
        _wake.wait(IDLE_RECHECK_SEC)
        _wake.clear()
        try:
            settings = get_all_settings()
            if not enabled(settings) or not _ollama_up(settings):
                continue
            for mid in [m for m in pending_ids(settings) if m not in _skip]:
                if not enabled() or _gpu_busy():
                    break  # transcription and live captions come first; picked up again later
                _state.update(running=True, current=mid)
                try:
                    index_meeting(mid, settings)
                except RuntimeError:
                    raise  # the model itself is not available: nothing else can be indexed now
                except Exception:
                    traceback.print_exc()  # one bad recording must not stop the others
                    _skip.add(mid)
            _state["error"] = ""
        except Exception as e:
            # Reported once, not every two minutes (a model that is not downloaded is the usual case)
            if str(e)[:300] != _state["error"]:
                if isinstance(e, RuntimeError):
                    print(f"[semantic] {e}", flush=True)
                else:
                    traceback.print_exc()
            _state["error"] = str(e)[:300]
        finally:
            _state.update(running=False, current=None)


def start_indexer() -> None:
    global _started
    with _start_lock:
        if not _started:
            threading.Thread(target=_run, daemon=True, name="semantic_indexer").start()
            _started = True
    _wake.set()


def kick() -> None:
    """Something may need indexing (a transcript finished or was edited, the feature was enabled)."""
    if _started:
        _wake.set()


# ------------------------------------------------------------------ search
def _invalidate_cache() -> None:
    with _cache_lock:
        _cache["key"] = None


def _load_matrix(model: str) -> Dict[str, Any]:
    with _db_lock:
        conn = get_connection()
        key = conn.execute(
            "SELECT COUNT(*), MAX(updated_at) FROM semantic_meta WHERE model = ?", (model,)
        ).fetchone()
        key = (model, key[0], key[1])
        with _cache_lock:
            if _cache["key"] == key:
                conn.close()
                return dict(_cache)
        rows = conn.execute(
            """
            SELECT c.meeting_id, c.start, c.speaker, c.text, c.vec FROM semantic_chunks c
            JOIN semantic_meta s ON s.meeting_id = c.meeting_id AND s.model = ?
            """,
            (model,),
        ).fetchall()
        conn.close()
    vecs = [np.frombuffer(r["vec"], dtype=np.float32) for r in rows]
    dim = max((v.size for v in vecs), default=0)
    matrix = np.stack([v for v in vecs if v.size == dim]) if vecs else np.zeros((0, 0), dtype=np.float32)
    keep = [r for r, v in zip(rows, vecs) if v.size == dim]
    data = {
        "key": key,
        "ids": np.asarray([int(r["meeting_id"]) for r in keep], dtype=np.int64),
        "starts": [float(r["start"]) for r in keep],
        "speakers": [r["speaker"] or "" for r in keep],
        "texts": [r["text"] for r in keep],
        "matrix": matrix,
    }
    with _cache_lock:
        _cache.update(data)
    return data


def search(question: str, meeting_ids: Optional[List[int]] = None, limit: int = 12) -> List[Dict[str, Any]]:
    """Chunks closest in meaning to the question: [{meeting_id, start, speaker, text, score}].
    Empty when the feature is off, Ollama is not running or nothing is indexed yet."""
    settings = get_all_settings()
    question = (question or "").strip()
    if not question or not enabled(settings) or meeting_ids == [] or not _ollama_up(settings):
        return []
    data = _load_matrix(model_name(settings))
    if not data["matrix"].size:
        return []
    try:
        q = embed([question], settings)[0]
    except Exception as e:
        _state["error"] = str(e)[:300]
        return []
    if q.size != data["matrix"].shape[1]:
        return []
    scores = data["matrix"] @ q
    if meeting_ids is not None:
        scores = np.where(np.isin(data["ids"], np.asarray(meeting_ids, dtype=np.int64)), scores, -1.0)
    order = np.argsort(-scores)[: limit * 2]
    out = []
    for i in order:
        if scores[i] < MIN_SCORE:
            break
        out.append({"meeting_id": int(data["ids"][i]), "start": data["starts"][i], "speaker": data["speakers"][i],
                    "text": data["texts"][i], "score": float(scores[i])})
        if len(out) >= limit:
            break
    return out

