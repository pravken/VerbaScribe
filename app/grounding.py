"""
Checks a summary against the transcript it was made from (no model involved).

Small local models sometimes write things nobody said. Every list item or sentence-line of a
summary is compared with the transcript: by the words it shares with the phrases around its time
stamp, or with the best matching place in the whole recording when it has none.

- A line with support but no time stamp gets the time of that place, so it links to the recording.
- A line with almost no support is marked UNVERIFIED_MARK; the page shows it as "not found in the
  recording".

Words are compared by their first letters (a crude stem that works for Russian endings), without
short and common words.
"""

import re
from typing import Any, Dict, List, Optional, Tuple

UNVERIFIED_MARK = "[?]"
GOOD = 0.34       # this share of the line's words found nearby: supported
WEAK = 0.17       # less than this anywhere in the recording: not supported
STEM = 5
WINDOW_SEC = 45.0  # around a time stamp the model gave

_TS_RE = re.compile(r"[\[(]?(\d{1,2}):([0-5]\d)(?::([0-5]\d))?[\])]?")
_WORD_RE = re.compile(r"[^\W\d_]{3,}", re.UNICODE)
_LINE_RE = re.compile(r"^(\s*(?:[-*+]|\d+[.)])\s+)(.*)$")

_STOP = {w[:STEM] for w in """
это этот эта эти того тому тем там тут как так что чтобы когда где кто или либо если все всё всех также
только еще ещё уже было были будет быть есть нет для при про под над без между через после перед потом
очень более менее самый который которая которые которых свой своя свои наш ваш его её их они она оно мы вы
говорит говорят сказал сказала отметил отметила упомянул упомянула обсуждали обсуждается речь идёт
участник участники спикер спикеры запись записи встреча встречи
the and for with that this these those from have has had was were are been will would about into over
speaker speakers said says mentioned discussed meeting recording also just very more less
""".split()}


def _seconds(m: "re.Match") -> float:
    a, b, c = m.group(1), m.group(2), m.group(3)
    return int(a) * 3600 + int(b) * 60 + int(c) if c is not None else int(a) * 60 + int(b)


def _fmt(sec: float) -> str:
    sec = int(sec)
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"[{h}:{m:02d}:{s:02d}]" if h else f"[{m:02d}:{s:02d}]"


def _stems(text: str) -> set:
    return {w[:STEM] for w in (x.casefold() for x in _WORD_RE.findall(text or "")) if w[:STEM] not in _STOP}


class _Index:
    def __init__(self, segments: List[Dict[str, Any]], speakers: Optional[Dict[str, Any]] = None):
        names = " ".join(str((v or {}).get("name") or "") for v in (speakers or {}).values())
        self.segs = [(float(s.get("start") or 0), _stems(s.get("text") or "")) for s in segments if (s.get("text") or "").strip()]
        self.names = _stems(names)

    def score(self, words: set, at: Optional[float] = None) -> Tuple[float, Optional[float]]:
        """(share of the words found in a phrase and its neighbours, the time of that phrase)."""
        best, best_t = (0.0, 0), None
        if not words:
            return 1.0, None
        for i, (t, own) in enumerate(self.segs):
            if at is not None and abs(t - at) > WINDOW_SEC:
                continue
            window = set().union(*(w for _, w in self.segs[max(0, i - 1): i + 2])) | self.names
            # The neighbours count for the support; the phrase itself decides where it points to
            key = (len(words & window) / len(words), len(words & own))
            if key > best:
                best, best_t = key, t
        return best[0], best_t


def alphabet(text: str) -> str:
    """"cyr", "lat", or "mixed" when neither has 80% of the letters."""
    cyr = len(re.findall(r"[\u0400-\u04ff]", text or ""))
    lat = len(re.findall(r"[A-Za-z]", text or ""))
    total = cyr + lat
    if not total:
        return "mixed"
    return "cyr" if cyr >= 0.8 * total else "lat" if lat >= 0.8 * total else "mixed"


def ground_summary(md: str, segments: List[Dict[str, Any]], speakers: Optional[Dict[str, Any]] = None) -> str:
    """The summary with time stamps added to supported lines and UNVERIFIED_MARK on unsupported ones.
    A summary in another alphabet than the recording (a Russian overview of an English call) is left
    as it is: its words cannot be found in the transcript, and every line would look made up."""
    if not md or not segments:
        return md
    said = alphabet(" ".join(str(s.get("text") or "") for s in segments[:400]))
    written = alphabet(_TS_RE.sub(" ", md))
    if "mixed" not in (said, written) and said != written:
        return md
    idx = _Index(segments, speakers)
    out = []
    in_code = False
    for line in md.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
        m = _LINE_RE.match(line)
        if in_code or not m or line.lstrip().startswith(("#", ">", "|")) or UNVERIFIED_MARK in line:
            out.append(line)
            continue
        body = m.group(2)
        words = _stems(_TS_RE.sub(" ", re.sub(r"\[[ xX]\]", " ", body)))
        if len(words) < 3:  # "- Да", a name: nothing to check
            out.append(line)
            continue
        stamps = [_seconds(x) for x in _TS_RE.finditer(body)]
        if stamps:
            near = max(idx.score(words, at)[0] for at in stamps)
            if near < WEAK and idx.score(words)[0] < WEAK:
                line = f"{line} {UNVERIFIED_MARK}"
        else:
            sc, at = idx.score(words)
            if sc >= GOOD and at is not None:
                line = f"{line} {_fmt(at)}"
            elif sc < WEAK:
                line = f"{line} {UNVERIFIED_MARK}"
        out.append(line)
    return "\n".join(out)


def maybe_ground(md: str, template_key: str, m: Dict[str, Any]) -> str:
    """ground_summary for the summaries it fits (not chapters: a strict format the page parses),
    when the setting is on."""
    from app.database import get_all_settings

    if template_key in ("chapters", "followup") or get_all_settings().get("summary_grounding", "true").lower() != "true":
        return md
    try:
        return ground_summary(md, m.get("segments") or [], m.get("speakers") or {})
    except Exception:
        return md


def readable(md: str, lang: str = "ru") -> str:
    """For files (DOCX, Markdown, Obsidian, clipboard): the mark as words."""
    return (md or "").replace(UNVERIFIED_MARK, "(not in the recording)" if lang == "en" else "(в записи не найдено)")


def readable_summaries(m: Dict[str, Any]) -> Dict[str, Any]:
    lang = "en" if (m.get("language") or "") == "en" else "ru"
    return {k: readable(v, lang) if isinstance(v, str) else v for k, v in (m.get("summaries") or {}).items()}
