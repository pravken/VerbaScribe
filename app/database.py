import json
import re
import os
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

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

# Reentrant: a change made under the lock (edit_meeting) may read the settings or a voice profile
_db_lock = threading.RLock()


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    try:
        from app import backup

        backup.apply_pending()  # a backup restored in Settings takes the place of this database
    except Exception as e:
        print(f"[backup] restore failed: {e}", flush=True)
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
        # Sorted once. The column is added in autocommit and the sorting committed at the end: a
        # first start cut off in between left the column without the sorting, so the marker decides
        # (a library with folders but no marker was sorted by an older version)
        cur.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        sorted_once = cur.execute("SELECT 1 FROM settings WHERE key = 'folders_sorted_v1'").fetchone()
        if not sorted_once and ("folder_id" not in existing_cols
                                or not cur.execute("SELECT 1 FROM meetings WHERE folder_id IS NOT NULL LIMIT 1").fetchone()):
            _sort_existing_meetings(cur)
        cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('folders_sorted_v1', '1')")

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
            "onnx_gpu": "true",  # GigaAM / Parakeet jobs on the NVIDIA GPU, its ONNX Runtime fetched once (app/onnx_gpu.py)
            "onnx_gpu_batch": "32",  # phrases per batch on the GPU (app/asr_onnx.gpu_tuning)
            "onnx_gpu_vad": "false",  # the VAD on the GPU too
            "voice_profiles": "true",  # name known voices automatically (app/voice_profiles.py)
            "update_check": "true",  # look for a new release on GitHub at start (app/updater.py)
            "idle_unload_minutes": "10",  # free models from memory after this long unused; 0 = never
            "recordings_dir": "",  # where recordings made in the app are saved; "" = <data>/recordings
            "live_whisper_model": "auto",
            "record_hotkey": "Ctrl+Alt+R",
            "dictation_hotkey": "Ctrl+Alt+D",  # dictation into any program (app/dictation.py); empty: off
            "dictation_language": "auto",
            "dictation_translate_hotkey": "Ctrl+Alt+T",  # dictation typed in another language; empty: off
            "dictation_translate_to": "en",
            "dictation_save": "true",
            "start_with_windows": "false",  # in the tray, without a window, when the user signs in (app/autostart.py)
            "close_to_tray": "false",  # closing the window keeps the app in the tray (quit from its menu)  # each dictation is kept in the library, folder "Dictations"
            "dictation_translate_cloud": "false",  # dictation is translated by the local model unless allowed
            "quiet_mode": "false",  # no tray notifications (run.py TrayController)
            "call_auto_record": "false",  # record calls by themselves (app/call_watch.py)
            "call_apps": "zoom,teams,telegram",
            "call_preset": "call",  # system-wide start/stop of a recording (app/tray.py); "" = off
            "semantic_search": "false",  # search by meaning with a local embedding model (app/semantic.py)
            "semantic_model": "bge-m3",  # the embedding model of search by meaning (Ollama)
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
            "delete_media_after": "false",  # the sound deleted once a recording is transcribed (privacy)
            "media_retention_days": "0",  # the sound of older recordings deleted; 0: kept
            "update_auto": "true",  # download updates by themselves, install on exit (app/updater.py)
            "update_beta": "false",  # offer beta versions (pre-releases) too: for testing before a release
            "calendar_ics": "",  # an .ics file or link: titles and participants of recordings (app/calendar_ics.py)
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
        if "stale" not in {r["name"] for r in cur.execute("PRAGMA table_info(semantic_meta)").fetchall()}:
            # 1: the transcript changed since it was indexed (its chunks still answer until it is redone)
            cur.execute("ALTER TABLE semantic_meta ADD COLUMN stale INTEGER DEFAULT 0")

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
        # The transcript: one row per line (see _write_segments), not one JSON in the recording's row
        cur.execute(_SEGMENTS_DDL)
        cur.execute(_VERSIONS_DDL)
        cur.execute("DROP TRIGGER IF EXISTS meetings_flags_segments")  # has_transcript is set by _write_segments
        for ddl in _FLAG_TRIGGERS:
            cur.execute(ddl)
        if not cur.execute("SELECT 1 FROM settings WHERE key = 'meeting_flags_v1'").fetchone():
            # Recordings made before the triggers existed (read once)
            cur.execute(f"UPDATE meetings SET has_transcript = ({_HAS_TRANSCRIPT.format(row='meetings')} "
                        "OR EXISTS (SELECT 1 FROM segments WHERE segments.meeting_id = meetings.id)), "
                        f"has_summary = {_HAS_SUMMARY.format(row='meetings')}")
            cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('meeting_flags_v1', '1')")
        moved = _move_segments_to_table(conn)
        # Transcripts made before the versions: by the model the recording names
        cur.execute("UPDATE meetings SET transcript_model = model_name, transcript_at = COALESCE(completed_at, '') "
                    "WHERE COALESCE(transcript_model, '') = '' AND has_transcript = 1")
        if cur.execute("PRAGMA user_version").fetchone()[0] < _FTS_SCHEMA_VERSION:
            # First start with the index (or a newer index format): build it from stored transcripts
            cur.execute("DELETE FROM segments_fts")
            for row in cur.execute("SELECT id FROM meetings").fetchall():
                _reindex_segments(conn, row["id"], _read_segments(conn, row["id"]))
            cur.execute(f"PRAGMA user_version = {_FTS_SCHEMA_VERSION}")

        conn.commit()
        if moved:
            # The transcripts' old place is free space in the file: given back to the disk once
            try:
                conn.execute("VACUUM")
            except sqlite3.Error as e:  # not enough room for the copy VACUUM makes: the space is reused later
                print(f"[data] database not compacted: {e}", flush=True)
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
    "exports_json": "TEXT DEFAULT '[]'",  # files saved by exports (Save as), deleted with the recording on request
    "user_notes": "TEXT DEFAULT ''",  # the user's own notes of the meeting (studio or "My notes" tab)
    "media_removed": "INTEGER DEFAULT 0",  # 1: the sound was deleted for privacy, the transcript kept
    "transcript_verified": "INTEGER DEFAULT 0",  # 1: the user checked and corrected it: a reference for app/bench.py
    "has_video": "INTEGER DEFAULT NULL",  # NULL: told from the file extension (see is_video)
    "local_only": "INTEGER DEFAULT 0",  # 1: never sent to a cloud model (see is_local_only)
    "media_size": "INTEGER DEFAULT NULL",  # bytes of the media file; NULL: not measured yet
    "series_id": "INTEGER DEFAULT NULL",  # a series of recurring meetings (app/series.py)
    # Whether there is a transcript / a summary, kept by triggers (_FLAG_TRIGGERS): the library list
    # reads these instead of the transcripts themselves (megabytes per recording)
    "has_transcript": "INTEGER DEFAULT 0",
    "has_summary": "INTEGER DEFAULT 0",
    # The model the transcript shown was made with, and when (the transcripts of other models are in
    # transcript_versions)
    "processing_json": "TEXT DEFAULT '{}'",  # what is done to the sound and text when transcribed (see effective_processing)
    "transcript_model": "TEXT DEFAULT ''",
    "transcript_at": "TEXT DEFAULT ''",
}

_HAS_TRANSCRIPT = "(COALESCE({row}.segments_json, '') NOT IN ('', '[]'))"  # recordings stored before the segments table
_HAS_SUMMARY = "(COALESCE({row}.summaries_json, '') NOT IN ('', '{{}}'))"
_FLAG_TRIGGERS = [
    f"""CREATE TRIGGER IF NOT EXISTS meetings_flags_insert AFTER INSERT ON meetings BEGIN
        UPDATE meetings SET has_transcript = {_HAS_TRANSCRIPT.format(row="NEW")},
            has_summary = {_HAS_SUMMARY.format(row="NEW")} WHERE id = NEW.id; END""",
    f"""CREATE TRIGGER IF NOT EXISTS meetings_flags_summaries AFTER UPDATE OF summaries_json ON meetings BEGIN
        UPDATE meetings SET has_summary = {_HAS_SUMMARY.format(row="NEW")} WHERE id = NEW.id; END""",
]

# A transcript is a list of lines (segments); each is stored as its own row, in order. The row of the
# recording stays small, so the library list reads it fast, and an edit of one line writes one row.
# `data` is the line as it is (JSON: start, end, speaker, text, words, ...), nothing is lost.
_SEGMENTS_DDL = """CREATE TABLE IF NOT EXISTS segments (
    meeting_id INTEGER NOT NULL,
    pos INTEGER NOT NULL,
    data TEXT NOT NULL,
    PRIMARY KEY (meeting_id, pos)
) WITHOUT ROWID"""


# The transcripts of a recording made with other models, one per model, to compare them and switch to
# one (switch_transcript_version). The one shown is in `segments` (meetings.transcript_model): search,
# analysis and exports read it as before. `data` (JSON) is what belongs to that text: segments,
# speakers, translations, the copy before the filler cleanup, language, verified flag, date.
_VERSIONS_DDL = """CREATE TABLE IF NOT EXISTS transcript_versions (
    meeting_id INTEGER NOT NULL,
    model TEXT NOT NULL,
    data TEXT NOT NULL,
    PRIMARY KEY (meeting_id, model)
) WITHOUT ROWID"""


def _read_segments(conn: sqlite3.Connection, meeting_id: int) -> List[Dict[str, Any]]:
    out = []
    for (data,) in conn.execute("SELECT data FROM segments WHERE meeting_id = ? ORDER BY pos", (meeting_id,)):
        try:
            seg = json.loads(data)
        except ValueError:
            continue
        if isinstance(seg, dict):
            out.append(seg)
    return out


def _segment_rows(segments: List[Dict[str, Any]]) -> List[str]:
    return [json.dumps(seg, ensure_ascii=False) for seg in segments]


def _write_segments(conn: sqlite3.Connection, meeting_id: int, segments: List[Dict[str, Any]],
                    before: Optional[List[str]] = None) -> None:
    """Stores the transcript of a recording; call inside the caller's transaction. `before`: the
    stored rows as they were read in this transaction (_segment_rows); with the same number of lines
    only the lines that changed are written (an edit of one line writes one row)."""
    rows = _segment_rows(segments)
    if before is not None and len(before) == len(segments):
        old = before
        conn.executemany("UPDATE segments SET data = ? WHERE meeting_id = ? AND pos = ?",
                         [(r, meeting_id, i) for i, (r, o) in enumerate(zip(rows, old)) if r != o])
    else:
        conn.execute("DELETE FROM segments WHERE meeting_id = ?", (meeting_id,))
        conn.executemany("INSERT INTO segments (meeting_id, pos, data) VALUES (?, ?, ?)",
                         [(meeting_id, i, r) for i, r in enumerate(rows)])
    # The JSON of an older version (read as a fallback until moved) is replaced by these rows: left
    # behind, it would overwrite them at the next start
    conn.execute("UPDATE meetings SET has_transcript = ?, segments_json = '' WHERE id = ?", (1 if segments else 0, meeting_id))


def _move_segments_to_table(conn: sqlite3.Connection) -> int:
    """Transcripts still kept in meetings.segments_json go to the segments table, and the column is
    emptied; returns how many recordings were moved. At every start: a recording an older version
    transcribed (after going back to it) is moved too, its newer transcript replacing the rows of
    the earlier one. The search index was built from this same text (or is built from the table
    right after, see init_db), so it is left alone."""
    pending = "COALESCE(segments_json, '') NOT IN ('', '[]')"
    ids = [r["id"] for r in conn.execute(f"SELECT id FROM meetings WHERE {pending}").fetchall()]
    if not ids:
        return 0
    conn.execute(f"DELETE FROM segments WHERE meeting_id IN (SELECT id FROM meetings WHERE {pending})")
    try:
        # In SQL: a library of a million lines moves in seconds, not minutes
        conn.execute(f"""INSERT INTO segments (meeting_id, pos, data)
            SELECT m.id, j.key, j.value FROM meetings AS m, json_each(m.segments_json) AS j
            WHERE m.{pending} AND json_valid(m.segments_json) AND j.type = 'object'""")
    except sqlite3.OperationalError:  # SQLite without JSON functions
        for row in conn.execute(f"SELECT id, segments_json FROM meetings WHERE {pending}").fetchall():
            _write_segments(conn, row["id"], [x for x in _loads_list(row["segments_json"]) if isinstance(x, dict)])
    conn.execute(f"UPDATE meetings SET has_transcript = EXISTS (SELECT 1 FROM segments WHERE meeting_id = meetings.id), "
                 f"segments_json = '' WHERE {pending}")
    print(f"[data] transcripts of {len(ids)} recording(s) moved to the segments table", flush=True)
    return len(ids)


SYSTEM_FOLDERS = {"recordings": "Записи", "imports": "Импорт", "dictations": "Диктовки"}
_VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".m4v", ".webm", ".wmv", ".flv", ".mpg", ".mpeg", ".ts", ".mts", ".3gp"}


def _ensure_system_folders(cur: sqlite3.Cursor) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for key, name in SYSTEM_FOLDERS.items():
        if not cur.execute("SELECT 1 FROM folders WHERE system_key = ?", (key,)).fetchone():
            cur.execute("INSERT INTO folders (name, parent_id, system_key, created_at) VALUES (?, NULL, ?, ?)", (name, key, now))
            if key == "dictations":  # they can hold anything said (a password too): never sent to a cloud model
                try:
                    cur.execute("UPDATE folders SET local_only = 1 WHERE system_key = 'dictations'")
                except sqlite3.OperationalError:
                    pass  # a library from before local_only (the column comes with its migration)


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


def _reindex_segments(conn: sqlite3.Connection, meeting_id: int, segments: List[Dict[str, Any]],
                      before: Optional[List[str]] = None) -> None:
    """Replaces the search index rows of one recording. Call inside the caller's transaction.
    `before` (the stored rows before an edit, see _write_segments): with the same lines, each with
    its own id, only the lines that changed are indexed again."""
    # The meaning index of this recording is out of date: app/semantic.py indexes it again a while
    # after the last edit, embedding only the chunks whose text changed
    conn.execute("UPDATE semantic_meta SET stale = 1, updated_at = datetime('now', 'localtime') WHERE meeting_id = ?",
                 (meeting_id,))

    def seg_id(seg: Dict[str, Any]) -> int:
        return int(seg.get("id", 0) or 0)

    lines = segments
    if before is not None and len(before) == len(segments):
        old = [json.loads(r) for r in before]
        ids, old_ids = [seg_id(x) for x in segments], [seg_id(x) for x in old]
        if len(set(ids)) == len(ids) and len(set(old_ids)) == len(old_ids):
            rows = _segment_rows(segments)
            changed = [i for i, (r, o) in enumerate(zip(rows, before)) if r != o]
            conn.executemany("DELETE FROM segments_fts WHERE meeting_id = ? AND segment_id = ?",
                             [(meeting_id, old_ids[i]) for i in changed])
            lines = [segments[i] for i in changed]
        else:
            before = None
    if before is None or len(before) != len(segments):
        conn.execute("DELETE FROM segments_fts WHERE meeting_id = ?", (meeting_id,))
    conn.executemany(
        "INSERT INTO segments_fts (text, meeting_id, segment_id, start, speaker) VALUES (?, ?, ?, ?, ?)",
        [
            (
                seg.get("text") or "",
                meeting_id,
                seg_id(seg),
                float(seg.get("start", 0.0) or 0.0),
                seg.get("speaker") or "SPEAKER_00",
            )
            for seg in lines
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
            key = {"recording": "recordings", "dictation": "dictations"}.get(source, "imports")
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
        m = _deserialize_meeting(dict(row), conn)
        conn.close()
    events.publish({"type": "list"})
    return m


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
    transcript_model: Optional[str] = None,
) -> None:
    """reindex=False: a partial transcript shown while recognizing (search is indexed at the end).
    transcript_model: the model the new segments were made with (see stash_transcript_for)."""
    fields = []
    values = []
    if transcript_model is not None:
        fields.append("transcript_model = ?, transcript_at = ?")
        values.extend([transcript_model, datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
    if speakers is not None:
        fields.append("speakers_json = ?")
        values.append(json.dumps(speakers, ensure_ascii=False))
    if summaries is not None:
        fields.append("summaries_json = ?")
        values.append(json.dumps(summaries, ensure_ascii=False))
    if language is not None:
        fields.append("language = ?")
        values.append(language)

    if not fields and segments is None:
        return

    values.append(meeting_id)
    with _db_lock:
        conn = get_connection()
        if fields:
            conn.execute(f"UPDATE meetings SET {', '.join(fields)} WHERE id = ?", tuple(values))
        if segments is not None:
            _write_segments(conn, meeting_id, segments)
            if reindex:
                _reindex_segments(conn, meeting_id, segments)
        conn.commit()
        conn.close()


_VERSION_COLUMNS = "transcript_model, transcript_at, speakers_json, segments_backup_json, translations_json, language, transcript_verified"


def _stash_active(conn: sqlite3.Connection, meeting_id: int) -> Optional[str]:
    """Copies the transcript shown (with what belongs to it) into transcript_versions under its model;
    returns the model, None when there is no transcript or no model to file it under."""
    row = conn.execute(f"SELECT {_VERSION_COLUMNS} FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
    segments = _read_segments(conn, meeting_id)
    if not row or not row["transcript_model"] or not segments:
        return None
    data = {"segments": segments, "speakers": _loads_dict(row["speakers_json"]),
            "segments_backup": _loads_list(row["segments_backup_json"]), "translations": _loads_dict(row["translations_json"]),
            "language": row["language"] or "", "verified": bool(row["transcript_verified"]), "at": row["transcript_at"] or ""}
    conn.execute("INSERT OR REPLACE INTO transcript_versions (meeting_id, model, data) VALUES (?, ?, ?)",
                 (meeting_id, row["transcript_model"], json.dumps(data, ensure_ascii=False)))
    return row["transcript_model"]


def stash_transcript_for(meeting_id: int, model: str) -> None:
    """A job is about to save the transcript `model` made. The one shown, made with another model,
    is kept among the versions; what belongs only to it (translations, the verified flag) does not
    pass to the new text. An older version of this same model is replaced by the new one."""
    with _db_lock:
        conn = get_connection()
        try:
            row = conn.execute("SELECT transcript_model FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
            if row and row["transcript_model"] and row["transcript_model"] != model and _stash_active(conn, meeting_id):
                conn.execute("UPDATE meetings SET translations_json = '{}', transcript_verified = 0 WHERE id = ?", (meeting_id,))
            conn.execute("DELETE FROM transcript_versions WHERE meeting_id = ? AND model = ?", (meeting_id, model))
            conn.commit()
        finally:
            conn.close()


def transcript_versions(conn: sqlite3.Connection, meeting_id: int) -> List[Dict[str, Any]]:
    """The models a recording has a stored transcript of (besides the one shown), with its date."""
    out = []
    for r in conn.execute("SELECT model, data FROM transcript_versions WHERE meeting_id = ? ORDER BY model", (meeting_id,)):
        try:
            data = json.loads(r["data"])
        except ValueError:
            continue
        out.append({"model": r["model"], "at": data.get("at") or "", "lines": len(data.get("segments") or [])})
    return out


def all_transcripts(meeting_id: int) -> Dict[str, Dict[str, Any]]:
    """Every transcript of a recording by model: the one shown and the stored versions (for merging)."""
    with _db_lock:
        conn = get_connection()
        try:
            out = {}
            for r in conn.execute("SELECT model, data FROM transcript_versions WHERE meeting_id = ?", (meeting_id,)):
                try:
                    out[r["model"]] = json.loads(r["data"])
                except ValueError:
                    pass
            row = conn.execute(f"SELECT {_VERSION_COLUMNS} FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
            segments = _read_segments(conn, meeting_id)
            if row and row["transcript_model"] and segments:
                out[row["transcript_model"]] = {"segments": segments, "speakers": _loads_dict(row["speakers_json"]),
                                                "language": row["language"] or ""}
            return out
        finally:
            conn.close()


def switch_transcript_version(meeting_id: int, model: str) -> bool:
    """Shows the transcript `model` made: the one shown goes to the versions, this one takes its
    place (search index included). False when there is no such version."""
    with _db_lock:
        conn = get_connection()
        try:
            row = conn.execute("SELECT transcript_model FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
            if not row:
                return False
            if row["transcript_model"] == model:
                return True
            got = conn.execute("SELECT data FROM transcript_versions WHERE meeting_id = ? AND model = ?",
                               (meeting_id, model)).fetchone()
            if not got:
                return False
            _stash_active(conn, meeting_id)
            _show_version(conn, meeting_id, model, json.loads(got["data"]))
            conn.commit()
            return True
        finally:
            conn.close()


def _show_version(conn: sqlite3.Connection, meeting_id: int, model: str, data: Dict[str, Any]) -> None:
    """A stored version takes the place of the transcript shown (which the caller has filed or dropped)."""
    segments = [s for s in data.get("segments") or [] if isinstance(s, dict)]
    backup = data.get("segments_backup") or []
    # A model's name stays the one the next Transcribe uses; the merged text is not made by one
    next_model = ", model_name = ?" if model != "merged" else ""
    conn.execute(
        "UPDATE meetings SET transcript_model = ?, transcript_at = ?, speakers_json = ?, segments_backup_json = ?, "
        f"translations_json = ?, language = ?, transcript_verified = ?{next_model} WHERE id = ?",
        (model, data.get("at") or "", json.dumps(data.get("speakers") or {}, ensure_ascii=False),
         json.dumps(backup, ensure_ascii=False) if backup else "", json.dumps(data.get("translations") or {}, ensure_ascii=False),
         data.get("language") or "", 1 if data.get("verified") else 0, *([model] if next_model else []), meeting_id))
    _write_segments(conn, meeting_id, segments)
    _reindex_segments(conn, meeting_id, segments)
    conn.execute("DELETE FROM transcript_versions WHERE meeting_id = ? AND model = ?", (meeting_id, model))


def delete_transcript_version(meeting_id: int, model: str) -> bool:
    """Deletes the transcript one model made (the button next to the model dropdown). When it is the
    one shown, the best of the others takes its place (the merged one, then by the models'
    accuracy); with none left the recording has no transcript (its analysis stays). False when the
    recording has no transcript of this model."""
    from app.transcript_merge import MERGED, ranked

    with _db_lock:
        conn = get_connection()
        try:
            row = conn.execute("SELECT transcript_model, language FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
            if not row:
                return False
            if row["transcript_model"] != model:
                gone = conn.execute("DELETE FROM transcript_versions WHERE meeting_id = ? AND model = ?", (meeting_id, model)).rowcount
                conn.commit()
                return bool(gone)
            others = {r["model"]: r["data"] for r in conn.execute(
                "SELECT model, data FROM transcript_versions WHERE meeting_id = ?", (meeting_id,))}
            order = ([MERGED] if MERGED in others else []) + ranked([m for m in others if m != MERGED], row["language"] or "")
            if order:
                _show_version(conn, meeting_id, order[0], json.loads(others[order[0]]))
            else:
                conn.execute("UPDATE meetings SET transcript_model = '', transcript_at = '', speakers_json = '{}', "
                             "segments_backup_json = '', translations_json = '{}', transcript_verified = 0, "
                             "status = 'cleared', progress = 0, stage_text = '', error_message = '' WHERE id = ?", (meeting_id,))
                _write_segments(conn, meeting_id, [])
                _reindex_segments(conn, meeting_id, [])
            conn.commit()
            return True
        finally:
            conn.close()


def edit_meeting(meeting_id: int, mutate: Callable[[Dict[str, Any]], Iterable[str]]) -> Optional[Dict[str, Any]]:
    """
    Changes the transcript and/or the participants of a recording in one step under the lock, so two
    edits at once (two segments, a rename while "apply voice profiles" runs) never undo each other.
    mutate(meeting) changes meeting["segments"] / ["speakers"] in place (and may set
    meeting["segments_backup"]) and returns what it changed: "segments", "speakers", "segments_backup".
    An exception leaves the recording as it was.
    Returns the recording after the change, or None when there is none.
    """
    with _db_lock:
        conn = get_connection()
        try:
            row = conn.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
            if not row:
                return None
            m = _deserialize_meeting(dict(row), conn)
            # As text: mutate changes the lines (and their words) in place. Only rows that are stored
            # can be updated one by one (a transcript read from older JSON is written whole)
            stored = conn.execute("SELECT 1 FROM segments WHERE meeting_id = ? LIMIT 1", (meeting_id,)).fetchone()
            before = _segment_rows(m["segments"]) if stored else None
            changed = set(mutate(m) or ())
            if "segments" in changed:
                _write_segments(conn, meeting_id, m["segments"], before)
                _reindex_segments(conn, meeting_id, m["segments"], before)
            if "speakers" in changed:
                conn.execute("UPDATE meetings SET speakers_json = ? WHERE id = ?",
                             (json.dumps(m["speakers"], ensure_ascii=False), meeting_id))
            if "segments_backup" in changed:
                backup = m.get("segments_backup") or []
                conn.execute("UPDATE meetings SET segments_backup_json = ? WHERE id = ?",
                             (json.dumps(backup, ensure_ascii=False) if backup else "", meeting_id))
            if not changed:
                return m
            conn.commit()
            row = conn.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
            return _deserialize_meeting(dict(row), conn)
        finally:
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
        row = conn.execute("SELECT segments_backup_json FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
        current = _read_segments(conn, meeting_id)
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

    before, after = words(_loads_list(row["segments_backup_json"])), words(current)
    gone = [d for k, d in before.items() if k not in after]
    if not before:  # no word timings: count words of the text
        n_before = sum(len((s.get("text") or "").split()) for s in _loads_list(row["segments_backup_json"]))
        n_after = sum(len((s.get("text") or "").split()) for s in current)
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
        restored = _loads_list(row["segments_backup_json"])
        _write_segments(conn, meeting_id, restored)
        conn.execute("UPDATE meetings SET segments_backup_json = '' WHERE id = ?", (meeting_id,))
        _reindex_segments(conn, meeting_id, restored)
        conn.commit()
        conn.close()
    return True


def update_meeting_fields(meeting_id: int, **fields: Any) -> None:
    allowed = {"title", "model_name", "language", "num_speakers_hint", "folder_id", "title_is_default", "obsidian_path", "obsidian_hash", "user_notes",
               "transcript_verified", "audio_path",
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


def delete_meeting_dub(meeting_id: int, lang: str) -> bool:
    """Removes one dub of a recording and its files (only the ones the app made, in the data folder)."""
    with _db_lock:
        conn = get_connection()
        row = conn.execute("SELECT dubs_json FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
        if row is None:
            conn.close()
            return False
        try:
            dubs = json.loads(row["dubs_json"] or "[]")
        except Exception:
            dubs = []
        gone = [_dub_paths_from_storage(d) for d in dubs if d.get("lang") == lang]
        conn.execute("UPDATE meetings SET dubs_json = ? WHERE id = ?",
                     (json.dumps([d for d in dubs if d.get("lang") != lang], ensure_ascii=False), meeting_id))
        conn.commit()
        conn.close()
    for old in gone:
        for key in _DUB_PATH_KEYS:
            p = Path(old.get(key) or "")
            if p.name and p.exists() and DATA_DIR in p.resolve().parents:
                delete_file(str(p))
    events.publish({"type": "list"})
    return bool(gone)


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


def meeting_status(meeting_id: int) -> str:
    """The status alone (no transcript is read); "" when there is no such recording."""
    with _db_lock:
        conn = get_connection()
        row = conn.execute("SELECT status FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
        conn.close()
    return str(row["status"] or "") if row else ""


def speaker_seconds(meeting_ids: Iterable[int]) -> Dict[int, Dict[str, float]]:
    """How long each speaker talks in each recording: {meeting_id: {speaker_id: seconds}}, read from
    the lines without loading whole transcripts."""
    ids = [int(i) for i in meeting_ids]
    out: Dict[int, Dict[str, float]] = {i: {} for i in ids}
    if not ids:
        return out
    with _db_lock:
        conn = get_connection()
        try:
            for i in range(0, len(ids), 500):
                part = ids[i:i + 500]
                q = (f"SELECT meeting_id, json_extract(data, '$.speaker') AS spk, "
                     f"SUM(MAX(0.0, COALESCE(json_extract(data, '$.end'), 0) - COALESCE(json_extract(data, '$.start'), 0))) AS sec "
                     f"FROM segments WHERE meeting_id IN ({','.join('?' * len(part))}) GROUP BY meeting_id, spk")
                for r in conn.execute(q, part):
                    out[r["meeting_id"]][r["spk"]] = float(r["sec"] or 0.0)
        except sqlite3.OperationalError:  # SQLite without JSON functions
            for mid in ids:
                for seg in _read_segments(conn, mid):
                    spk = seg.get("speaker")
                    out[mid][spk] = out[mid].get(spk, 0.0) + max(0.0, float(seg.get("end") or 0) - float(seg.get("start") or 0))
        finally:
            conn.close()
    return out


def get_meeting(meeting_id: int) -> Optional[Dict[str, Any]]:
    with _db_lock:
        conn = get_connection()
        row = conn.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
        m = _deserialize_meeting(dict(row), conn) if row else None
        conn.close()
        return m


_LIST_COLUMNS = """id, title, filename, media_path, duration, status, progress, stage_text,
    error_message, language, model_name, num_speakers_hint,
    speakers_json, created_at, completed_at, folder_id, source, recorded_at, has_video, local_only, media_size, series_id,
    transcript_verified, transcript_model,
    obsidian_path, exports_json,
    has_transcript, has_summary"""

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


def delete_file(path: str) -> bool:
    """Deletes a file of the app. Windows refuses while a file is open (the player streams it): such a
    file is remembered and deleted at the next start (retry_pending_deletes), never left forever."""
    p = Path(path)
    try:
        p.unlink(missing_ok=True)
        return True
    except OSError as e:
        print(f"[delete] {path}: {e} (deleted at the next start)", flush=True)
    with _db_lock:
        conn = get_connection()
        conn.execute("CREATE TABLE IF NOT EXISTS pending_deletes (path TEXT PRIMARY KEY, since TEXT NOT NULL)")
        conn.execute("INSERT OR IGNORE INTO pending_deletes (path, since) VALUES (?, datetime('now'))", (str(p),))
        conn.commit()
        conn.close()
    return False


def retry_pending_deletes() -> int:
    """Files that could not be deleted before (see delete_file); returns how many are gone now."""
    with _db_lock:
        conn = get_connection()
        conn.execute("CREATE TABLE IF NOT EXISTS pending_deletes (path TEXT PRIMARY KEY, since TEXT NOT NULL)")
        paths = [r["path"] for r in conn.execute("SELECT path FROM pending_deletes").fetchall()]
        conn.close()
    gone = []
    for path in paths:
        try:
            Path(path).unlink(missing_ok=True)
            gone.append(path)
        except OSError:
            pass
    if gone:
        with _db_lock:
            conn = get_connection()
            conn.executemany("DELETE FROM pending_deletes WHERE path = ?", [(g,) for g in gone])
            conn.commit()
            conn.close()
    return len(gone)


def remove_media(meeting_id: int) -> bool:
    """Deletes the sound of a recording and keeps its transcript and analysis (privacy settings, or
    by hand). Only files the app made or copied in; a file used in place is the user's and stays."""
    m = get_meeting(meeting_id)
    if not m or m.get("media_removed"):
        return False
    paths = [m.get("audio_path")]
    if m.get("owns_media"):
        paths.append(m.get("media_path"))
    for p_str in paths:
        if p_str:
            delete_file(p_str)
    with _db_lock:
        conn = get_connection()
        if m.get("owns_media"):
            conn.execute("UPDATE meetings SET media_path = '', audio_path = '', media_removed = 1, media_size = 0 WHERE id = ?", (meeting_id,))
        else:
            conn.execute("UPDATE meetings SET audio_path = '' WHERE id = ?", (meeting_id,))
        conn.commit()
        conn.close()
    (DATA_DIR / "thumbs" / f"{meeting_id}.jpg").unlink(missing_ok=True)
    events.publish({"type": "list"})
    return bool(m.get("owns_media"))


def expired_media_ids(days: int) -> List[int]:
    """Completed recordings older than `days` that still keep their sound (retention setting)."""
    if days <= 0:
        return []
    from datetime import datetime, timedelta

    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    with _db_lock:
        conn = get_connection()
        rows = conn.execute(
            "SELECT id FROM meetings WHERE status = 'completed' AND owns_media = 1 AND COALESCE(media_removed, 0) = 0 "
            "AND media_path != '' AND COALESCE(NULLIF(recorded_at, ''), created_at) < ?", (cutoff,)).fetchall()
        conn.close()
    return [int(r["id"]) for r in rows]


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


# Work folders of the data folder whose files belong to recordings (exports and recordings are the
# user's files and are never scanned)
ORPHAN_DIRS = ("uploads", "audio", "dubs", "thumbs")
ORPHAN_MIN_AGE_SEC = 24 * 3600  # younger files may belong to work in progress (an upload, a dub)
_ID_NAME = re.compile(r"^(?:meeting_|work_meeting_)?(\d+)(?=[._]|$)")


def orphan_files() -> List[Path]:
    """Files in the work folders that no recording refers to: left by a crash, a failed delete or an
    older version. A file named after a recording that exists (thumbs/12.jpg, audio/meeting_12.wav)
    or changed within a day is kept; system_*.wav belongs to the start-up recovery of recordings."""
    import time

    with _db_lock:
        conn = get_connection()
        rows = conn.execute("SELECT id, media_path, audio_path, dubs_json FROM meetings").fetchall()
        conn.close()
    ids = {int(r["id"]) for r in rows}
    used = set()
    for r in rows:
        paths = [r["media_path"], r["audio_path"]]
        try:
            for d in json.loads(r["dubs_json"] or "[]"):
                d = _dub_paths_from_storage(d)
                paths += [d.get("path"), d.get("srt_path")]
        except (ValueError, TypeError):
            pass
        for p in paths:
            if p:
                try:
                    used.add(os.path.normcase(str(Path(p).resolve())))
                except OSError:
                    pass
    now = time.time()
    out: List[Path] = []
    for folder in ORPHAN_DIRS:
        base = DATA_DIR / folder
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            try:
                if not p.is_file() or p.is_symlink() or now - p.stat().st_mtime < ORPHAN_MIN_AGE_SEC:
                    continue
            except OSError:
                continue
            if folder == "uploads" and p.name.startswith("system_"):
                continue
            if os.path.normcase(str(p.resolve())) in used:
                continue
            # A file (or a dub's work folder) named after a recording that still exists
            m = _ID_NAME.match(p.relative_to(base).parts[0])
            if m and int(m.group(1)) in ids:
                continue
            out.append(p)
    return out


def delete_orphan_files() -> Dict[str, int]:
    """Deletes what orphan_files() finds; {"files": n, "bytes": freed}."""
    files, freed = 0, 0
    for p in orphan_files():
        try:
            size = p.stat().st_size
        except OSError:
            continue
        if delete_file(str(p)):
            files += 1
            freed += size
    for folder in ORPHAN_DIRS:  # the work folders of dubs emptied above, deepest first
        base = DATA_DIR / folder
        if not base.is_dir():
            continue
        for d in sorted((x for x in base.rglob("*") if x.is_dir()), key=lambda x: len(x.parts), reverse=True):
            try:
                d.rmdir()  # only an empty folder goes
            except OSError:
                pass
    return {"files": files, "bytes": freed}


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
                delete_file(p_str)
    thumb = DATA_DIR / "thumbs" / f"{meeting_id}.jpg"
    thumb.unlink(missing_ok=True)
    (AUDIO_DIR / f"meeting_{meeting_id}.peaks.json").unlink(missing_ok=True)  # the player's waveform

    with _db_lock:
        conn = get_connection()
        conn.execute("DELETE FROM meetings WHERE id = ?", (meeting_id,))
        conn.execute("DELETE FROM segments WHERE meeting_id = ?", (meeting_id,))
        conn.execute("DELETE FROM transcript_versions WHERE meeting_id = ?", (meeting_id,))
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


def clear_results(meeting_id: int) -> bool:
    """Everything the processing made (transcript, speakers, summaries, translations, promises,
    search index) is deleted; the sound, the title and the user's notes stay. Status "cleared": the
    recording waits for Retry. Dubs (made from the old transcript) go with their files, and exports
    waiting for the end of processing are dropped: they would run on the next Retry unasked."""
    m = get_meeting(meeting_id)
    if not m:
        return False
    for d in m.get("dubs") or []:
        for key in _DUB_PATH_KEYS:
            p = Path(d.get(key) or "")
            if p.name and p.exists() and DATA_DIR in p.resolve().parents:
                delete_file(str(p))
    with _db_lock:
        conn = get_connection()
        conn.execute(
            "UPDATE meetings SET segments_json = '', has_transcript = 0, speakers_json = '{}', summaries_json = '{}', translations_json = '{}', "
            "segments_backup_json = '', dubs_json = '[]', pending_exports_json = '[]', status = 'cleared', progress = 0, "
            "stage_text = '', error_message = '', transcript_verified = 0, transcript_model = '', transcript_at = '' WHERE id = ?",
            (meeting_id,))
        conn.execute("DELETE FROM segments WHERE meeting_id = ?", (meeting_id,))
        conn.execute("DELETE FROM transcript_versions WHERE meeting_id = ?", (meeting_id,))
        conn.execute("DELETE FROM segments_fts WHERE meeting_id = ?", (meeting_id,))
        conn.execute("DELETE FROM semantic_chunks WHERE meeting_id = ?", (meeting_id,))
        conn.execute("DELETE FROM semantic_meta WHERE meeting_id = ?", (meeting_id,))
        try:
            conn.execute("DELETE FROM commitments WHERE meeting_id = ?", (meeting_id,))
        except sqlite3.OperationalError:
            pass
        conn.commit()
        conn.close()
    events.publish({"type": "list"})
    return True


# What is done to a recording when it is transcribed, chosen per recording above the text (the
# "Recognition" row); what a recording has not chosen comes from the settings
PROCESSING_KEYS = ("denoise", "loudnorm", "leveling", "vad", "diarization", "clean_fillers")


def effective_processing(stored: Optional[Dict[str, Any]], settings: Optional[Dict[str, str]] = None) -> Dict[str, bool]:
    s = settings if settings is not None else get_all_settings()
    on = lambda key: (s.get(key) or "true").lower() == "true"  # noqa: E731
    out = {
        "denoise": on("audio_enhance"),  # FFmpeg: hiss and hum, clicks, the voice band
        "loudnorm": on("audio_enhance"),  # FFmpeg: loudness levelled over time
        "leveling": True,  # quiet parts raised before GigaAM / Parakeet (asr_onnx.level)
        "vad": on("vad_filter"),  # Whisper skips the silence
        "diarization": on("diarization_enabled"),
        "clean_fillers": on("clean_fillers"),
    }
    out.update({k: bool(v) for k, v in (stored or {}).items() if k in PROCESSING_KEYS})
    return out


def set_meeting_processing(meeting_id: int, processing: Dict[str, Any]) -> None:
    clean = {k: bool(v) for k, v in (processing or {}).items() if k in PROCESSING_KEYS}
    with _db_lock:
        conn = get_connection()
        conn.execute("UPDATE meetings SET processing_json = ? WHERE id = ?", (json.dumps(clean), meeting_id))
        conn.commit()
        conn.close()


def _deserialize_meeting(d: Dict[str, Any], conn: sqlite3.Connection) -> Dict[str, Any]:
    d["has_segments_backup"] = bool(d.pop("segments_backup_json", ""))
    d["pending_exports"] = _loads_list(d.pop("pending_exports_json", None))
    d["exports"] = [p for p in _loads_list(d.pop("exports_json", None)) if isinstance(p, str)]
    d["is_video"] = is_video(d)
    d["local_only"] = bool(d.get("local_only"))
    legacy = _loads_list(d.pop("segments_json", None))  # not moved yet (see _move_segments_to_table)
    d["segments"] = _read_segments(conn, d["id"]) or legacy
    for key, target in (
        ("speakers_json", "speakers"),
        ("summaries_json", "summaries"),
        ("dubs_json", "dubs"),
    ):
        raw = d.pop(key, None)
        default_val = [] if target == "dubs" else {}
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
    d["transcript_versions"] = transcript_versions(conn, d["id"])
    d["processing"] = effective_processing(_loads_dict(d.pop("processing_json", None)))
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
    def merge(m: Dict[str, Any]) -> tuple:
        segments = m.get("segments") or []
        target_idx = next((i for i, seg in enumerate(segments) if int(seg.get("id", -1)) == int(segment_id)), -1)
        if target_idx <= 0:
            return ()
        prev_seg = segments[target_idx - 1]
        curr_seg = segments[target_idx]
        prev_seg["end"] = round(float(curr_seg.get("end", prev_seg.get("end", 0.0))), 2)
        prev_seg["text"] = (
            (prev_seg.get("text") or "").rstrip() + " " + (curr_seg.get("text") or "").lstrip()
        ).strip()
        prev_seg["words"] = list(prev_seg.get("words") or []) + list(curr_seg.get("words") or [])
        segments.pop(target_idx)
        for idx, s in enumerate(segments):
            s["id"] = idx + 1
        m["segments"] = segments
        return ("segments",)

    return edit_meeting(meeting_id, merge)



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
