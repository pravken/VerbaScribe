import json
import re
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from app import events
from app.hardware import get_hardware_info
from app.paths import (  # noqa: F401  (re-exported for older imports)
    APP_DIR as BASE_DIR,
    AUDIO_DIR,
    DATA_DIR,
    DB_PATH,
    EXPORTS_DIR,
    MODELS_DIR,
    UPLOADS_DIR,
)

_db_lock = threading.Lock()


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    hw = get_hardware_info()
    with _db_lock:
        conn = get_connection()
        # WAL lets the UI read while the worker writes progress updates
        conn.execute("PRAGMA journal_mode=WAL")
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
                segments_backup_json TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                completed_at TEXT DEFAULT ''
            )
            """
        )

        # Migrations for databases created by earlier versions
        existing_cols = {r["name"] for r in cur.execute("PRAGMA table_info(meetings)").fetchall()}
        if "segments_backup_json" not in existing_cols:
            cur.execute("ALTER TABLE meetings ADD COLUMN segments_backup_json TEXT DEFAULT ''")

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
            "record_source_mode": "mic",
            "record_mic_device": "",
            "batched_inference": "true",  # faster-whisper BatchedInferencePipeline on GPU
            "keep_model_loaded": "true",  # reuse Whisper between queued recordings if VRAM allows
        }

        for k, v in default_settings.items():
            cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, str(v)))

        # Full-text index of transcript segments for the global search (SQLite FTS5).
        # Only `text` is indexed; the other columns are stored to build results without JSON parsing.
        cur.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS segments_fts USING fts5(
                text,
                meeting_id UNINDEXED,
                segment_id UNINDEXED,
                start UNINDEXED,
                speaker UNINDEXED,
                tokenize = 'unicode61 remove_diacritics 2'
            )
            """
        )
        if cur.execute("PRAGMA user_version").fetchone()[0] < _FTS_SCHEMA_VERSION:
            # First start with the index (or a newer index format): build it from stored transcripts
            cur.execute("DELETE FROM segments_fts")
            for row in cur.execute("SELECT id, segments_json FROM meetings").fetchall():
                _reindex_segments(conn, row["id"], _loads_list(row["segments_json"]))
            cur.execute(f"PRAGMA user_version = {_FTS_SCHEMA_VERSION}")

        conn.commit()
        conn.close()


_FTS_SCHEMA_VERSION = 1


def _loads_list(raw: Optional[str]) -> List[Dict[str, Any]]:
    try:
        value = json.loads(raw or "[]")
        return value if isinstance(value, list) else []
    except Exception:
        return []


def _reindex_segments(conn: sqlite3.Connection, meeting_id: int, segments: List[Dict[str, Any]]) -> None:
    """Replaces the search index rows of one recording. Call inside the caller's transaction."""
    conn.execute("DELETE FROM segments_fts WHERE meeting_id = ?", (meeting_id,))
    conn.executemany(
        "INSERT INTO segments_fts (text, meeting_id, segment_id, start, speaker) VALUES (?, ?, ?, ?, ?)",
        [
            (
                seg.get("text") or "",
                meeting_id,
                int(seg.get("id", 0) or 0),
                float(seg.get("start", 0.0) or 0.0),
                seg.get("speaker") or "SPEAKER_00",
            )
            for seg in segments
            if (seg.get("text") or "").strip()
        ],
    )


# Settings helpers
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


def get_dictionary_hotwords() -> str:
    """Active vocabulary terms joined for faster-whisper's `hotwords` (applied to every 30s window)."""
    terms = [item["term"] for item in list_dictionary(active_only=True)]
    return ", ".join(terms)


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
    events.publish({"type": "list"})
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

    event: Dict[str, Any] = {"type": "progress", "id": meeting_id}
    for key, value in (
        ("status", status),
        ("progress", None if progress is None else int(max(0, min(100, progress)))),
        ("stage_text", stage_text),
        ("error_message", error_message),
        ("duration", duration),
    ):
        if value is not None:
            event[key] = value
    events.publish(event)


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
        if segments is not None:
            _reindex_segments(conn, meeting_id, segments)
        conn.commit()
        conn.close()


def save_segments_backup(meeting_id: int, segments: List[Dict[str, Any]]) -> None:
    """Keeps a copy of the segments before a destructive operation (e.g. filler cleanup)."""
    with _db_lock:
        conn = get_connection()
        conn.execute(
            "UPDATE meetings SET segments_backup_json = ? WHERE id = ?",
            (json.dumps(segments, ensure_ascii=False), meeting_id),
        )
        conn.commit()
        conn.close()


def restore_segments_backup(meeting_id: int) -> bool:
    with _db_lock:
        conn = get_connection()
        row = conn.execute(
            "SELECT segments_backup_json FROM meetings WHERE id = ?", (meeting_id,)
        ).fetchone()
        if not row or not row["segments_backup_json"]:
            conn.close()
            return False
        conn.execute(
            "UPDATE meetings SET segments_json = segments_backup_json, segments_backup_json = '' WHERE id = ?",
            (meeting_id,),
        )
        _reindex_segments(conn, meeting_id, _loads_list(row["segments_backup_json"]))
        conn.commit()
        conn.close()
    return True


def update_meeting_fields(meeting_id: int, **fields: Any) -> None:
    allowed = {"title", "model_name", "language", "num_speakers_hint"}
    items = [(k, v) for k, v in fields.items() if k in allowed]
    if not items:
        return
    with _db_lock:
        conn = get_connection()
        conn.execute(
            f"UPDATE meetings SET {', '.join(f'{k} = ?' for k, _ in items)} WHERE id = ?",
            tuple(v for _, v in items) + (meeting_id,),
        )
        conn.commit()
        conn.close()
    events.publish({"type": "list"})


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
        conn.execute("DELETE FROM segments_fts WHERE meeting_id = ?", (meeting_id,))
        conn.commit()
        conn.close()
    events.publish({"type": "list"})
    return True


def _deserialize_meeting(d: Dict[str, Any]) -> Dict[str, Any]:
    d["has_segments_backup"] = bool(d.pop("segments_backup_json", ""))
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


def _fts_match_expression(query: str) -> str:
    """
    Turns free user input into a safe FTS5 query: every word becomes a quoted prefix term and all
    terms must match ("встреч бюдж" finds "встреча по бюджету"). Operators typed by the user are
    treated as plain text.
    """
    tokens = re.findall(r"\w+", query.casefold())
    return " ".join(f'"{t}"*' for t in tokens[:12])


def search_all_meetings(query: str, max_hits_per_meeting: int = 4) -> List[Dict[str, Any]]:
    """
    Global search across all recordings: titles (substring, case-insensitive) and transcript
    segments (FTS5 word-prefix search). Returns matching recordings with segment timestamps.
    """
    q = (query or "").strip()
    if not q:
        return []
    match = _fts_match_expression(q)
    q_fold = q.casefold()

    with _db_lock:
        conn = get_connection()
        meetings = conn.execute(
            "SELECT id, title, duration, status, speakers_json FROM meetings ORDER BY id DESC"
        ).fetchall()
        hit_rows = []
        if match:
            hit_rows = conn.execute(
                """
                SELECT meeting_id, segment_id, start, speaker, text FROM (
                    SELECT meeting_id, segment_id, start, speaker, text,
                           ROW_NUMBER() OVER (PARTITION BY meeting_id ORDER BY start) AS rn
                    FROM segments_fts WHERE segments_fts MATCH ?
                ) WHERE rn <= ?
                ORDER BY meeting_id DESC, start
                """,
                (match, max_hits_per_meeting),
            ).fetchall()
        conn.close()

    hits_by_meeting: Dict[int, List[sqlite3.Row]] = {}
    for r in hit_rows:
        hits_by_meeting.setdefault(int(r["meeting_id"]), []).append(r)

    results: List[Dict[str, Any]] = []
    for m in meetings:
        mid = int(m["id"])
        rows = hits_by_meeting.get(mid, [])
        if not rows and q_fold not in (m["title"] or "").casefold():
            continue
        try:
            speakers = json.loads(m["speakers_json"] or "{}")
        except Exception:
            speakers = {}
        results.append(
            {
                "meeting_id": mid,
                "title": m["title"],
                "duration": m["duration"] or 0.0,
                "status": m["status"] or "completed",
                "hits": [
                    {
                        "segment_id": int(r["segment_id"]),
                        "start": float(r["start"]),
                        "speaker": (speakers.get(r["speaker"]) or {}).get("name") or r["speaker"],
                        "text": r["text"],
                    }
                    for r in rows
                ],
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

