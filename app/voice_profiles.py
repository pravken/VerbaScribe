"""
Voice profiles: once a speaker is given a name, the same voice is named automatically in new
recordings.

Diarization keeps a voice print per speaker (the duration-weighted mean of its 192-dim ECAPA
embeddings, see diarizer). Renaming a speaker stores that print under the name; a later recording
compares its speakers with the stored prints by cosine similarity. Everything stays in the local
database; the feature can be switched off and profiles deleted in Settings.
"""

import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np

from app.database import _db_lock, get_all_settings, get_connection

# Same-speaker ECAPA embeddings of meeting audio typically score 0.6-0.85, different people
# below 0.5. The bar is set high: a wrong name is worse than no name.
MATCH_THRESHOLD = 0.72
# How much one recording may move a stored print (a running mean that keeps adapting)
MAX_SAMPLES_WEIGHT = 20

_DEFAULT_NAME_RE = re.compile(r"^(Участник|Speaker|SPEAKER)[ _]?\d+$", re.I)


def enabled() -> bool:
    return get_all_settings().get("voice_profiles", "true").lower() == "true"


def is_default_name(name: str) -> bool:
    return not name.strip() or bool(_DEFAULT_NAME_RE.match(name.strip()))


def init_table() -> None:
    with _db_lock:
        conn = get_connection()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS voice_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                embedding TEXT NOT NULL,
                samples INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
        conn.close()


def _unit(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return (v / n).astype(np.float32) if n > 1e-8 else v.astype(np.float32)


def _load() -> List[Dict[str, Any]]:
    init_table()
    conn = get_connection()
    rows = conn.execute("SELECT id, name, embedding, samples, updated_at FROM voice_profiles").fetchall()
    conn.close()
    out = []
    for r in rows:
        try:
            emb = np.asarray(json.loads(r[2]), dtype=np.float32)
        except (ValueError, TypeError):
            continue
        out.append({"id": r[0], "name": r[1], "embedding": emb, "samples": r[3], "updated_at": r[4]})
    return out


def list_profiles() -> List[Dict[str, Any]]:
    return [{k: p[k] for k in ("id", "name", "samples", "updated_at")} for p in _load()]


def delete_profile(profile_id: int) -> bool:
    init_table()
    with _db_lock:
        conn = get_connection()
        cur = conn.execute("DELETE FROM voice_profiles WHERE id = ?", (profile_id,))
        conn.commit()
        conn.close()
    return cur.rowcount > 0


def learn(name: str, embedding: Optional[List[float]]) -> None:
    """Stores or refines the voice print of `name` from one speaker of one recording."""
    name = name.strip()[:120]
    if not embedding or is_default_name(name) or not enabled():
        return
    new = _unit(np.asarray(embedding, dtype=np.float32))
    existing = {p["name"].lower(): p for p in _load()}
    now = datetime.now().isoformat(timespec="seconds")
    with _db_lock:
        conn = get_connection()
        p = existing.get(name.lower())
        if p is None:
            conn.execute(
                "INSERT INTO voice_profiles (name, embedding, samples, updated_at) VALUES (?, ?, 1, ?)",
                (name, json.dumps([round(float(x), 5) for x in new]), now),
            )
        else:
            w = min(int(p["samples"]), MAX_SAMPLES_WEIGHT)
            merged = _unit(p["embedding"] * w + new)
            conn.execute(
                "UPDATE voice_profiles SET embedding = ?, samples = samples + 1, updated_at = ? WHERE id = ?",
                (json.dumps([round(float(x), 5) for x in merged]), now, p["id"]),
            )
        conn.commit()
        conn.close()


def apply_to_speakers(speakers: Dict[str, Dict[str, Any]], only_unnamed: bool = False) -> int:
    """
    Gives known names to the speakers of a recording. Each profile names at most one speaker
    (the most similar one) and only above MATCH_THRESHOLD. only_unnamed: speakers the user named
    keep their names, and a name already used in the recording is not given again (for recordings
    made before the profile existed). Returns how many were named.
    """
    if not speakers or not enabled():
        return 0
    profiles = _load()
    if only_unnamed:
        taken = {str(m.get("name") or "").strip().lower() for m in speakers.values() if not is_default_name(str(m.get("name") or ""))}
        profiles = [p for p in profiles if p["name"].lower() not in taken]
        speakers = {k: v for k, v in speakers.items() if is_default_name(str(v.get("name") or ""))}
    if not profiles or not speakers:
        return 0
    candidates = []
    for spk_id, meta in speakers.items():
        emb = meta.get("embedding")
        if not emb:
            continue
        v = _unit(np.asarray(emb, dtype=np.float32))
        for p in profiles:
            candidates.append((float(np.dot(v, p["embedding"])), spk_id, p))
    named = 0
    used_speakers, used_profiles = set(), set()
    for score, spk_id, p in sorted(candidates, key=lambda c: -c[0]):
        if score < MATCH_THRESHOLD:
            break
        if spk_id in used_speakers or p["id"] in used_profiles:
            continue
        speakers[spk_id]["name"] = p["name"]
        speakers[spk_id]["voice_profile"] = {"id": p["id"], "score": round(score, 3)}
        used_speakers.add(spk_id)
        used_profiles.add(p["id"])
        named += 1
    return named


def apply_to_library() -> Dict[str, int]:
    """Looks for the known voices in recordings made before their profiles existed; only unnamed
    speakers get a name. Returns how many recordings and speakers were named."""
    from app.database import save_meeting_results

    if not enabled() or not _load():
        return {"meetings": 0, "speakers": 0}
    with _db_lock:
        conn = get_connection()
        rows = conn.execute("SELECT id, speakers_json FROM meetings WHERE status = 'completed'").fetchall()
        conn.close()
    meetings = speakers_named = 0
    for r in rows:
        try:
            speakers = json.loads(r["speakers_json"] or "{}")
        except ValueError:
            continue
        if not any(m.get("embedding") for m in speakers.values() if isinstance(m, dict)):
            continue
        named = apply_to_speakers(speakers, only_unnamed=True)
        if named:
            save_meeting_results(int(r["id"]), speakers=speakers)
            meetings += 1
            speakers_named += named
    return {"meetings": meetings, "speakers": speakers_named}


def people() -> List[Dict[str, Any]]:
    """Everyone named in the library, with the recordings they speak in and for how long, most
    frequent first: [{name, profile, total_seconds, recordings: [{meeting_id, title, date, seconds}]}]."""
    profile_names = {p["name"].lower() for p in _load()}
    with _db_lock:
        conn = get_connection()
        rows = conn.execute(
            "SELECT id, title, recorded_at, created_at, speakers_json, segments_json FROM meetings WHERE status = 'completed'"
        ).fetchall()
        conn.close()
    by_name: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        try:
            speakers = json.loads(r["speakers_json"] or "{}")
            segments = json.loads(r["segments_json"] or "[]")
        except ValueError:
            continue
        seconds: Dict[str, float] = {}
        for seg in segments:
            spk = seg.get("speaker")
            seconds[spk] = seconds.get(spk, 0.0) + max(0.0, float(seg.get("end") or 0) - float(seg.get("start") or 0))
        for spk_id, meta in speakers.items():
            name = str((meta or {}).get("name") or "").strip()
            if is_default_name(name) or not seconds.get(spk_id):
                continue
            entry = by_name.setdefault(name.lower(), {"name": name, "profile": name.lower() in profile_names,
                                                      "total_seconds": 0.0, "recordings": []})
            entry["total_seconds"] += seconds[spk_id]
            entry["recordings"].append({"meeting_id": int(r["id"]), "title": r["title"],
                                        "date": str(r["recorded_at"] or r["created_at"] or "")[:10],
                                        "seconds": round(seconds[spk_id], 1)})
    out = sorted(by_name.values(), key=lambda e: (-len(e["recordings"]), -e["total_seconds"]))
    for e in out:
        e["total_seconds"] = round(e["total_seconds"], 1)
        e["recordings"].sort(key=lambda x: x["date"], reverse=True)
    return out
