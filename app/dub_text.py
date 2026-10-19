"""Splits diarized segments into short phrases for dubbing. Pure Python, so it is unit-testable in CI."""

import re
from typing import Any, Dict, List

UNIT_MAX_SEC = 12.0      # Chatterbox degrades on very long inputs; keep phrases short
UNIT_PAUSE_SPLIT = 0.6   # a pause this long always starts a new phrase

_SENTENCE_END = re.compile(r"[.!?…]['\"»)]*$")
_SOFT_END = re.compile(r"[,;:—–-]['\"»)]*$")


def build_dub_units(segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Splits diarized segments into short phrases (sentence ends, long pauses, max length)."""
    units: List[Dict[str, Any]] = []

    def _flush(words: List[Dict[str, Any]], speaker: str) -> None:
        text = "".join(w["word"] for w in words).strip()
        if text:
            units.append({"start": float(words[0]["start"]), "end": float(words[-1]["end"]), "speaker": speaker, "text": text})

    for seg in segments:
        speaker = seg.get("speaker") or "SPEAKER_00"
        words = [w for w in (seg.get("words") or []) if str(w.get("word", "")).strip()]
        if not words:
            if str(seg.get("text", "")).strip():
                units.append({"start": float(seg["start"]), "end": float(seg["end"]), "speaker": speaker, "text": seg["text"].strip()})
            continue
        cur: List[Dict[str, Any]] = []
        for i, w in enumerate(words):
            cur.append(w)
            dur = float(w["end"]) - float(cur[0]["start"])
            nxt = words[i + 1] if i + 1 < len(words) else None
            gap = (float(nxt["start"]) - float(w["end"])) if nxt else 0.0
            token = str(w["word"]).strip()
            if (
                nxt is None
                or gap >= UNIT_PAUSE_SPLIT
                or (_SENTENCE_END.search(token) and dur >= 1.2)
                or (_SOFT_END.search(token) and dur >= UNIT_MAX_SEC * 0.6)
                or dur >= UNIT_MAX_SEC
            ):
                _flush(cur, speaker)
                cur = []
    for i, u in enumerate(units):
        u["id"] = i
    return units
