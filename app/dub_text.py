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


# ---------------------------------------------------------------- timing helpers for the mix
# Typical speaking rate in characters per second; CJK scripts pack more meaning per character
_CHARS_PER_SEC = {"zh": 6.0, "ja": 7.0, "ko": 7.0}
_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")

CONTINUOUS_GAP = 0.8   # same speaker and a pause shorter than this: one continuous monologue
MAX_LEAD = 1.5         # a phrase may start at most this much earlier than in the original
PHRASE_GAP = 0.15      # pause kept between pulled-together phrases
TEMPO_MIN = 0.92       # gentle slow-down for phrases that came out much shorter than the original


def expected_speech_sec(text: str, lang: str) -> float:
    """Rough duration of a phrase read at a normal pace, used to spot TTS output that ran on."""
    return max(0.6, len((text or "").strip()) / _CHARS_PER_SEC.get(lang, 14.0))


def has_foreign_script(text: str, lang: str) -> bool:
    """True when a translation into a non-CJK language contains CJK characters (LLM leakage)."""
    return lang not in _CHARS_PER_SEC and bool(_CJK.search(text or ""))


def plan_placement(
    units: List[Dict[str, Any]],
    total_dur: float,
    tempo_max: float,
) -> List[Dict[str, float]]:
    """
    Decides where each synthesized phrase starts and how much it is sped up or slowed down.
    `units` need start, end, speaker and dur (seconds of synthesized speech). Phrases of one
    continuous monologue are pulled together (at most MAX_LEAD earlier than the original), so a
    translation that is shorter than the original does not leave a long silence after each phrase.
    Returns one {start, room, tempo} per unit, in order.
    """
    plan: List[Dict[str, float]] = []
    prev_end = -1e9
    prev_speaker = None
    prev_orig_end = -1e9
    for i, u in enumerate(units):
        orig_start, orig_end = float(u["start"]), float(u["end"])
        nxt = float(units[i + 1]["start"]) if i + 1 < len(units) else float(total_dur)
        start = orig_start
        if u.get("speaker") == prev_speaker and orig_start - prev_orig_end < CONTINUOUS_GAP:
            start = min(orig_start, max(prev_end + PHRASE_GAP, orig_start - MAX_LEAD))
        start = max(start, prev_end + 0.05, 0.0)
        room = max(0.3, nxt - 0.05 - start)
        dur = float(u["dur"])
        orig_len = max(0.3, orig_end - orig_start)
        if dur > room:
            tempo = min(tempo_max, dur / room)
        elif dur < 0.7 * orig_len:
            tempo = max(TEMPO_MIN, dur / orig_len)
        else:
            tempo = 1.0
        plan.append({"start": start, "room": room, "tempo": tempo})
        prev_end = start + min(room, dur / tempo)
        prev_speaker = u.get("speaker")
        prev_orig_end = orig_end
    return plan


# ---------------------------------------------------------------- parallel synthesis
TTS_WORKER_VRAM_GB = 4.5  # one Chatterbox process peaks at ~3.7 GB; the rest is headroom
TTS_MAX_WORKERS = 3       # measured: 3 processes give ~1.6x, more mostly add CPU contention


def choose_tts_workers(setting: str, free_vram_gb: float, cpu_count: int) -> int:
    """
    Number of parallel Chatterbox processes. Generation is a token-by-token loop that leaves the
    GPU mostly idle, so a few processes side by side raise throughput while VRAM allows.
    `setting` is "auto" or an explicit number from the settings.
    """
    value = (setting or "auto").strip().lower()
    if value.isdigit():
        return max(1, min(TTS_MAX_WORKERS, int(value)))
    by_vram = int(max(0.0, free_vram_gb - 1.0) // TTS_WORKER_VRAM_GB)
    by_cpu = max(1, (cpu_count or 1) // 4)
    return max(1, min(TTS_MAX_WORKERS, by_vram, by_cpu))
