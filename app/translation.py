"""
"Translate text": machine translation of a transcript, stored per language on the recording.

Storage (meetings.translations_json):
    {lang: {"model": ..., "source_lang": ..., "created_at": ..., "updated_at": ...,
            "items": {"<segment id>": {"text": translation, "src": fingerprint of the source text,
                                       "start": seconds}}}}

Segment ids are renumbered when segments are merged and texts change when they are edited, so a
stored translation is matched to the current transcript by id *and* source fingerprint, with the
segment start time as a fallback after renumbering. A translation whose source text has changed
since is kept and shown as "stale" until it is translated again; segments without any translation
are "missing". Timing always comes from the current segments, never from the stored items.

This module has no heavy dependencies: the Ollama client is imported only by the job runner.
"""

import hashlib
import re
from copy import deepcopy
from datetime import datetime
from typing import Any, Callable, Dict, Iterable, List, Optional

from app.audio_utils import srt_time as _format_srt_time

# code -> English name used in LLM prompts. Superset of the dub languages; the quality of rarer
# languages depends entirely on the chosen Ollama model.
TEXT_LANGUAGES: Dict[str, str] = {
    "ar": "Arabic", "bg": "Bulgarian", "cs": "Czech", "da": "Danish", "de": "German", "el": "Greek",
    "en": "English", "es": "Spanish", "et": "Estonian", "fa": "Persian", "fi": "Finnish", "fr": "French",
    "he": "Hebrew", "hi": "Hindi", "hu": "Hungarian", "hy": "Armenian", "id": "Indonesian", "it": "Italian",
    "ja": "Japanese", "ka": "Georgian", "kk": "Kazakh", "ko": "Korean", "lt": "Lithuanian", "lv": "Latvian",
    "ms": "Malay", "nl": "Dutch", "no": "Norwegian", "pl": "Polish", "pt": "Portuguese", "ro": "Romanian",
    "ru": "Russian", "sk": "Slovak", "sr": "Serbian (Cyrillic script)", "sr-latn": "Serbian (Latin script)", "sv": "Swedish", "sw": "Swahili", "th": "Thai",
    "tr": "Turkish", "uk": "Ukrainian", "uz": "Uzbek", "vi": "Vietnamese", "zh": "Chinese",
}

# MP4/MKV store track languages as ISO 639-2 codes; two-letter codes are silently dropped
ISO639_2: Dict[str, str] = {
    "ar": "ara", "bg": "bul", "cs": "ces", "da": "dan", "de": "deu", "el": "ell", "en": "eng", "es": "spa",
    "et": "est", "fa": "fas", "fi": "fin", "fr": "fra", "he": "heb", "hi": "hin", "hu": "hun", "hy": "hye",
    "id": "ind", "it": "ita", "ja": "jpn", "ka": "kat", "kk": "kaz", "ko": "kor", "lt": "lit", "lv": "lav",
    "ms": "msa", "nl": "nld", "no": "nor", "pl": "pol", "pt": "por", "ro": "ron", "ru": "rus", "sk": "slk",
    "sr": "srp", "sr-latn": "srp", "sv": "swe", "sw": "swa", "th": "tha", "tr": "tur", "uk": "ukr", "uz": "uzb", "vi": "vie",
    "zh": "zho",
}

_START_TOLERANCE = 0.05  # seconds


def source_fingerprint(text: str) -> str:
    """Short hash of a segment's source text, insensitive to whitespace differences."""
    norm = " ".join((text or "").split())
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:12]


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ------------------------------------------------------------------ storage <-> current transcript
def resolve_translation(segments: List[Dict[str, Any]], entry: Dict[str, Any]) -> Dict[str, Any]:
    """
    Matches a stored translation to the current segments. Returns
    {"segments": {"<id>": {"text", "stale"}}, "count", "translated", "stale", "missing", ...meta}.
    """
    items: Dict[str, Dict[str, Any]] = (entry or {}).get("items") or {}
    by_src: Dict[str, List[Dict[str, Any]]] = {}
    for it in items.values():
        by_src.setdefault(it.get("src") or "", []).append(it)

    out: Dict[str, Dict[str, Any]] = {}
    stale = missing = 0
    for seg in segments:
        sid = str(seg.get("id"))
        fp = source_fingerprint(seg.get("text") or "")
        start = float(seg.get("start") or 0.0)
        it = items.get(sid)
        if it and it.get("src") == fp:
            out[sid] = {"text": it.get("text") or "", "stale": False}
            continue
        # Same text elsewhere: the segment was renumbered (e.g. an earlier merge)
        moved = next(
            (c for c in by_src.get(fp, []) if abs(float(c.get("start") or 0.0) - start) <= _START_TOLERANCE),
            None,
        )
        if moved:
            out[sid] = {"text": moved.get("text") or "", "stale": False}
            continue
        # Translation of an older version of this segment (edited, or merged with the next one)
        old = next(
            (c for c in items.values() if abs(float(c.get("start") or 0.0) - start) <= _START_TOLERANCE),
            None,
        ) or (it if it and abs(float(it.get("start") or 0.0) - start) <= 1.0 else None)
        if old and (old.get("text") or "").strip():
            out[sid] = {"text": old.get("text") or "", "stale": True}
            stale += 1
        else:
            missing += 1
    return {
        "model": (entry or {}).get("model") or "",
        "source_lang": (entry or {}).get("source_lang") or "",
        "created_at": (entry or {}).get("created_at") or "",
        "updated_at": (entry or {}).get("updated_at") or "",
        "count": len(segments),
        "translated": len(out) - stale,
        "stale": stale,
        "missing": missing,
        "segments": out,
    }


def resolve_all(segments: List[Dict[str, Any]], raw: Dict[str, Any]) -> Dict[str, Any]:
    return {lang: resolve_translation(segments, entry) for lang, entry in (raw or {}).items() if isinstance(entry, dict)}


def segments_needing_translation(segments: List[Dict[str, Any]], entry: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Segments whose translation is stale or missing (for "update translation")."""
    resolved = resolve_translation(segments, entry or {})
    return [
        s for s in segments
        if (s.get("text") or "").strip()
        and (str(s.get("id")) not in resolved["segments"] or resolved["segments"][str(s.get("id"))]["stale"])
    ]


def merge_translated_items(
    entry: Optional[Dict[str, Any]],
    segments: List[Dict[str, Any]],
    translations: Dict[int, str],
    *,
    model: str,
    source_lang: str,
) -> Dict[str, Any]:
    """
    Returns a new stored entry with `translations` ({segment id: text}) written for the given
    `segments` snapshot. Items of segments that no longer exist are dropped.
    """
    entry = deepcopy(entry or {})
    items: Dict[str, Dict[str, Any]] = entry.get("items") or {}
    by_id = {int(s.get("id")): s for s in segments}
    # Re-key surviving items to the current ids, so renumbering after a merge does not orphan them.
    # A stale translation is kept (with a fingerprint that never matches) so the UI can still show it.
    resolved = resolve_translation(segments, {"items": items})
    fresh: Dict[str, Dict[str, Any]] = {}
    for sid, r in resolved["segments"].items():
        seg = by_id.get(int(sid))
        if seg is None:
            continue
        fresh[sid] = {
            "text": r["text"],
            "src": "stale" if r["stale"] else source_fingerprint(seg.get("text") or ""),
            "start": float(seg.get("start") or 0.0),
        }
    for seg_id, text in translations.items():
        seg = by_id.get(int(seg_id))
        if seg is None or not (text or "").strip():
            continue
        fresh[str(seg_id)] = {
            "text": text.strip(),
            "src": source_fingerprint(seg.get("text") or ""),
            "start": float(seg.get("start") or 0.0),
        }
    now = _now()
    entry.update(
        items=fresh,
        model=model,
        source_lang=source_lang,
        created_at=entry.get("created_at") or now,
        updated_at=now,
    )
    return entry


# ------------------------------------------------------------------ subtitles


def wrap_subtitle_line(text: str, width: int = 42) -> str:
    """Breaks a subtitle text into lines of at most ~`width` characters, balanced in length."""
    text = " ".join((text or "").split())
    if len(text) <= width:
        return text
    n = -(-len(text) // width)
    while True:
        lines = split_into_n(text, n)
        # Stop once every line fits, or the only lines too long are single long words
        if len(lines) < n or all(len(line) <= width or " " not in line for line in lines):
            return "\n".join(lines)
        n += 1


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…;:])\s+|(?<=,)\s+")


def split_text_for_cues(text: str, max_chars: int = 84) -> List[str]:
    """Splits long text at sentence ends (then commas, then spaces) into pieces of <= max_chars."""
    text = " ".join((text or "").split())
    if len(text) <= max_chars:
        return [text] if text else []
    parts = [p for p in _SENTENCE_SPLIT.split(text) if p]
    pieces: List[str] = []
    cur = ""
    for p in parts:
        if len(p) > max_chars:  # a single very long clause: break on spaces
            if cur:
                pieces.append(cur)
                cur = ""
            words, buf = p.split(" "), ""
            for w in words:
                if buf and len(buf) + 1 + len(w) > max_chars:
                    pieces.append(buf)
                    buf = w
                else:
                    buf = f"{buf} {w}" if buf else w
            cur = buf
            continue
        if cur and len(cur) + 1 + len(p) > max_chars:
            pieces.append(cur)
            cur = p
        else:
            cur = f"{cur} {p}" if cur else p
    if cur:
        pieces.append(cur)
    return pieces


def split_into_n(text: str, n: int) -> List[str]:
    """Splits text on spaces into at most n pieces of roughly equal length."""
    words = (text or "").split()
    if not words:
        return []
    n = max(1, min(n, len(words)))
    target = sum(len(w) + 1 for w in words) / n
    pieces: List[str] = []
    cur: List[str] = []
    size = 0
    for i, w in enumerate(words):
        cur.append(w)
        size += len(w) + 1
        left_words, left_pieces = len(words) - i - 1, n - len(pieces) - 1
        if left_pieces > 0 and (size >= target or left_words == left_pieces):
            pieces.append(" ".join(cur))
            cur, size = [], 0
    if cur:
        pieces.append(" ".join(cur))
    return pieces


def build_cues(
    segments: List[Dict[str, Any]],
    resolved: Optional[Dict[str, Any]],
    *,
    variant: str = "translated",
    speakers: Optional[Dict[str, Any]] = None,
    with_speaker: bool = False,
    split_long: bool = True,
    max_chars: int = 84,
) -> List[Dict[str, Any]]:
    """
    Subtitle cues [{start, end, text}] from the current segments.

    variant: "original", "translated" (falls back to the original where a segment has no
    translation) or "bilingual" (original line, translation line). Long translated segments are
    split into several cues with time shared in proportion to text length; the overall timing of
    every segment is kept exactly.
    """
    tr = (resolved or {}).get("segments") or {}
    speakers = speakers or {}
    cues: List[Dict[str, Any]] = []
    for seg in segments:
        start, end = float(seg.get("start") or 0.0), float(seg.get("end") or 0.0)
        if end <= start:
            end = start + 1.0
        original = " ".join((seg.get("text") or "").split())
        translated = " ".join(((tr.get(str(seg.get("id"))) or {}).get("text") or "").split())
        prefix = ""
        if with_speaker:
            spk = seg.get("speaker") or ""
            prefix = f"[{(speakers.get(spk) or {}).get('name') or spk}] " if spk else ""
        if variant == "bilingual":
            # Both texts are cut into the same number of length-balanced pieces that share the
            # segment's time evenly: an approximation, as the translation has no word timings.
            n = 1
            if split_long:
                per_lang = max_chars * 2 // 3
                n = max(1, -(-len(original) // per_lang), -(-len(translated) // per_lang))
            orig_parts, tr_parts = split_into_n(original, n), split_into_n(translated, n)
            n = max(len(orig_parts), len(tr_parts), 1)
            for k in range(n):
                o = orig_parts[k] if k < len(orig_parts) else ""
                t_ = tr_parts[k] if k < len(tr_parts) else ""
                text = "\n".join(x for x in (wrap_subtitle_line((prefix if k == 0 else "") + o), wrap_subtitle_line(t_)) if x)
                if text:
                    a = start + (end - start) * k / n
                    b = end if k == n - 1 else start + (end - start) * (k + 1) / n
                    cues.append({"start": round(a, 3), "end": round(b, 3), "text": text})
            continue
        main = original if variant == "original" else (translated or original)
        if not main:
            continue
        timed = _word_pieces(seg, max_chars) if (main is original and split_long) else None
        if timed:
            # The original has word timings: each piece shows exactly while its words are spoken
            for k, (a, b, p) in enumerate(timed):
                cues.append({"start": round(a, 3), "end": round(b, 3), "text": wrap_subtitle_line((prefix if k == 0 else "") + p)})
            continue
        pieces = split_text_for_cues(main, max_chars) if split_long else [main]
        total = sum(len(p) for p in pieces) or 1
        t = start
        for k, p in enumerate(pieces):
            t_end = end if k == len(pieces) - 1 else t + (end - start) * len(p) / total
            cues.append({"start": round(t, 3), "end": round(t_end, 3), "text": wrap_subtitle_line((prefix if k == 0 else "") + p)})
            t = t_end
    return cues


def _word_pieces(seg: Dict[str, Any], max_chars: int) -> Optional[List[tuple]]:
    """(start, end, text) pieces of a segment cut at sentence ends or at max_chars, timed by its
    words; None when the segment has no word timings or they no longer match its (edited) text."""
    words = [w for w in seg.get("words") or [] if str(w.get("word") or "").strip()]
    text = " ".join((seg.get("text") or "").split())
    if not words or "".join(str(w["word"]).strip() for w in words).casefold() != "".join(text.split()).casefold():
        return None
    pieces, cur, size = [], [], 0
    for w in words:
        word = str(w["word"]).strip()
        if cur and size + len(word) + 1 > max_chars:
            pieces.append(cur)
            cur, size = [], 0
        cur.append(w)
        size += len(word) + 1
        if word.endswith((".", "!", "?", "…")) and size >= max_chars // 3:
            pieces.append(cur)
            cur, size = [], 0
    if cur:
        pieces.append(cur)
    out = []
    for k, p in enumerate(pieces):
        a = float(p[0].get("start") or 0.0)
        # A piece stays on screen until the next one starts (no flashing gaps inside a phrase)
        b = float(pieces[k + 1][0].get("start") or 0.0) if k + 1 < len(pieces) else float(p[-1].get("end") or a)
        out.append((a, max(b, a + 0.3), " ".join(str(w["word"]).strip() for w in p)))
    return out


def _format_vtt_time(seconds: float) -> str:
    return _format_srt_time(seconds).replace(",", ".")


def cues_to_vtt(cues: Iterable[Dict[str, Any]]) -> str:
    """WebVTT for players and browsers (YouTube, VLC, <track>)."""
    blocks = ["WEBVTT"]
    for c in cues:
        blocks.append(f"{_format_vtt_time(c['start'])} --> {_format_vtt_time(c['end'])}\n{c['text']}")
    return "\n\n".join(blocks) + "\n"


def cues_to_srt(cues: Iterable[Dict[str, Any]]) -> str:
    blocks = []
    for i, c in enumerate(cues, 1):
        blocks.append(f"{i}\n{_format_srt_time(c['start'])} --> {_format_srt_time(c['end'])}\n{c['text']}")
    return "\n\n".join(blocks) + "\n"


# ------------------------------------------------------------------ exports
def meeting_for_export(meeting: Dict[str, Any], lang: str, variant: str) -> Dict[str, Any]:
    """
    A copy of the meeting prepared for the existing exporters: for "translated" the segment texts
    are replaced by the translation (original kept where a segment has none); for "bilingual" every
    segment gets a `secondary_text` with the translation.
    """
    resolved = (meeting.get("translations") or {}).get(lang)
    if not resolved:
        raise ValueError(f"Перевода на язык {lang!r} нет")
    out = dict(meeting)
    segs = []
    for seg in meeting.get("segments") or []:
        s = dict(seg)
        tr = (resolved["segments"].get(str(seg.get("id"))) or {}).get("text") or ""
        if variant == "bilingual":
            s["secondary_text"] = tr
        elif tr:
            s["text"] = tr
            s.pop("words", None)
        segs.append(s)
    out["segments"] = segs
    note = f"Машинный перевод ({lang.upper()}), модель {resolved.get('model') or '—'}"
    if resolved.get("stale") or resolved.get("missing"):
        note += f"; устарело: {resolved['stale']}, без перевода: {resolved['missing']}"
    out["translation_note"] = note
    out["translation_lang"] = lang
    # The exporters write their headings and this note in the language of the document
    out["translation_info"] = {
        "lang": lang, "model": resolved.get("model") or "—",
        "stale": resolved.get("stale") or 0, "missing": resolved.get("missing") or 0,
    }
    return out


# ------------------------------------------------------------------ queue job
def run_translate_job(
    meeting_id: int,
    target_lang: str,
    *,
    model: Optional[str],
    only_changed: bool,
    progress: Callable[[int, str], None],
    check_cancel: Callable[[], None],
) -> Dict[str, Any]:
    """Translates the recording's segments; partial results are saved after every batch."""
    from app.database import get_meeting, get_meeting_translations_raw, save_meeting_translation
    from app.llm_translate import resolve_ollama, translate_texts

    meeting = get_meeting(meeting_id)
    if not meeting:
        raise RuntimeError("Запись не найдена")
    if target_lang not in TEXT_LANGUAGES:
        raise ValueError(f"Язык {target_lang!r} не поддерживается")
    segments = [s for s in meeting.get("segments") or [] if (s.get("text") or "").strip()]
    if not segments:
        raise RuntimeError("В записи нет текста для перевода")
    src_lang = meeting.get("language") or "auto"
    entry = get_meeting_translations_raw(meeting_id).get(target_lang)
    todo = segments_needing_translation(segments, entry) if only_changed and entry else segments
    if not todo:
        progress(100, "Перевод уже актуален")
        return {"lang": target_lang, "translated": 0}

    progress(2, "Подключение к Ollama...")
    base_url, model_name = resolve_ollama(model)
    src_name = TEXT_LANGUAGES.get(src_lang, "the source language" if src_lang == "auto" else src_lang)
    tgt_name = TEXT_LANGUAGES[target_lang]
    # A full run overwrites segment by segment, so cancelling it keeps the rest of the old translation
    state = {"entry": entry}

    def _on_batch(done: int, got: Dict[int, str]) -> None:
        by_index = {int(todo[i]["id"]): t for i, t in got.items()}
        state["entry"] = merge_translated_items(
            state["entry"], segments, by_index, model=model_name, source_lang=src_lang
        )
        save_meeting_translation(meeting_id, target_lang, state["entry"])
        progress(3 + int(96 * done / len(todo)), f"Перевод ({target_lang.upper()}, {model_name}): {done} из {len(todo)} реплик")

    progress(3, f"Перевод ({target_lang.upper()}, {model_name}): 0 из {len(todo)} реплик")
    translate_texts(
        [s["text"] for s in todo], src_name, tgt_name,
        base_url=base_url, model=model_name, style="text", check_cancel=check_cancel,
        on_batch=_on_batch, batch_size=10, tgt_code=target_lang,
    )
    return {"lang": target_lang, "translated": len(todo), "model": model_name}
