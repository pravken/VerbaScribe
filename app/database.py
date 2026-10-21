import json
import re
import os
import sqlite3
import threading
import uuid
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
        if "dubs_json" not in existing_cols:
            cur.execute("ALTER TABLE meetings ADD COLUMN dubs_json TEXT DEFAULT '[]'")
        if "translations_json" not in existing_cols:
            cur.execute("ALTER TABLE meetings ADD COLUMN translations_json TEXT DEFAULT '{}'")
        # Library folders, where a recording came from and who owns its file (see _MEETING_EXTRA_COLUMNS)
        for col, ddl in _MEETING_EXTRA_COLUMNS.items():
            if col not in existing_cols:
                cur.execute(f"ALTER TABLE meetings ADD COLUMN {col} {ddl}")

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS folders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                parent_id INTEGER DEFAULT NULL,
                system_key TEXT DEFAULT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        folder_cols = {r["name"] for r in cur.execute("PRAGMA table_info(folders)").fetchall()}
        if "local_only" not in folder_cols:
            cur.execute("ALTER TABLE folders ADD COLUMN local_only INTEGER DEFAULT 0")
        _ensure_system_folders(cur)
        if "folder_id" not in existing_cols:
            _sort_existing_meetings(cur)

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
            "record_source_mode": "both",
            "record_mic_device": "",
            "batched_inference": "true",  # faster-whisper BatchedInferencePipeline on GPU
            "keep_model_loaded": "true",  # reuse Whisper between queued recordings if VRAM allows
            "voice_profiles": "true",  # name known voices automatically (app/voice_profiles.py)
            "update_check": "true",  # look for a new release on GitHub at start (app/updater.py)
            "idle_unload_minutes": "10",  # free models from memory after this long unused; 0 = never
            "recordings_dir": "",  # where recordings made in the app are saved; "" = <data>/recordings
            "live_whisper_model": "auto",
            "record_hotkey": "Ctrl+Alt+R",
            "quiet_mode": "false",  # no tray notifications (run.py TrayController)
            "call_auto_record": "false",  # record calls by themselves (app/call_watch.py)
            "call_apps": "zoom,teams,telegram",
            "call_preset": "call",  # system-wide start/stop of a recording (app/tray.py); "" = off
            "semantic_search": "false",  # search by meaning with a local embedding model (app/semantic.py)
            "semantic_model": "bge-m3",  # captions while recording (app/live.py caption_model)
            "auto_title": "true",  # name recordings by their content (LLM) when the title is generic
            "obsidian_auto": "false",  # save a note to the Obsidian vault after processing
            "obsidian_vault": "",
            "obsidian_subfolder": "VerbaScribe",
            "obsidian_filename": "{date} {title}",
            "file_name_template": "{date} {title}",  # recordings and exports (app/naming.py)
            "date_folders": "off",  # off | day | month | year_month: folders by date (app/naming.py)
            "summary_templates": "[]",  # the user's own summary templates, JSON (summary_templates())
            "summary_grounding": "true",  # summaries checked against the transcript (app/grounding.py)
            "auto_commitments": "true",  # promises found after the automatic summary (app/commitments.py)
            "update_auto": "true",  # download updates by themselves, install on exit (app/updater.py)
            "obsidian_summary": "true",
            "obsidian_tasks": "true",
            "obsidian_chapters": "true",
            "obsidian_transcript": "true",
            "obsidian_generate": "true",  # make the overview and tasks for the note if they are missing
            "llm_provider": "ollama",  # analysis: "ollama" (local) or "cloud" (app/llm.py)
            "llm_cloud_provider": "openai",
            "llm_cloud_consent": "false",  # the user agreed that transcripts are sent to the cloud service
        }

        for k, v in default_settings.items():
            cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, str(v)))

        # One-time switch of the recording default from "microphone only" to "microphone + system"
        if not cur.execute("SELECT 1 FROM settings WHERE key = 'record_default_v2'").fetchone():
            cur.execute("UPDATE settings SET value = 'both' WHERE key = 'record_source_mode' AND value = 'mic'")
            cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('record_default_v2', '1')")

        # Search by meaning (app/semantic.py): transcript chunks with their embedding vectors, and
        # which recordings are indexed with which model. A changed transcript drops its meta row.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS semantic_chunks (
                meeting_id INTEGER NOT NULL,
                chunk INTEGER NOT NULL,
                start REAL NOT NULL,
                speaker TEXT DEFAULT '',
                text TEXT NOT NULL,
                vec BLOB NOT NULL,
                PRIMARY KEY (meeting_id, chunk)
            )
            """
        )
        cur.execute(
            "CREATE TABLE IF NOT EXISTS semantic_meta (meeting_id INTEGER PRIMARY KEY, model TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )

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

# Columns added after the first release, with their SQL type and default
_MEETING_EXTRA_COLUMNS = {
    "folder_id": "INTEGER DEFAULT NULL",
    "source": "TEXT DEFAULT 'import'",  # 'recording' (made in the app), 'import' (copied in), 'local' (used in place)
    "owns_media": "INTEGER DEFAULT 0",  # 1: the media file was created by the app and is deleted with the recording
    "recorded_at": "TEXT DEFAULT ''",  # when the meeting took place (file date for imports)
    "title_is_default": "INTEGER DEFAULT 0",  # 1: the title came from a file name and may be replaced
    "obsidian_path": "TEXT DEFAULT ''",
    "obsidian_hash": "TEXT DEFAULT ''",  # sha256 of the part of the note VerbaScribe wrote last
    "pending_exports_json": "TEXT DEFAULT '[]'",
    "exports_json": "TEXT DEFAULT '[]'",
    "user_notes": "TEXT DEFAULT ''",  # the user's own notes of the meeting (studio or "My notes" tab)  # files saved by exports (Save as), deleted with the recording on request
    "has_video": "INTEGER DEFAULT NULL",  # NULL: told from the file extension (see is_video)
    "local_only": "INTEGER DEFAULT 0",  # 1: never sent to a cloud model (see is_local_only)
    "media_size": "INTEGER DEFAULT NULL",  # bytes of the media file; NULL: not measured yet
    "series_id": "INTEGER DEFAULT NULL",  # a series of recurring meetings (app/series.py)
}

SYSTEM_FOLDERS = {"recordings": "Записи", "imports": "Импорт"}
_VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".m4v", ".webm", ".wmv", ".flv", ".mpg", ".mpeg", ".ts", ".mts", ".3gp"}


def _ensure_system_folders(cur: sqlite3.Cursor) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for key, name in SYSTEM_FOLDERS.items():
        if not cur.execute("SELECT 1 FROM folders WHERE system_key = ?", (key,)).fetchone():
            cur.execute("INSERT INTO folders (name, parent_id, system_key, created_at) VALUES (?, NULL, ?, ?)", (name, key, now))


def _sort_existing_meetings(cur: sqlite3.Cursor) -> None:
    """First start with folders: recordings made in the app go to "Recordings", everything else to
    "Imports". Older versions did not store the origin, so it is told from the file."""
    rec_id = cur.execute("SELECT id FROM folders WHERE system_key = 'recordings'").fetchone()[0]
    imp_id = cur.execute("SELECT id FROM folders WHERE system_key = 'imports'").fetchone()[0]
    uploads = str(UPLOADS_DIR.resolve()).casefold()
    for row in cur.execute("SELECT id, media_path, filename, created_at FROM meetings").fetchall():
        media = str(row[1] or "")
        in_uploads = media.casefold().startswith(uploads)
        name = Path(media).name
        # Recordings: the app's own mix ("<8 hex>_<title>.m4a") or the page's microphone file (.webm)
        recorded = in_uploads and (
            (name.lower().endswith(".m4a") and re.match(r"^[0-9a-f]{8}_", name))
            or (name.lower().endswith(".webm") and (row[2] or "").lower().endswith(".webm")
                and re.match(r"^[0-9a-f]{8}_(запись|recording)", name, re.IGNORECASE))
        )
        cur.execute(
            "UPDATE meetings SET folder_id = ?, source = ?, owns_media = ?, recorded_at = ? WHERE id = ?",
            (rec_id if recorded else imp_id, "recording" if recorded else ("import" if in_uploads else "local"),
             1 if in_uploads else 0, row[3] or "", row[0]),
        )


def is_video_path(path: str) -> bool:
    return Path(path or "").suffix.lower() in _VIDEO_EXTS


def is_video(row: Dict[str, Any]) -> bool:
    """A recording with a picture. Recordings made in the app are audio unless they captured the
    screen; for other files the extension decides (a .webm may be either, it is shown as video)."""
    if row.get("has_video") is not None:
        return bool(row["has_video"])
    return row.get("source") != "recording" and is_video_path(row.get("media_path") or "")


def _loads_list(raw: Optional[str]) -> List[Dict[str, Any]]:
    try:
        value = json.loads(raw or "[]")
        return value if isinstance(value, list) else []
    except Exception:
        return []


def _reindex_segments(conn: sqlite3.Connection, meeting_id: int, segments: List[Dict[str, Any]]) -> None:
    """Replaces the search index rows of one recording. Call inside the caller's transaction."""
    conn.execute("DELETE FROM segments_fts WHERE meeting_id = ?", (meeting_id,))
    # The meaning index of this recording is out of date: app/semantic.py indexes it again
    conn.execute("DELETE FROM semantic_meta WHERE meeting_id = ?", (meeting_id,))
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


def clear_dictionary() -> None:
    with _db_lock:
        conn = get_connection()
        conn.execute("DELETE FROM dictionary")
        conn.commit()
        conn.close()


def get_dictionary_hotwords() -> str:
    """Active vocabulary terms joined for faster-whisper's `hotwords` (applied to every 30s window)."""
    terms = [item["term"] for item in list_dictionary(active_only=True)]
    return ", ".join(terms)


# Meetings helpers
def _file_size(path: str) -> Optional[int]:
    try:
        return os.path.getsize(path) if path else None
    except OSError:
        return None


def create_meeting(
    title: str,
    filename: str,
    media_path: str,
    language: str = "auto",
    model_name: str = "large-v3-turbo",
    num_speakers_hint: int = 0,
    source: str = "import",
    folder_id: Optional[int] = None,
    owns_media: bool = False,
    recorded_at: str = "",
    title_is_default: bool = False,
    has_video: Optional[bool] = None,
) -> Dict[str, Any]:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _db_lock:
        conn = get_connection()
        cur = conn.cursor()
        if folder_id is None or not cur.execute("SELECT 1 FROM folders WHERE id = ?", (folder_id,)).fetchone():
            # New recordings land in the system folder of their kind
            key = "recordings" if source == "recording" else "imports"
            row = cur.execute("SELECT id FROM folders WHERE system_key = ?", (key,)).fetchone()
            folder_id = row[0] if row else None
        cur.execute(
            """
            INSERT INTO meetings (
                title, filename, media_path, status, progress, stage_text,
                language, model_name, num_speakers_hint, created_at,
                folder_id, source, owns_media, recorded_at, title_is_default, has_video, media_size
            ) VALUES (?, ?, ?, 'queued', 0, 'В очереди на обработку...', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (title, filename, media_path, language, model_name, num_speakers_hint, now,
             folder_id, source, 1 if owns_media else 0, recorded_at or now, 1 if title_is_default else 0,
             None if has_video is None else int(bool(has_video)), _file_size(media_path)),
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
    reindex: bool = True,
) -> None:
    """reindex=False: a partial transcript shown while recognizing (search is indexed at the end)."""
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
        if segments is not None and reindex:
            _reindex_segments(conn, meeting_id, segments)
        conn.commit()
        conn.close()


def summary_templates() -> List[Dict[str, Any]]:
    """Summary templates the user made: [{id, name, prompt}] (Settings → Analysis)."""
    try:
        items = json.loads(get_all_settings().get("summary_templates") or "[]")
    except ValueError:
        return []
    return [t for t in items if isinstance(t, dict) and t.get("id") and t.get("name") and t.get("prompt")]


def save_summary_templates(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    import re as _re

    clean = []
    for t in items[:30]:
        name, prompt = str(t.get("name") or "").strip()[:60], str(t.get("prompt") or "").strip()[:4000]
        tid = _re.sub(r"[^a-z0-9_-]", "", str(t.get("id") or "").lower())[:24]
        if name and prompt:
            clean.append({"id": tid or uuid.uuid4().hex[:8], "name": name, "prompt": prompt})
    update_settings({"summary_templates": json.dumps(clean, ensure_ascii=False)})
    return clean


def merge_summaries(meeting_id: int, updates: Dict[str, Any], remove: tuple = ()) -> Dict[str, Any]:
    """Sets some summaries of a recording, keeping the others: two analyses running at once (each
    takes minutes) must not overwrite each other's result. Returns all summaries."""
    with _db_lock:
        conn = get_connection()
        row = conn.execute("SELECT summaries_json FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
        if row is None:
            conn.close()
            return {}
        current = _loads_dict(row[0])
        current.update(updates)
        for k in remove:
            current.pop(k, None)
        conn.execute("UPDATE meetings SET summaries_json = ? WHERE id = ?",
                     (json.dumps(current, ensure_ascii=False), meeting_id))
        conn.commit()
        conn.close()
    return current


def save_segments_backup(meeting_id: int, segments: List[Dict[str, Any]]) -> None:
    """Keeps a copy of the segments before a destructive operation (e.g. filler cleanup)."""
    with _db_lock:
        conn = get_connection()
        conn.execute(
            "UPDATE meetings SET segments_backup_json = ? WHERE id = ?",
            (json.dumps(segments, ensure_ascii=False) if segments else "", meeting_id),
        )
        conn.commit()
        conn.close()


def filler_stats(meeting_id: int) -> Dict[str, Any]:
    """What the last cleanup removed, compared with the copy kept before it: how many words and
    how long they took to say. {"cleaned": False} when there is no such copy."""
    with _db_lock:
        conn = get_connection()
        row = conn.execute("SELECT segments_json, segments_backup_json FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
        conn.close()
    if not row or not row["segments_backup_json"]:
        return {"cleaned": False, "words": 0, "seconds": 0.0}

    def words(segs: List[Dict[str, Any]]) -> Dict[tuple, float]:
        out = {}
        for s in segs:
            for w in s.get("words") or []:
                key = (round(float(w.get("start") or 0), 2), str(w.get("word") or "").strip().casefold().strip(".,!?…"))
                out[key] = max(0.0, float(w.get("end") or 0) - float(w.get("start") or 0))
        return out

    before, after = words(_loads_list(row["segments_backup_json"])), words(_loads_list(row["segments_json"]))
    gone = [d for k, d in before.items() if k not in after]
    if not before:  # no word timings: count words of the text
        n_before = sum(len((s.get("text") or "").split()) for s in _loads_list(row["segments_backup_json"]))
        n_after = sum(len((s.get("text") or "").split()) for s in _loads_list(row["segments_json"]))
        return {"cleaned": True, "words": max(0, n_before - n_after), "seconds": 0.0}
    return {"cleaned": True, "words": len(gone), "seconds": round(sum(gone), 1)}


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
    allowed = {"title", "model_name", "language", "num_speakers_hint", "folder_id", "title_is_default", "obsidian_path", "obsidian_hash", "user_notes",
               "local_only", "series_id"}
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


_DUB_PATH_KEYS = ("path", "srt_path")


def _dub_paths_to_storage(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Dub files live in the data folder; store them relative to it so moving the folder keeps them."""
    out = dict(entry)
    for key in _DUB_PATH_KEYS:
        if out.get(key):
            try:
                out[key] = Path(out[key]).resolve().relative_to(DATA_DIR).as_posix()
            except ValueError:
                pass
    return out


def _dub_paths_from_storage(entry: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(entry)
    for key in _DUB_PATH_KEYS:
        if out.get(key) and not Path(out[key]).is_absolute():
            out[key] = str(DATA_DIR / out[key])
    return out


def save_meeting_dub(meeting_id: int, entry: Dict[str, Any]) -> None:
    """Stores the result of a dubbing job; a newer dub for the same language replaces the old entry."""
    with _db_lock:
        conn = get_connection()
        row = conn.execute("SELECT dubs_json FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
        if row is None:
            conn.close()
            return
        try:
            dubs = json.loads(row["dubs_json"] or "[]")
        except Exception:
            dubs = []
        replaced = [_dub_paths_from_storage(d) for d in dubs if d.get("lang") == entry.get("lang")]
        dubs = [d for d in dubs if d.get("lang") != entry.get("lang")] + [_dub_paths_to_storage(entry)]
        conn.execute("UPDATE meetings SET dubs_json = ? WHERE id = ?", (json.dumps(dubs, ensure_ascii=False), meeting_id))
        conn.commit()
        conn.close()
    for old in replaced:
        for key in ("path", "srt_path"):
            p = Path(old.get(key) or "")
            if p.name and p.exists() and DATA_DIR in p.resolve().parents and p.resolve() != Path(entry.get(key) or "").resolve():
                try:
                    p.unlink()
                except Exception:
                    pass


def get_meeting_translations_raw(meeting_id: int) -> Dict[str, Any]:
    """Stored translations as saved ({lang: entry}), see app/translation.py."""
    with _db_lock:
        conn = get_connection()
        row = conn.execute("SELECT translations_json FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
        conn.close()
    return _loads_dict(row["translations_json"] if row else None)


def save_meeting_translation(meeting_id: int, lang: str, entry: Optional[Dict[str, Any]]) -> None:
    """Writes (or with entry=None removes) the translation of one language."""
    with _db_lock:
        conn = get_connection()
        row = conn.execute("SELECT translations_json FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
        if row is None:
            conn.close()
            return
        data = _loads_dict(row["translations_json"])
        if entry is None:
            data.pop(lang, None)
        else:
            data[lang] = entry
        conn.execute(
            "UPDATE meetings SET translations_json = ? WHERE id = ?",
            (json.dumps(data, ensure_ascii=False), meeting_id),
        )
        conn.commit()
        conn.close()


def _loads_dict(raw: Optional[str]) -> Dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def get_meeting(meeting_id: int) -> Optional[Dict[str, Any]]:
    with _db_lock:
        conn = get_connection()
        row = conn.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
        conn.close()
        if not row:
            return None
        return _deserialize_meeting(dict(row))


_LIST_COLUMNS = """id, title, filename, media_path, duration, status, progress, stage_text,
    error_message, language, model_name, num_speakers_hint,
    speakers_json, created_at, completed_at, folder_id, source, recorded_at, has_video, local_only, media_size, series_id,
    obsidian_path, exports_json,
    segments_json NOT IN ('', '[]') AS has_transcript,
    summaries_json NOT IN ('', '{}') AS has_summary"""

# What a recording has been through, for the badges in the library: the languages of its
# translations and dubs (read inside SQLite, so the large JSON is not loaded for every row)
_LIST_LANGS_SQL = """,
    (SELECT group_concat(key) FROM json_each(CASE WHEN json_valid(translations_json) THEN translations_json ELSE '{}' END)) AS tr_langs,
    (SELECT group_concat(json_extract(value, '$.lang')) FROM json_each(CASE WHEN json_valid(dubs_json) THEN dubs_json ELSE '[]' END)) AS dub_langs"""


def meeting_briefs(ids: List[int]) -> Dict[int, Dict[str, Any]]:
    """Title and progress of a few recordings (the queue, polled often): no full library scan."""
    if not ids:
        return {}
    with _db_lock:
        conn = get_connection()
        rows = conn.execute(
            f"SELECT id, title, status, progress, stage_text FROM meetings WHERE id IN ({','.join('?' * len(ids))})",
            [int(i) for i in ids]).fetchall()
        conn.close()
    return {r["id"]: dict(r) for r in rows}


def list_meetings() -> List[Dict[str, Any]]:
    with _db_lock:
        conn = get_connection()
        try:
            rows = conn.execute(f"SELECT {_LIST_COLUMNS}{_LIST_LANGS_SQL} FROM meetings ORDER BY id DESC").fetchall()
        except sqlite3.OperationalError:  # SQLite without JSON functions
            rows = conn.execute(f"SELECT {_LIST_COLUMNS}, translations_json, dubs_json FROM meetings ORDER BY id DESC").fetchall()
        conn.close()
        result = []
        unmeasured = []
        for r in rows:
            d = dict(r)
            if "translations_json" in d:
                d["tr_langs"] = ",".join(_loads_dict(d.pop("translations_json")))
                try:
                    d["dub_langs"] = ",".join(str(x.get("lang")) for x in json.loads(d.pop("dubs_json") or "[]"))
                except Exception:
                    d["dub_langs"] = ""
            d["translations_langs"] = [x for x in (d.pop("tr_langs") or "").split(",") if x]
            d["dub_langs"] = [x for x in (d.pop("dub_langs") or "").split(",") if x and x != "None"]
            d["has_transcript"] = bool(d["has_transcript"])
            d["local_only"] = bool(d.get("local_only"))
            d["has_summary"] = bool(d["has_summary"])
            try:
                d["speakers"] = json.loads(d.pop("speakers_json", "{}") or "{}")
            except Exception:
                d["speakers"] = {}
            d["is_video"] = is_video(d)
            d["exports"] = [p for p in _loads_list(d.pop("exports_json", None)) if isinstance(p, str)]
            # Measured once and kept: the list must not touch every file (a network drive is slow)
            if d.get("media_size") is None:
                d["media_size"] = _file_size(d.get("media_path") or "")
                if d["media_size"] is not None:
                    unmeasured.append((d["media_size"], d["id"]))
            d["size_bytes"] = d.pop("media_size") or 0
            d.pop("media_path", None)
            result.append(d)
    if unmeasured:
        with _db_lock:
            conn = get_connection()
            conn.executemany("UPDATE meetings SET media_size = ? WHERE id = ?", unmeasured)
            conn.commit()
            conn.close()
    return result


def add_export_path(meeting_id: int, path: str) -> None:
    with _db_lock:
        conn = get_connection()
        row = conn.execute("SELECT exports_json FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
        if row is not None:
            items = [p for p in _loads_list(row[0]) if p != path] + [path]
            conn.execute("UPDATE meetings SET exports_json = ? WHERE id = ?",
                         (json.dumps(items[-200:], ensure_ascii=False), meeting_id))
            conn.commit()
        conn.close()


def delete_meeting(meeting_id: int, with_source: bool = False, with_exports: bool = False,
                   with_note: bool = False) -> bool:
    """with_source: the media file used in place (added by its path) is deleted too; with_exports: the
    files its exports saved; with_note: its Obsidian note."""
    meeting = get_meeting(meeting_id)
    if not meeting:
        return False
    extra = (meeting.get("exports") or []) if with_exports else []
    if with_note and meeting.get("obsidian_path"):
        extra.append(meeting["obsidian_path"])
    for p_str in extra:
        try:
            Path(p_str).unlink(missing_ok=True)
        except OSError as e:
            print(f"[delete] {p_str}: {e}", flush=True)
    if with_source and meeting.get("source") == "local" and meeting.get("media_path"):
        try:
            Path(meeting["media_path"]).unlink(missing_ok=True)
        except OSError as e:
            print(f"[delete] source file of meeting {meeting_id}: {e}", flush=True)

    dub_paths = [d.get(k) for d in meeting.get("dubs") or [] for k in ("path", "srt_path")]
    owned = [meeting.get("media_path")] if meeting.get("owns_media") else []
    for p_str in [meeting.get("media_path"), meeting.get("audio_path"), *dub_paths]:
        if p_str:
            p = Path(p_str)
            # Files in the data folder, and the app's own recordings wherever the recordings folder is;
            # a file used in place (added by its path) is never touched
            if p.exists() and (DATA_DIR in p.resolve().parents or p_str in owned):
                try:
                    p.unlink()
                except Exception:
                    pass
    thumb = DATA_DIR / "thumbs" / f"{meeting_id}.jpg"
    thumb.unlink(missing_ok=True)

    with _db_lock:
        conn = get_connection()
        conn.execute("DELETE FROM meetings WHERE id = ?", (meeting_id,))
        conn.execute("DELETE FROM segments_fts WHERE meeting_id = ?", (meeting_id,))
        conn.execute("DELETE FROM semantic_chunks WHERE meeting_id = ?", (meeting_id,))
        conn.execute("DELETE FROM semantic_meta WHERE meeting_id = ?", (meeting_id,))
        try:
            conn.execute("DELETE FROM commitments WHERE meeting_id = ?", (meeting_id,))
        except sqlite3.OperationalError:
            pass  # no commitments table yet (app/commitments.py creates it on first use)
        conn.commit()
        conn.close()
    events.publish({"type": "list"})
    return True


def _deserialize_meeting(d: Dict[str, Any]) -> Dict[str, Any]:
    d["has_segments_backup"] = bool(d.pop("segments_backup_json", ""))
    d["pending_exports"] = _loads_list(d.pop("pending_exports_json", None))
    d["exports"] = [p for p in _loads_list(d.pop("exports_json", None)) if isinstance(p, str)]
    d["is_video"] = is_video(d)
    d["local_only"] = bool(d.get("local_only"))
    for key, target in (
        ("speakers_json", "speakers"),
        ("segments_json", "segments"),
        ("summaries_json", "summaries"),
        ("dubs_json", "dubs"),
    ):
        raw = d.pop(key, None)
        default_val = [] if target in ("segments", "dubs") else {}
        if raw:
            try:
                d[target] = json.loads(raw)
            except Exception:
                d[target] = default_val
        else:
            d[target] = default_val
    d["dubs"] = [_dub_paths_from_storage(x) for x in d.get("dubs") or [] if isinstance(x, dict)]
    # Translations are matched to the current segments here, so every response carries their state
    from app.translation import resolve_all

    d["translations"] = resolve_all(d["segments"], _loads_dict(d.pop("translations_json", None)))
    return d


def _substring_hits(conn: sqlite3.Connection, q_fold: str, max_hits_per_meeting: int) -> List[sqlite3.Row]:
    """Slow path: plain substring search over the stored segment texts (no JSON parsing)."""
    hits: List[sqlite3.Row] = []
    per_meeting: Dict[int, int] = {}
    for r in conn.execute(
        "SELECT meeting_id, segment_id, start, speaker, text FROM segments_fts ORDER BY meeting_id DESC, start"
    ):
        mid = int(r["meeting_id"])
        if per_meeting.get(mid, 0) < max_hits_per_meeting and q_fold in (r["text"] or "").casefold():
            hits.append(r)
            per_meeting[mid] = per_meeting.get(mid, 0) + 1
    return hits


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
        if not hit_rows:
            # The index matches whole words and word beginnings only. For fragments from the middle
            # of a word ("рщ с") fall back to a case-insensitive substring scan of the indexed text.
            hit_rows = _substring_hits(conn, q_fold, max_hits_per_meeting)
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



# --- Library folders ---
def list_folders() -> List[Dict[str, Any]]:
    with _db_lock:
        conn = get_connection()
        rows = conn.execute("SELECT id, name, parent_id, system_key, local_only FROM folders ORDER BY id").fetchall()
        conn.close()
    return [{**dict(r), "local_only": bool(r["local_only"])} for r in rows]


def get_folder(folder_id: int) -> Optional[Dict[str, Any]]:
    return next((f for f in list_folders() if f["id"] == folder_id), None)


def system_folder_id(key: str) -> Optional[int]:
    return next((f["id"] for f in list_folders() if f.get("system_key") == key), None)


def is_local_only(meeting: Optional[Dict[str, Any]]) -> bool:
    """A recording that must not be sent to a cloud model: marked itself, or in a folder (at any
    depth) that is marked."""
    if not meeting:
        return False
    if meeting.get("local_only"):
        return True
    folders = {f["id"]: f for f in list_folders()}
    fid, seen = meeting.get("folder_id"), set()
    while fid is not None and fid in folders and fid not in seen:
        seen.add(fid)
        if folders[fid].get("local_only"):
            return True
        fid = folders[fid].get("parent_id")
    return False


def local_only_ids() -> set:
    """Ids of every recording that must not go to a cloud model (see is_local_only)."""
    folders = {f["id"]: f for f in list_folders()}

    def marked(fid: Any) -> bool:
        seen = set()
        while fid is not None and fid in folders and fid not in seen:
            seen.add(fid)
            if folders[fid].get("local_only"):
                return True
            fid = folders[fid].get("parent_id")
        return False

    with _db_lock:
        conn = get_connection()
        rows = conn.execute("SELECT id, folder_id, local_only FROM meetings").fetchall()
        conn.close()
    return {int(r["id"]) for r in rows if r["local_only"] or marked(r["folder_id"])}


def folder_subtree_ids(folder_id: int) -> List[int]:
    """The folder and every folder inside it."""
    folders = list_folders()
    out = [folder_id]
    i = 0
    while i < len(out):
        out.extend(f["id"] for f in folders if f["parent_id"] == out[i] and f["id"] not in out)
        i += 1
    return out


def create_folder(name: str, parent_id: Optional[int] = None) -> Dict[str, Any]:
    name = (name or "").strip()[:120]
    if not name:
        raise ValueError("Название папки не может быть пустым")
    if parent_id is not None and not get_folder(parent_id):
        raise ValueError("Родительская папка не найдена")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _db_lock:
        conn = get_connection()
        cur = conn.execute(
            "INSERT INTO folders (name, parent_id, system_key, created_at) VALUES (?, ?, NULL, ?)", (name, parent_id, now)
        )
        folder_id = cur.lastrowid
        conn.commit()
        conn.close()
    events.publish({"type": "list"})
    return get_folder(folder_id) or {}


_UNSET = object()


def update_folder(folder_id: int, name: Optional[str] = None, parent_id: Any = _UNSET,
                  local_only: Optional[bool] = None) -> Dict[str, Any]:
    folder = get_folder(folder_id)
    if not folder:
        raise LookupError("Папка не найдена")
    fields: Dict[str, Any] = {}
    if local_only is not None:
        fields["local_only"] = 1 if local_only else 0
    if name is not None:
        name = name.strip()[:120]
        if not name:
            raise ValueError("Название папки не может быть пустым")
        fields["name"] = name
    if parent_id is not _UNSET:
        if folder.get("system_key") and parent_id is not None:
            raise ValueError("Системную папку нельзя вложить в другую")
        if parent_id is not None:
            if not get_folder(parent_id):
                raise ValueError("Папка назначения не найдена")
            if parent_id in folder_subtree_ids(folder_id):
                raise ValueError("Папку нельзя переместить внутрь неё самой")
        fields["parent_id"] = parent_id
    if fields:
        with _db_lock:
            conn = get_connection()
            conn.execute(
                f"UPDATE folders SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
                (*fields.values(), folder_id),
            )
            conn.commit()
            conn.close()
        events.publish({"type": "list"})
    return get_folder(folder_id) or {}


def delete_folder(folder_id: int) -> None:
    """Removes a folder; what it contained (recordings and folders) moves up one level."""
    folder = get_folder(folder_id)
    if not folder:
        raise LookupError("Папка не найдена")
    if folder.get("system_key"):
        raise ValueError("Системную папку удалить нельзя")
    with _db_lock:
        conn = get_connection()
        conn.execute("UPDATE folders SET parent_id = ? WHERE parent_id = ?", (folder["parent_id"], folder_id))
        conn.execute("UPDATE meetings SET folder_id = ? WHERE folder_id = ?", (folder["parent_id"], folder_id))
        conn.execute("DELETE FROM folders WHERE id = ?", (folder_id,))
        conn.commit()
        conn.close()
    events.publish({"type": "list"})


def set_meeting_folder(meeting_id: int, folder_id: Optional[int]) -> None:
    if folder_id is not None and not get_folder(folder_id):
        raise ValueError("Папка не найдена")
    update_meeting_fields(meeting_id, folder_id=folder_id)


def meeting_ids_in_folder(folder_id: int) -> List[int]:
    ids = folder_subtree_ids(folder_id)
    with _db_lock:
        conn = get_connection()
        rows = conn.execute(
            f"SELECT id FROM meetings WHERE folder_id IN ({','.join('?' * len(ids))}) ORDER BY id DESC", ids
        ).fetchall()
        conn.close()
    return [int(r[0]) for r in rows]


# --- Exports waiting for a job (a translation, a dub) to finish ---
def get_pending_exports(meeting_id: int) -> List[Dict[str, Any]]:
    with _db_lock:
        conn = get_connection()
        row = conn.execute("SELECT pending_exports_json FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
        conn.close()
    return _loads_list(row[0] if row else None)


def set_pending_exports(meeting_id: int, items: List[Dict[str, Any]]) -> None:
    with _db_lock:
        conn = get_connection()
        conn.execute(
            "UPDATE meetings SET pending_exports_json = ? WHERE id = ?", (json.dumps(items, ensure_ascii=False), meeting_id)
        )
        conn.commit()
        conn.close()
