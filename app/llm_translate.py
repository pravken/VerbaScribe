"""
Line-by-line translation with the local Ollama model.

Shared by the dub pipeline (short spoken phrases that must keep their length) and the
"Translate text" feature (transcript segments, read as text). Texts are sent in numbered batches;
a line the model skips or garbles is retried on its own.
"""

import json
import re
from typing import Callable, Dict, List, Optional, Tuple

import httpx

from app.database import get_all_settings
from app.dub_text import has_foreign_script
from app.ollama_client import ensure_ollama_running, get_ollama_status, raise_for_ollama

_LINE_RE = re.compile(r"^\s*(\d+)\s*[.):]\s*(.+?)\s*$")

_SYSTEM_PROMPTS = {
    "dub": (
        "You are a professional dubbing translator from {src} to {tgt}. "
        "Each numbered line is one spoken phrase of a video. The translation will be voiced over the "
        "original timing, so keep every line about as long as the original when spoken (similar number "
        "of syllables), natural and conversational. Never merge, split, skip or explain lines. "
        "Answer only in {tgt}, with exactly the same numbering, one line per phrase: `N. translation`."
    ),
    "text": (
        "You are a professional translator from {src} to {tgt}. "
        "Each numbered line is one utterance from a transcript of a recorded conversation, in order. "
        "Translate each line faithfully and naturally, keeping names, numbers and terms. Use the "
        "surrounding lines only as context. Never merge, split, skip, summarize or explain lines. "
        "Answer only in {tgt}, with exactly the same numbering, one line per utterance: `N. translation`."
    ),
}


def ollama_chat(
    base_url: str,
    model: str,
    system: str,
    user: str,
    keep_alive: str = "5m",
    check_cancel: Optional[Callable[[], None]] = None,
) -> str:
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "stream": check_cancel is not None,
        "keep_alive": keep_alive,
        "options": {"temperature": 0.2, "top_p": 0.9, "num_ctx": 8192},
    }
    from app.llm_text import strip_think, thinking_off

    payload.update(thinking_off(model))
    with httpx.Client(timeout=600.0) as client:
        if check_cancel is None:
            resp = client.post(f"{base_url}/api/chat", json=payload)
            raise_for_ollama(resp)
            return strip_think(str((resp.json().get("message") or {}).get("content") or ""))
        # Streamed, so a cancel request is noticed between tokens; closing the connection also
        # makes Ollama stop generating.
        parts: List[str] = []
        with client.stream("POST", f"{base_url}/api/chat", json=payload) as resp:
            raise_for_ollama(resp)
            for line in resp.iter_lines():
                check_cancel()
                if not line.strip():
                    continue
                chunk = json.loads(line)
                if chunk.get("error"):
                    raise RuntimeError(f"Ollama: {chunk['error']}")
                parts.append(str((chunk.get("message") or {}).get("content") or ""))
                if chunk.get("done"):
                    break
        return strip_think("".join(parts))


def parse_numbered_lines(answer: str, count: int) -> List[Optional[str]]:
    """Maps `N. text` lines of a model answer back to their input positions (first answer wins)."""
    out: List[Optional[str]] = [None] * count
    for line in answer.splitlines():
        m = _LINE_RE.match(line)
        if m:
            idx = int(m.group(1)) - 1
            if 0 <= idx < count and out[idx] is None:
                out[idx] = m.group(2).strip().strip("`")
    return out


def translate_batch(
    base_url: str, model: str, texts: List[str], src: str, tgt: str, style: str = "dub",
    check_cancel: Optional[Callable[[], None]] = None,
) -> List[Optional[str]]:
    system = _SYSTEM_PROMPTS[style].format(src=src, tgt=tgt)
    # Line breaks inside one text would break the numbering
    user = "\n".join(f"{i + 1}. {' '.join(t.split())}" for i, t in enumerate(texts))
    return parse_numbered_lines(ollama_chat(base_url, model, system, user, check_cancel=check_cancel), len(texts))


def translate_single(
    base_url: str, model: str, text: str, src: str, tgt: str, style: str = "dub",
    check_cancel: Optional[Callable[[], None]] = None,
) -> str:
    if style == "dub":
        system = (
            f"Translate the user's {src} phrase into natural spoken {tgt} of about the same length. "
            "Output only the translation, nothing else."
        )
    else:
        system = f"Translate the user's {src} text into {tgt}. Output only the translation, nothing else."
    return ollama_chat(base_url, model, system, text, check_cancel=check_cancel).strip().strip('"«»`')


def resolve_ollama(preferred_model: Optional[str] = None) -> Tuple[str, str]:
    """Starts Ollama if needed and returns (base_url, model); the preferred model wins if installed."""
    settings = get_all_settings()
    base_url = (settings.get("ollama_url") or "http://127.0.0.1:11434").replace("localhost", "127.0.0.1").rstrip("/")
    model = preferred_model or settings.get("ollama_model") or "qwen3.5:4b"
    ensure_ollama_running(base_url)
    st = get_ollama_status(base_url)
    usable = st.get("chat_models", st["models"])
    if not st["running"] or not usable:
        raise RuntimeError("Ollama не запущена или в ней нет моделей — перевод невозможен")
    if model not in usable:
        model = usable[0]
    return base_url, model


def translate_texts(
    texts: List[str],
    src: str,
    tgt: str,
    *,
    base_url: str,
    model: str,
    style: str,
    check_cancel: Callable[[], None],
    on_batch: Optional[Callable[[int, Dict[int, str]], None]] = None,
    batch_size: int = 12,
    tgt_code: str = "",
) -> List[str]:
    """
    Translates `texts` in batches. `on_batch(done_count, {index: translation})` is called after
    every batch, so a caller can save partial results and report progress.
    """
    result: List[str] = [""] * len(texts)
    for b in range(0, len(texts), batch_size):
        check_cancel()
        chunk = texts[b : b + batch_size]
        res = translate_batch(base_url, model, chunk, src, tgt, style, check_cancel)
        got: Dict[int, str] = {}
        for j, (src_text, tr) in enumerate(zip(chunk, res)):
            # Retry lines the model skipped or mixed with CJK characters (Qwen sometimes does)
            if not tr or (tgt_code and has_foreign_script(tr, tgt_code)):
                check_cancel()
                tr = translate_single(base_url, model, src_text, src, tgt, style, check_cancel)
            result[b + j] = tr
            got[b + j] = tr
        if on_batch:
            on_batch(min(len(texts), b + batch_size), got)
    return result
