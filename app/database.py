import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.hardware import get_hardware_info

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
UPLOADS_DIR = DATA_DIR / "uploads"
AUDIO_DIR = DATA_DIR / "audio"
MODELS_DIR = BASE_DIR / "models"
EXPORTS_DIR = DATA_DIR / "exports"

for d in (DATA_DIR, UPLOADS_DIR, AUDIO_DIR, MODELS_DIR, EXPORTS_DIR):
    d.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "meetings.db"
_db_lock = threading.Lock()


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    hw = get_hardware_info()
    with _db_lock:
        conn = get_connection()
        cur = conn.cursor()

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS meetings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                filename TEXT NOT NULL,
                media_path TEXT NOT NULL,
                audio_path TEXT DEFAULT '',
                duration REAL DEFAULT 0.0,
                status TEXT DEFAULT 'queued',
                progress INTEGER DEFAULT 0,
                stage_text TEXT DEFAULT 'В очереди на обработку...',
                error_message TEXT DEFAULT '',
                language TEXT DEFAULT 'ru',
                model_name TEXT DEFAULT 'large-v3-turbo',
                num_speakers_hint INTEGER DEFAULT 0,
                speakers_json TEXT DEFAULT '{}',
                segments_json TEXT DEFAULT '[]',
                summaries_json TEXT DEFAULT '{}',
                created_at TEXT NOT NULL,
                completed_at TEXT DEFAULT ''
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS dictionary (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                term TEXT NOT NULL UNIQUE,
                category TEXT DEFAULT 'Общее',
                active INTEGER DEFAULT 1,
                created_at TEXT NOT NULL
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )

        default_settings = {
            "whisper_model": hw["recommended_whisper_model"],
            "device": hw["recommended_device"],
            "compute_type": hw["recommended_compute_type"],
            "language": "auto",
            "diarization_enabled": "true",
            "num_speakers": "0",  # 0 = auto-detect
            "auto_summary": "true",
            "ollama_url": "http://localhost:11434",
            "ollama_model": hw["recommended_ollama_model"],
            "vad_filter": "true",
            "audio_enhance": "true",
            "clean_fillers": "true",
        }

        for k, v in default_settings.items():
            cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, str(v)))

        conn.commit()
        conn.close()


#Settings helpers
def get_all_settings() -> Dict[str, str]:
    with _db_lock:
        conn = get_connection()
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        conn.close()
        return {r["key"]: r["value"] for r in rows}


def update_settings(new_settings: Dict[str, Any]) -> Dict[str, str]:
    with _db_lock:
        conn = get_connection()
        for k, v in new_settings.items():
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (k, str(v)),
            )
        conn.commit()
        conn.close()
    return get_all_settings()


# Dictionary helpers
def list_dictionary(active_only: bool = False) -> List[Dict[str, Any]]:
    with _db_lock:
        conn = get_connection()
        if active_only:
            rows = conn.execute(
                "SELECT * FROM dictionary WHERE active = 1 ORDER BY category, term"
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM dictionary ORDER BY category, term").fetchall()
        conn.close()
        return [dict(r) for r in rows]


def add_dictionary_term(term: str, category: str = "Термин") -> Dict[str, Any]:
    term = term.strip()
    if not term:
        raise ValueError("Пустое слово")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _db_lock:
        conn = get_connection()
        conn.execute(
            """
            INSERT INTO dictionary (term, category, active, created_at)
            VALUES (?, ?, 1, ?)
            ON CONFLICT(term) DO UPDATE SET category = excluded.category, active = 1
            """,
            (term, category.strip() or "Термин", now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM dictionary WHERE term = ?", (term,)).fetchone()
        conn.close()
        return dict(row)


def toggle_dictionary_term(term_id: int, active: bool) -> None:
    with _db_lock:
        conn = get_connection()
        conn.execute("UPDATE dictionary SET active = ? WHERE id = ?", (1 if active else 0, term_id))
        conn.commit()
        conn.close()


def delete_dictionary_term(term_id: int) -> None:
    with _db_lock:
        conn = get_connection()
        conn.execute("DELETE FROM dictionary WHERE id = ?", (term_id,))
        conn.commit()
        conn.close()


def get_dictionary_prompt() -> str:
    terms = [item["term"] for item in list_dictionary(active_only=True)]
    if not terms:
        return ""
    return "Ключевые слова, имена и термины встречи: " + ", ".join(terms) + "."


# Meetings helpers
def create_meeting(
    title: str,
    filename: str,
    media_path: str,
    language: str = "auto",
    model_name: str = "large-v3-turbo",
    num_speakers_hint: int = 0,
) -> Dict[str, Any]:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _db_lock:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO meetings (
                title, filename, media_path, status, progress, stage_text,
                language, model_name, num_speakers_hint, created_at
            ) VALUES (?, ?, ?, 'queued', 0, 'В очереди на обработку...', ?, ?, ?, ?)
            """,
            (title, filename, media_path, language, model_name, num_speakers_hint, now),
        )
        meeting_id = cur.lastrowid
        conn.commit()
        row = conn.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
        conn.close()
        return _deserialize_meeting(dict(row))


def update_meeting_progress(
    meeting_id: int,
    status: Optional[str] = None,
    progress: Optional[int] = None,
    stage_text: Optional[str] = None,
    error_message: Optional[str] = None,
    duration: Optional[float] = None,
    audio_path: Optional[str] = None,
) -> None:
    fields = []
    values = []
    if status is not None:
        fields.append("status = ?")
        values.append(status)
        if status == "completed":
            fields.append("completed_at = ?")
            values.append(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    if progress is not None:
        fields.append("progress = ?")
        values.append(int(max(0, min(100, progress))))
    if stage_text is not None:
        fields.append("stage_text = ?")
        values.append(stage_text)
    if error_message is not None:
        fields.append("error_message = ?")
        values.append(error_message)
    if duration is not None:
        fields.append("duration = ?")
        values.append(float(duration))
    if audio_path is not None:
        fields.append("audio_path = ?")
        values.append(audio_path)

    if not fields:
        return

    values.append(meeting_id)
    with _db_lock:
        conn = get_connection()
        conn.execute(f"UPDATE meetings SET {', '.join(fields)} WHERE id = ?", tuple(values))
        conn.commit()
        conn.close()


def save_meeting_results(
    meeting_id: int,
    speakers: Optional[Dict[str, Any]] = None,
    segments: Optional[List[Dict[str, Any]]] = None,
    summaries: Optional[Dict[str, Any]] = None,
    language: Optional[str] = None,
) -> None:
    fields = []
    values = []
    if speakers is not None:
        fields.append("speakers_json = ?")
        values.append(json.dumps(speakers, ensure_ascii=False))
    if segments is not None:
        fields.append("segments_json = ?")
        values.append(json.dumps(segments, ensure_ascii=False))
    if summaries is not None:
        fields.append("summaries_json = ?")
        values.append(json.dumps(summaries, ensure_ascii=False))
    if language is not None:
        fields.append("language = ?")
        values.append(language)

    if not fields:
        return

    values.append(meeting_id)
    with _db_lock:
        conn = get_connection()
        conn.execute(f"UPDATE meetings SET {', '.join(fields)} WHERE id = ?", tuple(values))
        conn.commit()
        conn.close()


def get_meeting(meeting_id: int) -> Optional[Dict[str, Any]]:
    with _db_lock:
        conn = get_connection()
        row = conn.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
        conn.close()
        if not row:
            return None
        return _deserialize_meeting(dict(row))


def list_meetings() -> List[Dict[str, Any]]:
    with _db_lock:
        conn = get_connection()
        rows = conn.execute(
            """
            SELECT id, title, filename, duration, status, progress, stage_text,
                   error_message, language, model_name, num_speakers_hint,
                   speakers_json, created_at, completed_at
            FROM meetings
            ORDER BY id DESC
            """
        ).fetchall()
        conn.close()
        result = []
        for r in rows:
            d = dict(r)
            try:
                d["speakers"] = json.loads(d.pop("speakers_json", "{}") or "{}")
            except Exception:
                d["speakers"] = {}
            result.append(d)
        return result


def delete_meeting(meeting_id: int) -> bool:
    meeting = get_meeting(meeting_id)
    if not meeting:
        return False

    for path_key in ("media_path", "audio_path"):
        p_str = meeting.get(path_key)
        if p_str:
            p = Path(p_str)
            if p.exists() and DATA_DIR in p.resolve().parents:
                try:
                    p.unlink()
                except Exception:
                    pass

    with _db_lock:
        conn = get_connection()
        conn.execute("DELETE FROM meetings WHERE id = ?", (meeting_id,))
        conn.commit()
        conn.close()
    return True


def _deserialize_meeting(d: Dict[str, Any]) -> Dict[str, Any]:
    for key, target in (
        ("speakers_json", "speakers"),
        ("segments_json", "segments"),
        ("summaries_json", "summaries"),
    ):
        raw = d.pop(key, None)
        default_val = [] if target == "segments" else {}
        if raw:
            try:
                d[target] = json.loads(raw)
            except Exception:
                d[target] = default_val
        else:
            d[target] = default_val
    return d


def search_all_meetings(query: str, max_hits_per_meeting: int = 4) -> List[Dict[str, Any]]:
    """
    Performs a global full-text search across all meetings (titles, transcript segments, and AI summaries).
    Returns matching meetings with exact segment timestamps and highlighted text snippets.
    """
    q = (query or "").strip().lower()
    if not q:
        return []

    with _db_lock:
        conn = get_connection()
        rows = conn.execute("SELECT * FROM meetings ORDER BY id DESC").fetchall()
        conn.close()

    results: List[Dict[str, Any]] = []
    for row in rows:
        m = _deserialize_meeting(dict(row))
        title_match = q in (m.get("title") or "").lower()
        speakers = m.get("speakers") or {}
        segments = m.get("segments") or []

        matching_hits: List[Dict[str, Any]] = []
        for seg in segments:
            txt = seg.get("text") or ""
            if q in txt.lower():
                spk_id = seg.get("speaker") or "SPEAKER_00"
                spk_name = (speakers.get(spk_id) or {}).get("name") or spk_id
                matching_hits.append(
                    {
                        "segment_id": seg.get("id", 0),
                        "start": float(seg.get("start", 0.0)),
                        "speaker": spk_name,
                        "text": txt,
                    }
                )
                if len(matching_hits) >= max_hits_per_meeting:
                    break

        if title_match or matching_hits:
            results.append(
                {
                    "meeting_id": m["id"],
                    "title": m["title"],
                    "duration": m.get("duration", 0.0),
                    "status": m.get("status", "completed"),
                    "hits": matching_hits,
                }
            )

    return results


def merge_segment_with_previous(meeting_id: int, segment_id: int) -> Optional[Dict[str, Any]]:
    """
    Merges the specified segment with the immediately preceding segment,
    combining their text, word timestamps, and end time.
    """
    m = get_meeting(meeting_id)
    if not m:
        return None

    segments = m.get("segments") or []
    target_idx = -1
    for i, seg in enumerate(segments):
        if int(seg.get("id", -1)) == int(segment_id):
            target_idx = i
            break

    if target_idx <= 0:
        return m

    prev_seg = segments[target_idx - 1]
    curr_seg = segments[target_idx]

    prev_seg["end"] = round(float(curr_seg.get("end", prev_seg.get("end", 0.0))), 2)
    prev_seg["text"] = (
        (prev_seg.get("text") or "").rstrip() + " " + (curr_seg.get("text") or "").lstrip()
    ).strip()

    prev_words = list(prev_seg.get("words") or [])
    curr_words = list(curr_seg.get("words") or [])
    prev_seg["words"] = prev_words + curr_words

    segments.pop(target_idx)
    for idx, s in enumerate(segments):
        s["id"] = idx + 1

    save_meeting_results(meeting_id, segments=segments)
    return get_meeting(meeting_id)

