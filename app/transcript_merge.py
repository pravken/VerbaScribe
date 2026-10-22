"""
One transcript from the transcripts several models made of a recording (each model's one is kept,
app/database.py transcript_versions; the dropdown above the text switches between them).

The way known as ROVER: the words of every version are lined up with those of a base version (the
most accurate model for the language, see bench-report/), phrase by phrase by time, and each place
is decided by the versions. Where at least two models agree and outweigh the rest, their word is
taken. A place nobody wins (two models that differ, or all different) goes to the analysis model,
which only picks one of the variants it is shown and never writes words of its own; without one,
the more accurate model wins. Those places keep their variants (word["alts"]): the transcript
underlines them and a click picks another one (choose_alternative).

The result is a version of its own (MERGED), made from the versions as they are now, edits included.
The speakers and the phrases are those of the base version.
"""

import bisect
import difflib
import functools
import json
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.text_utils import normalize_token

MERGED = "merged"

# Most accurate first (WER on FLEURS, bench-report/report.md); the base and the tie-break
_RANK = {
    "ru": ["gigaam-v3", "large-v3", "parakeet-v3", "large-v3-turbo"],
    "": ["parakeet-v3", "large-v3", "large-v3-turbo", "gigaam-v3"],
}
_LLM_BATCH = 20
_CONTEXT_WORDS = 8


def ranked(models: List[str], lang: str) -> List[str]:
    order = _RANK.get((lang or "")[:2], _RANK[""])
    return sorted(models, key=lambda m: order.index(m) if m in order else len(order))


def _weight(model: str, lang: str) -> float:
    order = _RANK.get((lang or "")[:2], _RANK[""])
    return 1.0 - 0.1 * order.index(model) if model in order else 0.5


class _Tok:
    __slots__ = ("norm", "surface", "start", "end", "model")

    def __init__(self, norm: str, surface: str, start: float, end: float, model: str):
        self.norm, self.surface, self.start, self.end, self.model = norm, surface, start, end, model

    @property
    def mid(self) -> float:
        return (self.start + self.end) / 2


def _tokens(seg: Dict[str, Any], model: str) -> List[_Tok]:
    """The words of a phrase with their times. Edited phrases keep their words in step with the text
    (realign_words); a phrase whose words do not match its text gets the text, spread over its time."""
    start, end = float(seg.get("start") or 0.0), float(seg.get("end") or 0.0)
    text_words = (seg.get("text") or "").split()
    words = [w for w in seg.get("words") or [] if str(w.get("word") or "").strip()]
    if words and [normalize_token(w["word"]) for w in words] == [normalize_token(x) for x in text_words]:
        return [_Tok(normalize_token(w["word"]), str(w["word"]).strip(), float(w.get("start") or start),
                     float(w.get("end") or w.get("start") or start), model) for w in words if normalize_token(w["word"])]
    out, n = [], max(1, len(text_words))
    for i, x in enumerate(text_words):
        if normalize_token(x):
            a = start + (end - start) * i / n
            out.append(_Tok(normalize_token(x), x, a, a + (end - start) / n, model))
    return out


@functools.lru_cache(maxsize=1 << 16)
def _likeness(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def _align(base: List[_Tok], other: List[_Tok]) -> Tuple[List[Optional[_Tok]], List[List[_Tok]]]:
    """Edit-distance alignment of two word lists. Returns the word of `other` standing for each base
    word (None: missing there) and the words `other` has in each gap before base word i (index
    len(base): after the last one)."""
    n, m = len(base), len(other)

    def sub(a: _Tok, b: _Tok) -> float:
        if a.norm == b.norm:
            return 0.0
        cost = 1.0 - 0.5 * _likeness(a.norm, b.norm)  # "пилюли" / "пилюлю" pair up
        return cost + (0.5 if abs(a.mid - b.mid) > 2.0 else 0.0)

    d = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        d[i][0] = float(i)
    for j in range(1, m + 1):
        d[0][j] = float(j)
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            d[i][j] = min(d[i - 1][j - 1] + sub(base[i - 1], other[j - 1]), d[i - 1][j] + 1.0, d[i][j - 1] + 1.0)
    paired: List[Optional[_Tok]] = [None] * n
    gaps: List[List[_Tok]] = [[] for _ in range(n + 1)]
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and d[i][j] == d[i - 1][j - 1] + sub(base[i - 1], other[j - 1]):
            paired[i - 1] = other[j - 1]
            i, j = i - 1, j - 1
        elif i > 0 and d[i][j] == d[i - 1][j] + 1.0:
            i -= 1
        else:
            gaps[i].insert(0, other[j - 1])
            j -= 1
    return paired, gaps


def _by_phrase(base_segments: List[Dict[str, Any]], toks: List[_Tok]) -> List[List[_Tok]]:
    """The words of another version handed to the base phrase they were said in (or the nearest)."""
    starts = [float(s.get("start") or 0.0) for s in base_segments]
    out: List[List[_Tok]] = [[] for _ in base_segments]
    for t in toks:
        i = max(0, bisect.bisect_right(starts, t.mid) - 1)
        if i + 1 < len(base_segments) and t.mid > float(base_segments[i].get("end") or 0.0):
            # between two phrases: the closer one
            if starts[i + 1] - t.mid < t.mid - float(base_segments[i].get("end") or 0.0):
                i += 1
        out[i].append(t)
    return out


class _Place:
    """One place of the merged text: a base word (or a gap between words) and what each version says."""
    __slots__ = ("seg", "gap", "options", "choice", "disputed", "by_llm")

    def __init__(self, seg: int, gap: bool):
        self.seg, self.gap = seg, gap
        self.options: Dict[str, Dict[str, Any]] = {}  # norm text -> {"toks": [...], "models": [...], "weight"}
        self.choice = ""
        self.disputed = False
        self.by_llm = False

    def vote(self, toks: List[_Tok], model: str, weight: float) -> None:
        key = " ".join(t.norm for t in toks)
        opt = self.options.setdefault(key, {"toks": toks, "models": [], "weight": 0.0})
        opt["models"].append(model)
        opt["weight"] += weight

    def text(self, key: str) -> str:
        return " ".join(t.surface for t in self.options[key]["toks"])


def _decide(place: _Place, total: float, rank: List[str]) -> None:
    """Clear: one variant, or one that more models said than any other (two at least) and that
    outweighs the rest. Else disputed (a tie, two models that differ)."""
    order = sorted(place.options, key=lambda k: (len(place.options[k]["models"]), place.options[k]["weight"]), reverse=True)
    best, opt = order[0], place.options[order[0]]
    second = len(place.options[order[1]]["models"]) if len(order) > 1 else 0
    if len(order) == 1 or (len(opt["models"]) >= 2 and len(opt["models"]) > second and opt["weight"] > total / 2):
        place.choice = best
        return
    place.disputed = True
    # until the analysis model says otherwise: the variant of the most accurate model
    place.choice = next(k for m in rank for k, o in place.options.items() if m in o["models"])


def _context(places: List[_Place], i: int) -> Tuple[str, str]:
    def words(p: _Place) -> List[str]:
        return p.text(p.choice).split() if p.choice else []

    left: List[str] = []
    for p in reversed(places[:i]):
        left = words(p) + left
        if len(left) >= _CONTEXT_WORDS:
            break
    right: List[str] = []
    for p in places[i + 1:]:
        right += words(p)
        if len(right) >= _CONTEXT_WORDS:
            break
    return " ".join(left[-_CONTEXT_WORDS:]), " ".join(right[:_CONTEXT_WORDS])


_SYSTEM = (
    "Ты сверяешь расшифровки одной записи, сделанные разными моделями распознавания речи. В каждом "
    "спорном месте модели услышали разное. Выбери вариант, который лучше подходит по смыслу и грамматике "
    "в окружающем тексте. Выбирай только из предложенных вариантов, ничего не придумывай и не исправляй. "
    'Ответ — только JSON: {"choices": [номер варианта для места 1, для места 2, ...]}.'
)


def _ask_llm(places: List[_Place], idx: List[int], chat: Callable[[str, str], str]) -> int:
    """The analysis model picks a variant for each disputed place in `idx`; returns how many it decided."""
    lines, keys = [], []
    for n, i in enumerate(idx, 1):
        p = places[i]
        left, right = _context(places, i)
        opts = list(p.options)
        keys.append(opts)
        variants = "  ".join(f"{k}) «{p.text(o)}»" if o else f"{k}) (ничего)" for k, o in enumerate(opts, 1))
        lines.append(f"Место {n}: …{left} [?] {right}…\n  Варианты: {variants}")
    answer = chat(_SYSTEM, "\n\n".join(lines))
    m = re.search(r"\{.*\}", answer or "", re.S)
    try:
        choices = json.loads(m.group(0)).get("choices") if m else None
    except ValueError:
        choices = None
    if not isinstance(choices, list):
        return 0
    decided = 0
    for i, opts, c in zip(idx, keys, choices):
        if isinstance(c, int) and 1 <= c <= len(opts):
            places[i].choice, places[i].by_llm = opts[c - 1], True
            decided += 1
    return decided


def merge(versions: Dict[str, Dict[str, Any]], lang: str, chat: Optional[Callable[[str, str], str]] = None,
          progress: Optional[Callable[[int, str], None]] = None,
          check_cancel: Optional[Callable[[], None]] = None) -> Dict[str, Any]:
    """versions: model -> {"segments", "speakers", ...} (two or more). Returns {"segments", "speakers",
    "base", "models", "disputed", "by_llm"}."""
    models = ranked([m for m in versions if m != MERGED and versions[m].get("segments")], lang)
    if len(models) < 2:
        raise ValueError("Для сведения нужны расшифровки хотя бы двух моделей")
    base_model, others = models[0], models[1:]
    base_segments = [s for s in versions[base_model]["segments"] if isinstance(s, dict)]
    weights = {m: _weight(m, lang) for m in models}
    total = sum(weights.values())

    other_by_phrase = {}
    for m in others:
        toks = [t for s in versions[m]["segments"] if isinstance(s, dict) for t in _tokens(s, m)]
        toks.sort(key=lambda t: t.start)
        other_by_phrase[m] = _by_phrase(base_segments, toks)

    places: List[_Place] = []
    for si, seg in enumerate(base_segments):
        if check_cancel and si % 50 == 0:
            check_cancel()
        base = _tokens(seg, base_model)
        aligned = {m: _align(base, other_by_phrase[m][si]) for m in others}
        for i in range(len(base) + 1):
            gap = _Place(si, gap=True)
            gap.vote([], base_model, weights[base_model])
            for m in others:
                gap.vote(aligned[m][1][i], m, weights[m])
            if len(gap.options) > 1:
                _decide(gap, total, models)
                places.append(gap)
            if i == len(base):
                break
            word = _Place(si, gap=False)
            word.vote([base[i]], base_model, weights[base_model])
            for m in others:
                t = aligned[m][0][i]
                word.vote([t] if t else [], m, weights[m])
            _decide(word, total, models)
            places.append(word)

    disputed = [i for i, p in enumerate(places) if p.disputed]
    by_llm = 0
    if chat and disputed:
        for b in range(0, len(disputed), _LLM_BATCH):
            if check_cancel:
                check_cancel()
            if progress:
                progress(10 + int(85 * b / len(disputed)), f"Сведение версий: спорные места {b + 1}–{min(len(disputed), b + _LLM_BATCH)} из {len(disputed)}")
            try:
                by_llm += _ask_llm(places, disputed[b:b + _LLM_BATCH], chat)
            except Exception as e:  # the model stopped answering: the rest is decided by accuracy
                print(f"[merge] analysis model failed: {e}", flush=True)
                break

    by_seg: Dict[int, List[_Place]] = {}
    for p in places:
        by_seg.setdefault(p.seg, []).append(p)
    out_segments = []
    for si, seg in enumerate(base_segments):
        words: List[Dict[str, Any]] = []
        for p in by_seg.get(si, []):
            toks = p.options[p.choice]["toks"]
            for k, t in enumerate(toks):
                w = {"word": " " + t.surface, "start": round(t.start, 2), "end": round(t.end, 2), "prob": 1.0}
                if p.disputed and k == 0:
                    w["alts"] = [{"text": p.text(o), "models": opt["models"]} for o, opt in p.options.items()]
                    if len(toks) > 1:
                        w["span"] = len(toks)
                words.append(w)
        text = "".join(w["word"] for w in words).strip()
        if text:
            out_segments.append({**{k: v for k, v in seg.items() if k not in ("words", "text")}, "text": text, "words": words})
    return {"segments": out_segments, "speakers": versions[base_model].get("speakers") or {}, "base": base_model,
            "models": models, "disputed": len(disputed), "by_llm": by_llm}


def choose_alternative(seg: Dict[str, Any], index: int, text: str) -> None:
    """Puts variant `text` in place of disputed word `index` of a merged phrase (in place). The word
    (and the words of its span) are replaced; the place is no longer marked as disputed."""
    words = seg.get("words") or []
    if not (0 <= index < len(words)) or not words[index].get("alts"):
        raise ValueError("Это слово не спорное")
    w = words[index]
    if text not in [a.get("text") for a in w["alts"]]:
        raise ValueError("Такого варианта нет")
    span = int(w.get("span") or 1)
    start, end = float(w.get("start") or 0.0), float(words[min(len(words), index + span) - 1].get("end") or 0.0)
    parts = text.split()
    new = []
    for k, part in enumerate(parts):
        a = start + (end - start) * k / len(parts)
        new.append({"word": " " + part, "start": round(a, 2), "end": round(a + (end - start) / len(parts), 2), "prob": 1.0})
    words[index:index + span] = new
    seg["words"] = words
    seg["text"] = "".join(x["word"] for x in words).strip()
