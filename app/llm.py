"""
The language model for analysis: overviews, tasks, chapters, questions, titles, Obsidian notes.

By default it is the local Ollama. Settings → Analysis can switch analysis to a cloud model over its
API instead: OpenAI (ChatGPT), Anthropic (Claude), Google (Gemini), or any OpenAI-compatible service
(OpenRouter, DeepSeek, a local LM Studio…). Then the transcript text of the recording being
analysed is sent to that service, which is why the switch needs the user's explicit consent in the
UI. Translation of transcripts, live captions and the dub always stay on Ollama.

`target()` returns the configured model; `Target.chat(system, user)` is all the callers use.
A cloud model has a much larger context, so `max_chars` lets callers send long transcripts at once
instead of summarising them in parts.
"""

import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

import httpx

from app import secret_store

PROVIDERS: Dict[str, Dict[str, Any]] = {
    "openai": {
        "name": "OpenAI (ChatGPT)",
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4.1-mini",
        "suggested": ["gpt-4.1-mini", "gpt-4.1", "gpt-4o-mini", "gpt-4o"],
        "keys_url": "https://platform.openai.com/api-keys",
    },
    "anthropic": {
        "name": "Anthropic (Claude)",
        "base_url": "https://api.anthropic.com/v1",
        "default_model": "claude-sonnet-5-5",
        "suggested": ["claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5"],
        "keys_url": "https://console.anthropic.com/settings/keys",
    },
    "gemini": {
        "name": "Google (Gemini)",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "default_model": "gemini-2.5-flash",
        "suggested": ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.5-flash-lite"],
        "keys_url": "https://aistudio.google.com/apikey",
    },
    "openai_compat": {
        "name": "OpenAI-compatible",
        "base_url": "",
        "default_model": "",
        "suggested": [],
        "keys_url": "",
    },
}

CLOUD_MAX_CHARS = 300_000     # one request: about 100k tokens of Russian text
OLLAMA_MAX_CHARS = 20_000     # app/ollama_client.SINGLE_PASS_MAX_CHARS
TIMEOUT_SEC = 300.0
RETRIES = 2                   # rate limit or a server error: try again after a pause


class LlmError(RuntimeError):
    pass


def _settings() -> Dict[str, str]:
    from app.database import get_all_settings

    return get_all_settings()


def key_name(provider: str) -> str:
    return f"llm_key_{provider}"


def cloud_provider(settings: Optional[Dict[str, str]] = None) -> str:
    settings = settings or _settings()
    p = settings.get("llm_cloud_provider") or "openai"
    return p if p in PROVIDERS else "openai"


def cloud_model(provider: str, settings: Optional[Dict[str, str]] = None) -> str:
    settings = settings or _settings()
    return (settings.get(f"llm_model_{provider}") or "").strip() or PROVIDERS[provider]["default_model"]


def cloud_base_url(provider: str, settings: Optional[Dict[str, str]] = None) -> str:
    settings = settings or _settings()
    if provider == "openai_compat":
        return (settings.get("llm_base_url_openai_compat") or "").strip().rstrip("/")
    return PROVIDERS[provider]["base_url"]


def uses_cloud(settings: Optional[Dict[str, str]] = None) -> bool:
    settings = settings or _settings()
    return (settings.get("llm_provider") or "ollama") == "cloud"


@dataclass
class Target:
    provider: str          # "ollama" or a key of PROVIDERS
    model: str
    max_chars: int
    chat_fn: Callable[[str, str], str]

    @property
    def is_cloud(self) -> bool:
        return self.provider != "ollama"

    @property
    def label(self) -> str:
        return self.model if not self.is_cloud else f"{PROVIDERS[self.provider]['name']} · {self.model}"

    def chat(self, system: str, user: str) -> str:
        if self.is_cloud:
            log_cloud_request(self.provider, self.model, system, user)
        return self.chat_fn(system, user)


def log_cloud_request(provider: str, model: str, system: str, user: str) -> None:
    """What left the computer: kept in the local database for the privacy log (Settings → Analysis)."""
    from datetime import datetime

    from app.database import _db_lock, get_connection

    try:
        with _db_lock:
            conn = get_connection()
            conn.execute("""CREATE TABLE IF NOT EXISTS cloud_log (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,
                provider TEXT NOT NULL, model TEXT NOT NULL, chars INTEGER NOT NULL, tokens INTEGER NOT NULL, purpose TEXT NOT NULL)""")
            purpose = " ".join((system or "").split())[:160]
            conn.execute("INSERT INTO cloud_log (at, provider, model, chars, tokens, purpose) VALUES (?,?,?,?,?,?)",
                         (datetime.now().isoformat(timespec="seconds"), provider, model, len(system) + len(user),
                          estimate_tokens(system + user), purpose))
            conn.execute("DELETE FROM cloud_log WHERE id NOT IN (SELECT id FROM cloud_log ORDER BY id DESC LIMIT 1000)")
            conn.commit()
            conn.close()
    except Exception as e:  # the log must never stop the request
        print(f"[llm] cloud log: {e}", flush=True)


def cloud_log(limit: int = 200) -> list:
    from app.database import _db_lock, get_connection

    with _db_lock:
        conn = get_connection()
        try:
            rows = [dict(r) for r in conn.execute("SELECT * FROM cloud_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]
        except Exception:
            rows = []
        conn.close()
    return rows


def clear_cloud_log() -> None:
    from app.database import _db_lock, get_connection

    with _db_lock:
        conn = get_connection()
        try:
            conn.execute("DELETE FROM cloud_log")
            conn.commit()
        except Exception:
            pass
        conn.close()


def estimate_tokens(text: str) -> int:
    """Rough token count of a text for the size shown before a cloud request: Cyrillic takes about
    one token per 2.5 characters in current tokenizers, Latin text about one per 4."""
    cyr = sum(1 for ch in text if "\u0400" <= ch <= "\u04ff")
    return int(cyr / 2.5 + (len(text) - cyr) / 4) + 1


LOCAL_ONLY_ERROR = ("Запись помечена «только локальный анализ»: она не отправляется в облачную модель. "
                    "Выберите модель Ollama или снимите пометку в меню записи.")


def target(model: Optional[str] = None, start_ollama: bool = True, local_only: bool = False) -> Target:
    """The model to use. `model`: "cloud" forces the cloud model, an Ollama tag forces Ollama with
    that model, None follows the settings. `local_only`: the text is from a recording marked "local
    analysis only" (app.database.is_local_only), so the settings' cloud choice gives way to Ollama.
    Raises LlmError when it cannot be used."""
    settings = _settings()
    if local_only and model == "cloud":
        raise LlmError(LOCAL_ONLY_ERROR)
    if model == "cloud" or (model is None and uses_cloud(settings) and not local_only):
        return _cloud_target(settings)
    return _ollama_target(model, settings, start_ollama)


def available(settings: Optional[Dict[str, str]] = None, local_only: bool = False) -> bool:
    """Whether analysis can run now without starting anything (for automatic steps after a job)."""
    settings = settings or _settings()
    if uses_cloud(settings) and not local_only:
        p = cloud_provider(settings)
        return bool(secret_store.get_secret(key_name(p))) and bool(cloud_base_url(p, settings)) and bool(cloud_model(p, settings))
    from app.ollama_client import get_ollama_status

    st = get_ollama_status()
    return bool(st["running"] and st["models"])


# ------------------------------------------------------------------ Ollama
def _ollama_target(model: Optional[str], settings: Dict[str, str], start: bool) -> Target:
    from app.ollama_client import _ollama_chat, ensure_ollama_running, get_ollama_status

    base_url = (settings.get("ollama_url") or "http://127.0.0.1:11434").replace("localhost", "127.0.0.1").rstrip("/")
    wanted = model or settings.get("ollama_model") or "qwen2.5:7b"
    if start:
        ensure_ollama_running(base_url)  # analysis is an explicit user action (or one they turned on)
    status = get_ollama_status(base_url)
    if not status["running"]:
        raise LlmError(
            "Локальный сервер Ollama не установлен или не запущен. Откройте «Настройки», выберите модель LLM и нажмите "
            "«Скачать» — или включите облачную модель в «Настройки → Анализ»."
        )
    models = status["models"]
    if not models:
        raise LlmError(f"В Ollama пока не скачана ни одна модель. Откройте «Настройки» и нажмите «Скачать» напротив {wanted}.")
    if wanted not in models:
        same = [m for m in models if m.startswith(wanted.split(":")[0])]
        wanted = same[0] if same else models[0]
    return Target("ollama", wanted, OLLAMA_MAX_CHARS, lambda system, user: _ollama_chat(base_url, wanted, system, user))


# ------------------------------------------------------------------ cloud
def _cloud_target(settings: Dict[str, str]) -> Target:
    provider = cloud_provider(settings)
    key = secret_store.get_secret(key_name(provider))
    if not key:
        raise LlmError(f"Не задан API-ключ для {PROVIDERS[provider]['name']}. Укажите его в «Настройки → Анализ».")
    base = cloud_base_url(provider, settings)
    if not base:
        raise LlmError("Не указан адрес API (Base URL) OpenAI-совместимого сервиса в «Настройки → Анализ».")
    model = cloud_model(provider, settings)
    if not model:
        raise LlmError("Не указана модель облачного сервиса в «Настройки → Анализ».")
    fn = {"anthropic": _anthropic_chat, "gemini": _gemini_chat}.get(provider, _openai_chat)
    return Target(provider, model, CLOUD_MAX_CHARS, lambda system, user: fn(base, key, model, system, user, provider))


def _post(url: str, headers: Dict[str, str], payload: Dict[str, Any], provider: str) -> Dict[str, Any]:
    last: Optional[Exception] = None
    for attempt in range(RETRIES + 1):
        try:
            with httpx.Client(timeout=TIMEOUT_SEC) as client:
                resp = client.post(url, headers=headers, json=payload)
        except httpx.HTTPError as e:
            last = LlmError(f"{PROVIDERS[provider]['name']}: нет соединения ({type(e).__name__}). Проверьте интернет и прокси.")
            time.sleep(2 * (attempt + 1))
            continue
        if resp.status_code in (429, 500, 502, 503, 504, 529) and attempt < RETRIES:
            last = LlmError(_error_text(provider, resp))
            time.sleep(float(resp.headers.get("retry-after") or 0) or 3 * (attempt + 1))
            continue
        if resp.status_code >= 400:
            raise LlmError(_error_text(provider, resp))
        return resp.json()
    raise last or LlmError("Сервис не ответил")


def _error_text(provider: str, resp: httpx.Response) -> str:
    name = PROVIDERS[provider]["name"]
    detail = ""
    try:
        data = resp.json()
        err = data.get("error") if isinstance(data, dict) else None
        if isinstance(err, dict):
            detail = err.get("message") or ""
        elif isinstance(data, list) and data and isinstance(data[0], dict):
            detail = ((data[0].get("error") or {}).get("message")) or ""
        elif err:
            detail = str(err)
    except Exception:
        detail = resp.text[:200]
    reason = {
        400: "запрос отклонён", 401: "неверный API-ключ", 403: "нет доступа к модели или API",
        404: "модель или адрес API не найдены", 413: "текст слишком длинный для модели",
        429: "превышен лимит запросов или закончились средства на счёте",
    }.get(resp.status_code, "ошибка сервиса")
    return f"{name}: {reason} (HTTP {resp.status_code}){': ' + detail[:300] if detail else ''}"


def _openai_chat(base: str, key: str, model: str, system: str, user: str, provider: str = "openai") -> str:
    data = _post(
        f"{base}/chat/completions",
        {"Authorization": f"Bearer {key}"},
        {"model": model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
         "temperature": 0.2},
        provider,
    )
    choices = data.get("choices") or []
    text = ((choices[0].get("message") or {}).get("content") if choices else "") or ""
    if not text.strip():
        raise LlmError("Модель вернула пустой ответ")
    return text.strip()


def _anthropic_chat(base: str, key: str, model: str, system: str, user: str, provider: str = "anthropic") -> str:
    data = _post(
        f"{base}/messages",
        {"x-api-key": key, "anthropic-version": "2023-06-01"},
        {"model": model, "max_tokens": 8192, "system": system, "temperature": 0.2,
         "messages": [{"role": "user", "content": user}]},
        "anthropic",
    )
    text = "".join(b.get("text") or "" for b in data.get("content") or [] if b.get("type") == "text")
    if not text.strip():
        raise LlmError("Модель вернула пустой ответ" + (f" ({data.get('stop_reason')})" if data.get("stop_reason") else ""))
    return text.strip()


def _gemini_chat(base: str, key: str, model: str, system: str, user: str, provider: str = "gemini") -> str:
    data = _post(
        f"{base}/models/{model}:generateContent",
        {"x-goog-api-key": key},
        {"systemInstruction": {"parts": [{"text": system}]},
         "contents": [{"role": "user", "parts": [{"text": user}]}],
         "generationConfig": {"temperature": 0.2}},
        "gemini",
    )
    cands = data.get("candidates") or []
    parts = ((cands[0].get("content") or {}).get("parts") if cands else None) or []
    text = "".join(p.get("text") or "" for p in parts)
    if not text.strip():
        reason = (cands[0].get("finishReason") if cands else None) or (data.get("promptFeedback") or {}).get("blockReason")
        raise LlmError("Модель вернула пустой ответ" + (f" ({reason})" if reason else ""))
    return text.strip()


def list_models(provider: str) -> List[str]:
    """Models the key can use, for the choice in the settings (best effort)."""
    settings = _settings()
    key = secret_store.get_secret(key_name(provider))
    base = cloud_base_url(provider, settings)
    if not key or not base:
        return []
    try:
        with httpx.Client(timeout=20.0) as client:
            if provider == "anthropic":
                r = client.get(f"{base}/models", headers={"x-api-key": key, "anthropic-version": "2023-06-01"}, params={"limit": 100})
                ids = [m.get("id") for m in r.json().get("data") or []]
            elif provider == "gemini":
                r = client.get(f"{base}/models", headers={"x-goog-api-key": key}, params={"pageSize": 200})
                ids = [str(m.get("name") or "").removeprefix("models/") for m in r.json().get("models") or []
                       if "generateContent" in (m.get("supportedGenerationMethods") or [])]
            else:
                r = client.get(f"{base}/models", headers={"Authorization": f"Bearer {key}"})
                ids = [m.get("id") for m in r.json().get("data") or []]
                if provider == "openai":
                    # Only chat models: no embeddings, audio, images or moderation
                    skip = ("embedding", "whisper", "tts", "dall-e", "image", "moderation", "audio", "realtime", "transcribe", "search")
                    ids = [i for i in ids if i and i.startswith(("gpt", "o1", "o3", "o4", "chatgpt")) and not any(s in i for s in skip)]
            if r.status_code >= 400:
                return []
    except Exception:
        return []
    return sorted({i for i in ids if i})


def test_connection(provider: str) -> Dict[str, Any]:
    settings = _settings()
    t0 = time.time()
    tgt = _cloud_target({**settings, "llm_cloud_provider": provider})
    reply = tgt.chat("Answer with one word.", "Say: ok")
    return {"ok": True, "model": tgt.model, "reply": reply[:60], "seconds": round(time.time() - t0, 1)}


def status() -> Dict[str, Any]:
    settings = _settings()
    providers = {}
    for p, meta in PROVIDERS.items():
        providers[p] = {
            "name": meta["name"], "default_model": meta["default_model"], "suggested": meta["suggested"],
            "keys_url": meta["keys_url"], "model": cloud_model(p, settings), "base_url": cloud_base_url(p, settings),
            "key": secret_store.hint(key_name(p)),
        }
    return {
        "mode": "cloud" if uses_cloud(settings) else "ollama",
        "provider": cloud_provider(settings),
        "consent": settings.get("llm_cloud_consent") == "true",
        "providers": providers,
    }


# ------------------------------------------------------------------ choosing a model
_SKIP_WORDS = ("embed", "tts", "audio", "image", "vision-only", "live", "realtime", "transcribe", "search",
               "moderation", "aqa", "learnlm", "gemma", "imagen", "veo", "computer-use", "robotics", "deep-research")
_PREFER = {
    "gemini": ("flash",),     # fast and cheap enough for long transcripts
    "anthropic": ("sonnet",),
    "openai": ("mini",),
    "openai_compat": (),
}


def _version_key(model_id: str) -> tuple:
    nums = [int(n) for n in re.findall(r"\d+", model_id)[:4]]
    return tuple(nums)


def pick_default_model(provider: str, models: List[str]) -> Optional[str]:
    """A sensible model from the ones the key can use: the newest of the preferred family
    (Gemini Flash, Claude Sonnet, GPT mini), without previews, experiments and lite versions."""
    usable = [m for m in models if not any(w in m.lower() for w in _SKIP_WORDS)]
    if not usable:
        return models[0] if models else None
    stable = [m for m in usable if not re.search(r"preview|exp|experimental|latest|\d{4}-\d{2}-\d{2}|-\d{8}$", m.lower())] or usable
    prefer = _PREFER.get(provider) or ()
    family = [m for m in stable if any(p in m.lower() for p in prefer) and "lite" not in m.lower() and "nano" not in m.lower()] or stable
    return max(family, key=_version_key)


def ensure_model(provider: str) -> Dict[str, Any]:
    """After a key is saved: if the chosen model is not available with it, switch to a default one.
    Returns {"models": [...], "model": chosen, "changed": bool}."""
    from app.database import update_settings

    models = list_models(provider)
    current = cloud_model(provider)
    if not models or current in models:
        return {"models": models, "model": current, "changed": False}
    better = pick_default_model(provider, models)
    if better and better != current:
        update_settings({f"llm_model_{provider}": better})
        return {"models": models, "model": better, "changed": True}
    return {"models": models, "model": current, "changed": False}
