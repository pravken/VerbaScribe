"""
Near-real-time translation of live captions (see app/live.py).

One worker thread per caption session talks to Ollama:
  * the model is loaded when the session starts and kept in memory (keep_alive), with the same
    small context every time, so Ollama never reloads it between lines;
  * every final line is a short request of its own (about 0.1-0.3 s on a warm model on a GPU);
  * the draft line (the phrase still being spoken) is translated too, at most every
    DRAFT_INTERVAL_SEC, so the translation follows speech before the phrase is final;
  * a line that failed (Ollama still starting, a timeout) is tried again later instead of being lost.
"""

import re
import threading
import time
import traceback
from typing import Any, Callable, Dict, List, Optional

import httpx

KEEP_ALIVE = "30m"
NUM_CTX = 2048            # short lines: a small context loads and runs faster
DRAFT_INTERVAL_SEC = 1.2  # how often the draft line is translated
MAX_ATTEMPTS = 4

_CJK_RUN = re.compile(r"[\u3000-\u303f\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af\uff00-\uffef]+")

_QUOTES = "\"'`«»“”„‟‘’‚‛‹›〝〞〟＂ "


def _first_line(text: str) -> str:
    """A caption translation is one line; anything after it is the model talking."""
    for line in (text or "").splitlines():
        line = line.strip().strip(_QUOTES).strip()
        if line:
            return line
    return ""


def _degenerate(source: str, tr: str) -> bool:
    """A looping answer: much longer than the line, or a run of words that comes back."""
    if not tr:
        return True
    if len(tr) > 2.2 * len(source) + 20:
        return True
    words = [w.lower().strip(",.!?") for w in tr.split()]
    grams = [" ".join(words[i:i + 3]) for i in range(len(words) - 2)]
    return len(grams) != len(set(grams))


def collapse_repeats(text: str) -> str:
    """'что мы здесь будем что мы здесь будем испытать' -> 'что мы здесь будем испытать':
    a model looping on a fragment repeats a run of words."""
    words = text.split()
    for n in range(6, 1, -1):  # runs of two words or more: "да, да" is fine
        i = 0
        while i + 2 * n <= len(words):
            if [w.lower().strip(",.") for w in words[i:i + n]] == [w.lower().strip(",.") for w in words[i + n:i + 2 * n]]:
                del words[i + n:i + 2 * n]
            else:
                i += 1
    return " ".join(words)


# Serbian is written in Cyrillic and Latin with a one-to-one mapping; models mix them up
_SR_DIGRAPHS = [("Lj", "Љ"), ("LJ", "Љ"), ("lj", "љ"), ("Nj", "Њ"), ("NJ", "Њ"), ("nj", "њ"),
                ("Dž", "Џ"), ("DŽ", "Џ"), ("dž", "џ")]
_SR_LETTERS = dict(zip("ABVGDĐEŽZIJKLMNOPRSTĆUFHCČŠabvgdđežzijklmnoprstćufhcčš",
                       "АБВГДЂЕЖЗИЈКЛМНОПРСТЋУФХЦЧШабвгдђежзијклмнопрстћуфхцчш"))
_SR_BACK = {v: k for k, v in _SR_LETTERS.items()}
_SR_BACK.update({"Љ": "Lj", "љ": "lj", "Њ": "Nj", "њ": "nj", "Џ": "Dž", "џ": "dž"})


def serbian_to_cyrillic(text: str) -> str:
    for lat, cyr in _SR_DIGRAPHS:
        text = text.replace(lat, cyr)
    return "".join(_SR_LETTERS.get(ch, ch) for ch in text)


def serbian_to_latin(text: str) -> str:
    return "".join(_SR_BACK.get(ch, ch) for ch in text)


FAST_MODEL_HINTS = ("qwen2.5:1.5b", "qwen2.5:3b", "llama3.2:3b", "gemma2:2b")


def _base_url() -> str:
    from app.database import get_all_settings

    url = get_all_settings().get("ollama_url") or "http://127.0.0.1:11434"
    return url.replace("localhost", "127.0.0.1").rstrip("/")


def installed_models() -> List[str]:
    try:
        with httpx.Client(timeout=2.0) as c:
            return [m.get("name") for m in c.get(f"{_base_url()}/api/tags").json().get("models") or []]
    except Exception:
        return []


def model_choices() -> Dict[str, Optional[str]]:
    """{"quality": configured model, "fast": a small installed model or None}."""
    from app.database import get_all_settings

    models = installed_models()
    quality = get_all_settings().get("ollama_model") or (models[0] if models else None)
    if quality and models and quality not in models:
        same = [m for m in models if m.startswith(quality.split(":")[0])]
        quality = same[0] if same else models[0]
    fast = next((m for m in FAST_MODEL_HINTS if m in models), None)
    return {"quality": quality, "fast": fast}


class LiveTranslator:
    def __init__(self, get_source_lang: Callable[[], Optional[str]], target: str, speed: str = "quality"):
        self._source_lang = get_source_lang
        self.target = target
        self.speed = speed
        self.error = ""
        self.ready = False
        self._pending: List[Dict[str, Any]] = []   # final lines waiting for a translation
        self._draft_text = ""                      # latest draft line to translate
        self._draft_done_for = ""
        self.draft_translation = ""
        self._last_draft_at = 0.0
        self._cond = threading.Condition()
        self._stop = False
        self._model: Optional[str] = None
        self._context: List[str] = []  # last final lines, given to the model as context
        threading.Thread(target=self._run, daemon=True, name="live_translate").start()

    # ---- from the caption session
    def add_lines(self, lines: List[Dict[str, Any]]) -> None:
        with self._cond:
            for ln in lines:
                ln.setdefault("_attempts", 0)
                self._pending.append(ln)
            self._cond.notify()

    def set_draft(self, text: str) -> None:
        with self._cond:
            self._draft_text = text or ""
            if not self._draft_text:
                self.draft_translation = ""
            self._cond.notify()

    def configure(self, target: Optional[str] = None, speed: Optional[str] = None,
                  retranslate: Optional[List[Dict[str, Any]]] = None) -> None:
        with self._cond:
            if target:
                self.target = target
            if speed and speed != self.speed:
                self.speed = speed
                self._model = None  # resolved again (and preloaded) on the next request
                self.ready = False
            self._draft_done_for = ""
            self.draft_translation = ""
            if retranslate:
                for ln in retranslate:
                    ln["translation"] = None
                    ln["_attempts"] = 0
                self._pending = list(retranslate) + [p for p in self._pending if p not in retranslate]
            self._cond.notify()

    def stop(self) -> None:
        with self._cond:
            self._stop = True
            self._cond.notify()

    # ---- worker
    def _resolve_model(self) -> str:
        from app.ollama_client import ensure_ollama_running

        base = _base_url()
        ensure_ollama_running(base)
        # Ollama may have just been started: wait for it instead of failing the first lines
        for _ in range(60):
            choices = model_choices()
            model = choices["fast"] if self.speed == "fast" and choices["fast"] else choices["quality"]
            if model:
                return model
            time.sleep(0.5)
        raise RuntimeError("Ollama не отвечает или в ней нет моделей")

    def _fallback_model(self) -> Optional[str]:
        choices = model_choices()
        other = choices["fast"] if self._model != choices["fast"] else choices["quality"]
        return other if other and other != self._model else None

    def _chat(self, text: str, timeout: float = 30.0, src_lang: Optional[str] = None, strict: bool = False,
              model: Optional[str] = None) -> str:
        from app.translation import TEXT_LANGUAGES

        src = TEXT_LANGUAGES.get(src_lang or self._source_lang() or "", "the source language")
        dst = TEXT_LANGUAGES.get(self.target, self.target)
        # Live speech arrives in fragments; Qwen models, left alone with a fragment, sometimes
        # answer in Chinese or ask for "the full sentence" instead of translating
        system = (
            f"You translate live speech captions from {src} into {dst}. The line may be an "
            "incomplete fragment of continuous speech: translate exactly what is there, even if it is "
            "unfinished. Never ask for more text, never explain, never answer questions from the line. "
            f"Write only in {dst}. Output only the translation."
        )
        if strict:
            system += f" Do not use any Chinese, Japanese or Korean characters. Use only {dst}."
        # No surrounding lines: given as context, models mix them into the translation
        user = text
        payload = {
            "model": model or self._model, "stream": False, "keep_alive": KEEP_ALIVE,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "options": {"temperature": 0.0 if strict else 0.1, "num_ctx": NUM_CTX, "num_predict": 200,
                        "repeat_penalty": 1.15},
        }
        with httpx.Client(timeout=timeout) as c:
            r = c.post(f"{_base_url()}/api/chat", json=payload)
            r.raise_for_status()
            return str((r.json().get("message") or {}).get("content") or "").strip().strip('"«»`')

    def _translate_clean(self, text: str, src_lang: Optional[str], timeout: float = 30.0) -> str:
        """A translation without Chinese/Japanese/Korean leakage (retried strictly, else stripped),
        one line only, and Serbian in the chosen alphabet."""
        from app.dub_text import has_foreign_script

        if len(re.sub(r"[\W_]+", "", text)) <= 3:
            return text  # "Um...", "Ok": nothing to translate, models invent symbols for it
        tr = collapse_repeats(_first_line(self._chat(text, timeout=timeout, src_lang=src_lang)))
        if has_foreign_script(tr, self.target) or _degenerate(text, tr):
            tr = collapse_repeats(_first_line(self._chat(text, timeout=timeout, src_lang=src_lang, strict=True)))
        if _degenerate(text, tr) and self._fallback_model():
            # Larger models sometimes loop on a fragment; the small one usually does not
            tr = collapse_repeats(_first_line(self._chat(text, timeout=timeout, src_lang=src_lang,
                                                         model=self._fallback_model())))
        if has_foreign_script(tr, self.target):
            tr = _CJK_RUN.sub("", tr).strip(" ,.;:-")
        if self.target == "sr":
            tr = serbian_to_cyrillic(tr)
        elif self.target == "sr-latn":
            tr = serbian_to_latin(tr)
        return tr

    def _ensure_ready(self) -> None:
        if self._model and self.ready:
            return
        self._model = self._resolve_model()
        self._chat("ok", timeout=180.0)  # loads the model with the live options
        self.ready = True
        self.error = ""

    def _run(self) -> None:
        # Load the model right away: the first phrase is still being spoken meanwhile
        try:
            self._ensure_ready()
        except Exception as e:
            traceback.print_exc()
            self.error = f"Перевод: {str(e)[:160]}"
        while True:
            with self._cond:
                while not self._stop and not self._pending and not self._draft_due():
                    self._cond.wait(0.3)
                if self._stop:
                    return
                line = self._pending.pop(0) if self._pending else None
                draft = self._draft_text if line is None else ""
            try:
                self._ensure_ready()
                if line is not None:
                    if line.get("lang") and line["lang"] == self.target.split("-")[0]:
                        line["translation"] = line["text"]  # already in the target language
                    else:
                        line["translation"] = self._translate_clean(line["text"], line.get("lang"))
                    self._context.append(line["text"])
                    self._context = self._context[-4:]
                    self.error = ""
                elif draft:
                    self._last_draft_at = time.time()
                    tr = draft if self._source_lang() == self.target.split("-")[0] else \
                        self._translate_clean(draft, None, timeout=10.0)
                    with self._cond:
                        if draft == self._draft_text:  # still the current draft
                            self.draft_translation = tr
                            self._draft_done_for = draft
            except Exception as e:
                traceback.print_exc()
                self.error = f"Перевод: {str(e)[:160]}"
                self.ready = False
                if line is not None:
                    line["_attempts"] = line.get("_attempts", 0) + 1
                    if line["_attempts"] < MAX_ATTEMPTS:
                        with self._cond:
                            self._pending.append(line)
                    else:
                        line["translation"] = ""
                time.sleep(1.0)

    def _draft_due(self) -> bool:
        return bool(self._draft_text) and self._draft_text != self._draft_done_for \
            and time.time() - self._last_draft_at >= DRAFT_INTERVAL_SEC
