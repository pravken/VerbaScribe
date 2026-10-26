"""
Pure-text helpers for transcripts: filler-word cleanup, word re-alignment after manual edits,
and Whisper hallucination checks. No heavy dependencies, so they are easy to unit-test.
"""

import difflib
import re
from typing import Any, Dict, List, Optional, Tuple

# Known Whisper hallucination phrases on Russian/English silence
HALLUCINATION_SUBSTRINGS = (
    "субтитры делал",
    "субтитры сделал",
    "редактор субтитров",
    "корректор а.егорова",
    "а.синецкая",
    "dimatorzok",
    "продолжение следует",
    "подпишись на канал",
    "ставьте лайки",
    "amara.org",
)

# Single-token vocal hesitations
FILLER_TOKENS = {
    "э", "ээ", "эээ", "э-э", "э-э-э", "эм", "эмм", "мм", "ммм", "м-м",
    "а-а", "ааа", "хм", "хмм", "uh", "uhh", "um", "umm", "erm", "er", "hmm", "hm",
}
_FILLER_RE = re.compile(r"(э+(-э+)*|э+м+|м{2,}|м+(-м+)+|а+-а+|а{3,}|хм+|uh+|um+|hm+|er+m*)")

# Multi-word filler phrases (normalized tokens). Kept deliberately short: phrases such as
# "как бы" or "вот это самое" often carry meaning and are not removed.
FILLER_PHRASES: Tuple[Tuple[str, ...], ...] = (("ну", "типа"),)

# Short words that are commonly repeated on purpose ("да да", "нет нет", "very very")
KEEP_REPEATS = {
    "да", "нет", "не", "ну", "так", "вот", "ага", "угу", "ой", "ну-ну",
    "yes", "no", "ok", "so", "bye", "hey",
}

# Text-only fallback (segments without word timestamps)
_TEXT_FILLER_PATTERNS = (
    (re.compile(r"(?i)(?<!\w)(э+(?:[\-\s]+э+)*|э+м+|м{2,}|uh+|um+|er+m)(?!\w)[,.]?\s*"), ""),
    (re.compile(r"(?i)(?<!\w)ну\s+типа(?!\w)[,]?\s*"), ""),
)

_SENTENCE_END = (".", "?", "!", "…")


def normalize_token(word: str) -> str:
    return re.sub(r"[^\w\-]", "", str(word).lower()).strip("-")


def is_filler_token(norm: str) -> bool:
    return bool(norm) and (norm in FILLER_TOKENS or bool(_FILLER_RE.fullmatch(norm)))


def is_hallucination(text: str) -> bool:
    t = text.strip().lower()
    if not t:
        return True
    return any(bad in t and len(t) < 65 for bad in HALLUCINATION_SUBSTRINGS)


def is_low_quality_segment(
    no_speech_prob: Optional[float],
    avg_logprob: Optional[float],
    compression_ratio: Optional[float],
) -> bool:
    """
    Mirrors Whisper's own heuristics: a segment that is most likely silence and was decoded
    with low confidence, or one stuck in a repetition loop, is dropped.
    """
    if no_speech_prob is not None and avg_logprob is not None:
        if no_speech_prob > 0.6 and avg_logprob < -1.0:
            return True
    if compression_ratio is not None and compression_ratio > 2.6:
        return True
    return False


def _clean_words(words: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    kept: List[Dict[str, Any]] = []
    removed = 0
    norms = [normalize_token(w.get("word", "")) for w in words]
    i = 0
    while i < len(words):
        phrase_len = 0
        for phrase in FILLER_PHRASES:
            if tuple(norms[i : i + len(phrase)]) == phrase:
                phrase_len = len(phrase)
                break

        n = norms[i]
        drop_count = phrase_len
        if not drop_count and is_filler_token(n):
            drop_count = 1
        if (
            not drop_count
            and kept
            and n
            and len(n) <= 3
            and n not in KEEP_REPEATS
            and n == normalize_token(kept[-1].get("word", ""))
        ):
            drop_count = 1  # stutter: "я я", "в в", "the the"

        if drop_count:
            # Keep sentence-ending punctuation that was attached to a removed word
            last_raw = str(words[i + drop_count - 1].get("word", "")).rstrip()
            if kept and last_raw.endswith(_SENTENCE_END):
                prev = dict(kept[-1])
                prev_raw = str(prev.get("word", "")).rstrip()
                if not prev_raw.endswith(_SENTENCE_END + (",",)):
                    prev["word"] = prev_raw + last_raw[-1]
                    kept[-1] = prev
            removed += 1
            i += drop_count
            continue

        kept.append(words[i])
        i += 1
    return kept, removed


def _capitalize_like(new_text: str, orig_text: str) -> str:
    if new_text and orig_text and orig_text[0].isupper() and new_text[0].islower():
        return new_text[0].upper() + new_text[1:]
    return new_text


def filler_view(segments: List[Dict[str, Any]]) -> Dict[str, Any]:
    """What «Без запинок» hides when the text is shown, without changing it: per segment id the
    indices of its words to hide (segments with word timings) or the text to show (without them).
    `count` is the number of fillers, `seconds` the time their words take."""
    out: Dict[str, Dict[str, Any]] = {}
    count, seconds = 0, 0.0
    for seg in segments:
        words = list(seg.get("words") or [])
        orig_text = str(seg.get("text") or "").strip()
        if words:
            tagged = [dict(w, _vi=i) for i, w in enumerate(words)]
            kept, removed = _clean_words(tagged)
            if not removed:
                continue
            keep = {w["_vi"] for w in kept}
            drop = [i for i in range(len(words)) if i not in keep]
            count += removed
            seconds += sum(max(0.0, float(words[i].get("end") or 0) - float(words[i].get("start") or 0)) for i in drop)
            text = "".join(str(w.get("word", "")) for w in kept).strip()
            out[str(seg.get("id"))] = {"drop": drop, "text": _capitalize_like(text, orig_text)}
        else:
            new_text, n = orig_text, 0
            for pattern, repl in _TEXT_FILLER_PATTERNS:
                new_text, k = pattern.subn(repl, new_text)
                n += k
            if not n:
                continue
            count += n
            new_text = _capitalize_like(re.sub(r"\s{2,}", " ", new_text).strip(" ,;-"), orig_text)
            out[str(seg.get("id"))] = {"drop": [], "text": new_text}
    return {"segments": out, "count": count, "seconds": round(seconds, 1)}


def clean_filler_words_in_segments(
    segments: List[Dict[str, Any]], renumber: bool = True,
) -> Tuple[List[Dict[str, Any]], int]:
    """
    Removes vocal hesitations, a few unambiguous filler phrases and stuttered short words.
    When word timestamps exist, the cleanup runs on words and the text is rebuilt from them,
    so the displayed words and the exported text always stay identical.
    renumber: the lines get ids 1, 2, ... again (a transcript being made); False keeps the ids, which
    the translations of a stored transcript are keyed by.
    Returns (cleaned_segments, removed_count).
    """
    removed_total = 0
    cleaned: List[Dict[str, Any]] = []

    for seg in segments:
        seg_copy = dict(seg)
        orig_text = str(seg_copy.get("text") or "").strip()
        words = list(seg_copy.get("words") or [])

        if words:
            kept, removed = _clean_words(words)
            removed_total += removed
            if not kept:
                continue
            if removed:
                first = dict(kept[0])
                first["word"] = " " + _capitalize_like(str(first.get("word", "")).lstrip(), orig_text)
                kept[0] = first
                seg_copy["text"] = "".join(str(w.get("word", "")) for w in kept).strip()
            seg_copy["words"] = kept
        else:
            new_text = orig_text
            for pattern, repl in _TEXT_FILLER_PATTERNS:
                new_text, n_sub = pattern.subn(repl, new_text)
                removed_total += n_sub
            new_text = re.sub(r"\s{2,}", " ", new_text).strip(" ,;-")
            new_text = _capitalize_like(new_text, orig_text)
            if not new_text:
                continue
            seg_copy["text"] = new_text

        cleaned.append(seg_copy)

    if renumber:
        for idx, s in enumerate(cleaned):
            s["id"] = idx + 1
    return cleaned, removed_total


def realign_words(
    old_words: List[Dict[str, Any]],
    new_text: str,
    seg_start: float,
    seg_end: float,
) -> List[Dict[str, Any]]:
    """
    Builds word timestamps for manually edited segment text. Unchanged words keep their exact
    timings, replaced words share the time span of the words they replaced, and inserted words
    are spread evenly between their neighbours. Edited words get prob=1.0 (human-verified).
    """
    tokens = re.findall(r"\S+", new_text or "")
    if not tokens:
        return []

    old_norm = [normalize_token(w.get("word", "")) for w in old_words]
    new_norm = [normalize_token(t) for t in tokens]
    timings: List[Optional[Tuple[float, float, float]]] = [None] * len(tokens)

    matcher = difflib.SequenceMatcher(a=old_norm, b=new_norm, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                ow = old_words[i1 + k]
                timings[j1 + k] = (float(ow["start"]), float(ow["end"]), float(ow.get("prob", 1.0)))
        elif tag == "replace":
            span_start = float(old_words[i1]["start"])
            span_end = float(old_words[i2 - 1]["end"])
            n = j2 - j1
            for k in range(n):
                timings[j1 + k] = (
                    span_start + (span_end - span_start) * k / n,
                    span_start + (span_end - span_start) * (k + 1) / n,
                    1.0,
                )

    # Interpolate runs of inserted words between known neighbours
    idx = 0
    while idx < len(timings):
        if timings[idx] is not None:
            idx += 1
            continue
        run_start = idx
        while idx < len(timings) and timings[idx] is None:
            idx += 1
        left = timings[run_start - 1][1] if run_start > 0 else float(seg_start)
        right = timings[idx][0] if idx < len(timings) else float(seg_end)
        if right < left:
            right = left
        n = idx - run_start
        for k in range(n):
            timings[run_start + k] = (
                left + (right - left) * k / n,
                left + (right - left) * (k + 1) / n,
                1.0,
            )

    return [
        {"start": round(t[0], 2), "end": round(t[1], 2), "word": " " + tok, "prob": round(t[2], 2)}
        for tok, t in zip(tokens, timings)
    ]
