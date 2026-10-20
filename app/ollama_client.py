import json
import re
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from app.audio_utils import format_timestamp
from app.database import get_all_settings, update_settings

RECOMMENDED_LLM_MODELS: List[Dict[str, Any]] = [
    {
        "id": "qwen2.5:7b",
        "name": "Qwen 7B Standard",
        "badge": "4.7 GB · Apache",
        "size": "4.7 GB",
        "vram": "8+ GB VRAM",
        "license": "Apache",
        "desc_ru": "Оптимальный выбор — отличный русский и английский, свободная коммерческая лицензия",
        "desc_en": "Recommended — excellent RU & EN quality, free commercial license",
    },
    {
        "id": "qwen2.5:14b",
        "name": "Qwen 14B Pro",
        "badge": "9 GB · Apache",
        "size": "9 GB",
        "vram": "12–24 GB VRAM",
        "license": "Apache",
        "desc_ru": "Максимальный интеллект и глубина анализа",
        "desc_en": "Maximum reasoning & detailed analysis",
    },
    {
        "id": "mistral:7b",
        "name": "Mistral 7B",
        "badge": "4.1 GB · Apache",
        "size": "4.1 GB",
        "vram": "6–8 GB VRAM",
        "license": "Apache",
        "desc_ru": "Европейская открытая модель от Mistral AI",
        "desc_en": "Open-weights model by Mistral AI",
    },
    {
        "id": "qwen2.5:1.5b",
        "name": "Qwen Lite",
        "badge": "1 GB · Apache",
        "size": "1 GB",
        "vram": "2–4 GB / CPU",
        "license": "Apache",
        "desc_ru": "Сверхлегкая и быстрая модель для слабых ПК и процессоров",
        "desc_en": "Ultra-lightweight model for low-VRAM GPUs and CPUs",
    },
]

_LLM_DISPLAY_NAMES: Dict[str, str] = {
    "qwen2.5:7b": "Qwen 7B Standard",
    "qwen2.5:14b": "Qwen 14B Pro",
    "mistral:7b": "Mistral 7B",
    "qwen2.5:1.5b": "Qwen Lite",
}


def format_llm_model_name(model_id: str) -> str:
    """
    Formats a technical Ollama tag (e.g. 'qwen2.5:1.5b') into a clean human-readable title
    without colons or version dots running together.
    """
    raw = (model_id or "").strip()
    if not raw:
        return "Ollama AI"
    lower = raw.lower()
    if lower in _LLM_DISPLAY_NAMES:
        return _LLM_DISPLAY_NAMES[lower]
    for k, v in _LLM_DISPLAY_NAMES.items():
        if lower.startswith(k):
            return v
    # Clean fallback for custom models: e.g. 'llama3.2:8b' -> 'Llama 8B'
    parts = raw.split(":")
    family = parts[0].replace("-", " ").replace("_", " ")
    # Strip trailing version numbers/dots from family name if followed by size tag
    import re
    family_clean = re.sub(r"[\d\.]+$", "", family).strip().title() or family.title()
    if len(parts) > 1 and parts[1].lower() != "latest":
        tag = parts[1].upper().replace(".", " ")
        return f"{family_clean} {tag}"
    return family_clean


_pull_state: Dict[str, Any] = {
    "active": False,
    "model": "",
    "status": "Ожидание",
    "progress": 0,
    "downloaded_mb": 0.0,
    "total_mb": 0.0,
    "error": "",
    "logs": [],
}
_pull_lock = threading.Lock()


def _append_log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    logs = _pull_state.get("logs", [])
    logs.append(line)
    if len(logs) > 80:
        logs[:] = logs[-80:]
    _pull_state["logs"] = logs
    _pull_state["status"] = msg


def find_ollama_executable() -> Optional[str]:
    in_path = shutil.which("ollama")
    if in_path:
        return in_path

    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if local_app_data:
        candidate = Path(local_app_data) / "Programs" / "Ollama" / "ollama.exe"
        if candidate.exists():
            return str(candidate)
    return None


def _is_ollama_port_open(host: str = "127.0.0.1", port: int = 11434) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.15)
        return s.connect_ex((host, port)) == 0


def ensure_ollama_running(base_url: str = "http://127.0.0.1:11434", auto_start: bool = True) -> bool:
    if _is_ollama_port_open():
        return True

    if not auto_start:
        return False

    exe = find_ollama_executable()
    if exe:
        try:
            subprocess.Popen(
                [exe, "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
            )
            for _ in range(8):
                time.sleep(0.25)
                if _is_ollama_port_open():
                    return True
        except Exception:
            pass
    return False


def get_ollama_status(base_url: Optional[str] = None) -> Dict[str, Any]:
    settings = get_all_settings()
    if not base_url:
        base_url = settings.get("ollama_url", "http://127.0.0.1:11434")
    base_url = base_url.replace("localhost", "127.0.0.1")
    active_model = settings.get("ollama_model", "qwen2.5:7b")

    exe = find_ollama_executable()
    # Status polling never starts background processes: Ollama is only launched after an
    # explicit user action (model download or AI summary request).
    running = _is_ollama_port_open()
    models: List[str] = []
    if running:
        try:
            with httpx.Client(timeout=0.8) as client:
                resp = client.get(f"{base_url.rstrip('/')}/api/tags")
                if resp.status_code == 200:
                    data = resp.json()
                    models = [m["name"] for m in data.get("models", [])]
        except Exception:
            running = False

    # Build catalog with installed flags
    catalog = []
    installed_set = set(models)
    for item in RECOMMENDED_LLM_MODELS:
        is_inst = item["id"] in installed_set or any(
            m.startswith(item["id"] + "-") for m in installed_set
        )
        catalog.append({**item, "installed": is_inst})

    # Also append any other custom models the user has in Ollama
    known_ids = {item["id"] for item in RECOMMENDED_LLM_MODELS}
    for m in models:
        if m not in known_ids:
            catalog.append(
                {
                    "id": m,
                    "name": format_llm_model_name(m),
                    "badge": "Локальная",
                    "size": "Локальная",
                    "vram": "GPU/CPU",
                    "desc_ru": "Установленная пользователем модель в Ollama",
                    "desc_en": "User-installed Ollama model",
                    "installed": True,
                }
            )

    return {
        "installed": exe is not None or running,
        "running": running,
        "base_url": base_url,
        "active_model": active_model,
        "models": models,
        "catalog": catalog,
        "pull_state": dict(_pull_state),
    }


def _install_ollama_if_needed() -> bool:
    """
    If Ollama is not installed on Windows, installs it automatically via winget
    and streams progress into _pull_state['logs'].
    """
    if find_ollama_executable() is not None:
        return True

    _append_log("Сервер Ollama не найден на ПК. Запускаю автоматическую установку Ollama через winget")
    _pull_state["progress"] = 5

    cmd = [
        "winget",
        "install",
        "-e",
        "--id",
        "Ollama.Ollama",
        "--accept-source-agreements",
        "--accept-package-agreements",
        "--silent",
    ]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
    )

    if proc.stdout:
        for line in proc.stdout:
            clean = line.strip()
            if clean and len(clean) > 3 and not all(c in "-\\|/" for c in clean):
                _append_log(f"winget: {clean[:100]}")

    proc.wait()
    time.sleep(2.0)

    if find_ollama_executable() is not None:
        _append_log("Ollama успешно установлена на компьютер!")
        return True

    return False


def start_model_pull(
    model_name: str,
    base_url: Optional[str] = None,
    allow_install: bool = False,
) -> Dict[str, Any]:
    """
    Starts downloading an Ollama model in a background thread with real-time logs and progress.
    Ollama itself is installed via winget only when the user explicitly confirmed it (allow_install).
    """
    if not base_url:
        base_url = get_all_settings().get("ollama_url", "http://127.0.0.1:11434")
    base_url = base_url.replace("localhost", "127.0.0.1")
    pretty_name = format_llm_model_name(model_name)

    with _pull_lock:
        if _pull_state["active"]:
            return dict(_pull_state)
        _pull_state.update(
            {
                "active": True,
                "model": model_name,
                "status": f"Подготовка к загрузке {pretty_name}",
                "progress": 1,
                "downloaded_mb": 0.0,
                "total_mb": 0.0,
                "error": "",
                "logs": [],
            }
        )
        _append_log(f"Запрошена установка модели {pretty_name}")

    def _worker():
        try:
            # 1. Check / Install Ollama if not present
            if not ensure_ollama_running(base_url):
                if find_ollama_executable() is None and not allow_install:
                    raise RuntimeError(
                        "Ollama не установлена. Подтвердите установку в окне настроек или установите её вручную: https://ollama.com/download"
                    )
                installed_ok = _install_ollama_if_needed()
                if not installed_ok:
                    raise RuntimeError(
                        "Не удалось автоматически установить Ollama. Скачайте установщик с https://ollama.com/download"
                    )
                _append_log("Запуск фоновой службы Ollama")
                if not ensure_ollama_running(base_url):
                    raise RuntimeError("Ollama установлена, но локальный сервер ещё не ответил. Попробуйте через 5 секунд.")

            _append_log("Подключение к локальному серверу Ollama")
            _pull_state["progress"] = 8

            last_logged_status = ""
            last_logged_pct = -5

            with httpx.Client(timeout=None) as client:
                with client.stream(
                    "POST",
                    f"{base_url.rstrip('/')}/api/pull",
                    json={"name": model_name, "stream": True},
                ) as resp:
                    resp.raise_for_status()
                    for line in resp.iter_lines():
                        if not line:
                            continue
                        data = json.loads(line)
                        if "error" in data and data["error"]:
                            raise RuntimeError(str(data["error"]))

                        status_txt = data.get("status", "Загрузка")
                        total = data.get("total", 0)
                        completed = data.get("completed", 0)

                        if total and total > 0:
                            dl_mb = round(completed / (1024 * 1024), 1)
                            tot_mb = round(total / (1024 * 1024), 1)
                            pct = int(round((completed / total) * 100))
                            _pull_state.update(
                                {
                                    "downloaded_mb": dl_mb,
                                    "total_mb": tot_mb,
                                    "progress": max(1, min(99, pct)),
                                    "status": f"Скачивание {pretty_name} — {dl_mb} из {tot_mb} МБ ({pct}%)",
                                }
                            )
                            if pct - last_logged_pct >= 10:
                                last_logged_pct = pct
                                _append_log(f"Скачивание {pretty_name} — {dl_mb} / {tot_mb} МБ ({pct}%)")
                        else:
                            _pull_state["status"] = status_txt
                            if status_txt != last_logged_status:
                                last_logged_status = status_txt
                                _append_log(f"Ollama — {status_txt}")

            # Automatically set this newly downloaded model as the active LLM model
            update_settings({"ollama_model": model_name})
            _append_log(f"Готово! Модель {pretty_name} успешно скачана и выбрана как активная.")
            _pull_state.update(
                {
                    "active": False,
                    "status": f"Модель {pretty_name} установлена и активна",
                    "progress": 100,
                }
            )
        except Exception as e:
            _append_log(f"ОШИБКА: {e}")
            _pull_state.update(
                {
                    "active": False,
                    "status": f"Ошибка: {e}",
                    "error": str(e),
                }
            )

    threading.Thread(target=_worker, daemon=True).start()
    return dict(_pull_state)


# A long segment (one speaker talking for a minute) gets a time stamp for every phrase inside it,
# so the analysis can point to the moment, not only to where the speaker started
LINE_SPLIT_SEC = 6.0
LINE_MAX_SEC = 15.0
_SENTENCE_END = (".", "!", "?", "…", ";")


def _segment_parts(seg: Dict[str, Any]) -> List[tuple]:
    """(start, text) pieces of a segment: at sentence ends after LINE_SPLIT_SEC, at the latest after
    LINE_MAX_SEC. Segments without word timings or short ones stay whole."""
    words = [w for w in seg.get("words") or [] if str(w.get("word") or "").strip()]
    start = float(seg.get("start", 0.0) or 0.0)
    end = float(seg.get("end", start) or start)
    text = (seg.get("text") or "").strip()
    if not words or end - start <= LINE_SPLIT_SEC:
        return [(start, text)]
    parts, cur, cur_start = [], [], None
    for w in words:
        if cur_start is None:
            cur_start = float(w.get("start", start) or start)
        cur.append(str(w.get("word")))
        span = float(w.get("end", cur_start) or cur_start) - cur_start
        word = str(w.get("word")).strip()
        if (span >= LINE_SPLIT_SEC and word.endswith(_SENTENCE_END)) or span >= LINE_MAX_SEC:
            parts.append((cur_start, "".join(cur).strip()))
            cur, cur_start = [], None
    if cur:
        parts.append((cur_start if cur_start is not None else start, "".join(cur).strip()))
    # Word timings follow the original recognition: after an edit the words may no longer match
    joined = " ".join(p[1] for p in parts)
    if _normalize(joined) != _normalize(text):
        return [(start, text)]
    return parts


def _normalize(text: str) -> str:
    return re.sub(r"\W+", "", (text or "").casefold())


def build_transcript_lines(
    segments: List[Dict[str, Any]],
    speakers: Dict[str, Dict[str, Any]],
) -> List[str]:
    lines = []
    for seg in segments:
        spk_id = seg.get("speaker", "SPEAKER_00")
        spk_name = speakers.get(spk_id, {}).get("name", spk_id)
        for i, (start, text) in enumerate(_segment_parts(seg)):
            ts = format_timestamp(start)
            # The speaker is named once per segment; its continuation lines only carry the time
            lines.append(f"[{ts}] {spk_name}: {text}" if i == 0 else f"[{ts}] {text}")
    return lines


def _chunk_lines(lines: List[str], max_chars: int) -> List[str]:
    chunks: List[str] = []
    current: List[str] = []
    size = 0
    for line in lines:
        if current and size + len(line) + 1 > max_chars:
            chunks.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


# A transcript up to this size is analysed in one request (fits num_ctx together with the prompt).
# Longer recordings are summarised chunk by chunk first, so nothing in the middle is dropped.
SINGLE_PASS_MAX_CHARS = 20000
CHUNK_MAX_CHARS = 14000
LLM_NUM_CTX = 16384

_LANG_PHRASES: Dict[str, str] = {
    "ru": "на русском языке",
    "en": "на английском языке",
    "uk": "на украинском языке",
    "be": "на белорусском языке",
    "kk": "на казахском языке",
    "de": "на немецком языке",
    "fr": "на французском языке",
    "es": "на испанском языке",
    "it": "на итальянском языке",
    "pt": "на португальском языке",
    "zh": "на китайском языке",
    "ja": "на японском языке",
}


def _lang_phrase(code: Optional[str]) -> str:
    key = (code or "").strip().lower()[:2]
    return _LANG_PHRASES.get(key, "на том же языке, на котором ведётся запись")


SYSTEM_GROUNDING_PROMPT = (
    "Ты — аккуратный ассистент для анализа расшифровок аудио- и видеозаписей в программе VerbaScribe. "
    "Анализируй ТОЛЬКО реальный текст предоставленной расшифровки.\n"
    "ПРАВИЛА:\n"
    "1. Опирайся только на факты, фразы, предметы и имена, которые явно есть в тексте расшифровки.\n"
    "2. Не выдумывай имена людей, встречи, проекты, решения или задачи, которых нет в исходном тексте.\n"
    "3. Запись может быть любого жанра: интервью, монолог, обзор, лекция, подкаст, бытовой разговор, видеоролик или беседа. "
    "Описывай ровно то, о чём реально говорят спикеры.\n"
    "4. Не повторяй одну и ту же фразу несколько раз. Пиши грамотно {lang} в формате Markdown без эмодзи.\n"
    "5. Не оформляй цитаты и ответы блоками кода (```): цитируй обычным текстом или через «>». "
    "Таймкоды пиши в квадратных скобках: [ММ:СС] или диапазоном [ММ:СС–ММ:СС]."
)

SUMMARY_TEMPLATES: Dict[str, str] = {
    "protocol": (
        "Составь точный структурированный обзор приведённой ниже расшифровки записи {lang} в формате Markdown (без эмодзи).\n\n"
        "Используй следующую структуру (заголовки тоже пиши на языке ответа):\n"
        "## Краткое содержание\n"
        "(2–3 предложения о том, кто говорит и о чём конкретно идёт речь в записи — строго по тексту расшифровки)\n\n"
        "## Основные темы и высказывания (с таймкодами)\n"
        "(Перечисли по пунктам с таймкодами `[ММ:СС]`, о чём именно говорили спикеры и какие оценки/мнения они высказали)\n\n"
        "## Упомянутые имена, объекты и детали\n"
        "(Какие имена, предметы, места или конкретные факты прозвучали в речи)\n\n"
        "## Итог\n"
        "(Краткое резюме сказанного в 1–2 предложениях строго по тексту)"
    ),
    "tasks": (
        "Проанализируй приведённую ниже расшифровку и выпиши {lang} только те задачи, планы или действия, которые реально упоминаются в тексте.\n\n"
        "ВАЖНО: Если в записи нет никаких рабочих задач, поручений или дедлайнов (например, это рассказ, интервью, обзор или бытовая беседа), "
        "НЕ выдумывай их! В таком случае прямо напиши, что в записи не ставились задачи или поручения, и ниже кратко перечисли, "
        "какие действия или события описывали говорящие в своих репликах (с таймкодами `[ММ:СС]`)."
    ),
    "tldr": (
        "Сделай короткую и точную выжимку (TL;DR) этой записи {lang} строго по фактам из текста (без выдумывания и без эмодзи):\n"
        "- **О чём запись**: 1–2 предложения.\n"
        "- **Ключевые моменты**: 2–5 коротких пунктов с таймкодами `[ММ:СС]` о том, что именно было сказано."
    ),
    # Parsed by the app into a clickable table of contents: keep the line format strict
    "chapters": (
        "Раздели запись на смысловые главы по порядку: от 3 до 12 глав, по одной на каждую смену темы. "
        "Ответь {lang}. Каждая глава — ровно одна строка строго в формате:\n"
        "- [ММ:СС] Название главы — одно короткое предложение о её содержании\n"
        "Время — момент начала главы, взятый из таймкодов расшифровки (для записей длиннее часа — [Ч:ММ:СС]). "
        "Первая глава начинается с [00:00]. Не пиши ничего, кроме этих строк: без заголовков, вступления и итогов."
    ),
}


def _ollama_chat(base_url: str, model: str, system: str, user: str) -> str:
    from app import idle_unload

    idle_unload.touch(model)
    payload: Dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "options": {
            "temperature": 0.1,
            "top_p": 0.85,
            "repeat_penalty": 1.15,
            "num_ctx": LLM_NUM_CTX,
        },
    }
    keep_alive = idle_unload.ollama_keep_alive()
    if keep_alive:
        payload["keep_alive"] = keep_alive  # Ollama frees the model after the same idle time
    with httpx.Client(timeout=600.0) as client:
        resp = client.post(f"{base_url}/api/chat", json=payload)
        resp.raise_for_status()
        data = resp.json()
        msg = data.get("message") or {}
        return str(msg.get("content") or data.get("response") or "").strip()


def generate_meeting_summary(
    segments: List[Dict[str, Any]],
    speakers: Dict[str, Dict[str, Any]],
    template_key: str = "protocol",
    custom_prompt: str = "",
    model_name: Optional[str] = None,
    base_url: Optional[str] = None,
    answer_lang: Optional[str] = "ru",
    local_only: bool = False,
) -> str:
    from app.llm import target

    # Ollama, or the cloud model chosen in Settings → Analysis ("cloud" asks for it explicitly)
    llm = target(model_name, local_only=local_only)
    lines = build_transcript_lines(segments, speakers)
    transcript_text = "\n".join(lines)
    if not transcript_text.strip():
        raise RuntimeError("Транскрипт пуст, нечего анализировать.")

    lang = _lang_phrase(answer_lang)
    system_prompt = SYSTEM_GROUNDING_PROMPT.format(lang=lang)

    if custom_prompt.strip():
        instruction = (
            "Ответь на вопрос или выполни задание пользователя СТРОГО на основе приведённой ниже "
            "расшифровки записи. Ничего не додумывай от себя. Указывай имена спикеров и таймкоды `[ММ:СС]`, "
            f"где это уместно. Отвечай {lang} в формате Markdown без эмодзи.\n\n"
            f"Вопрос / задание пользователя: {custom_prompt.strip()}"
        )
    else:
        instruction = SUMMARY_TEMPLATES.get(template_key, SUMMARY_TEMPLATES["protocol"]).format(lang=lang)

    source_label = "Вот точная расшифровка аудио/видеозаписи"
    single_pass_max = SINGLE_PASS_MAX_CHARS if not llm.is_cloud else llm.max_chars
    if len(transcript_text) > single_pass_max:
        # Map step: condense each part of a long recording into detailed timestamped notes
        chunks = _chunk_lines(lines, CHUNK_MAX_CHARS if not llm.is_cloud else llm.max_chars // 2)
        focus = (
            f"Особенно подробно выпиши всё, что относится к вопросу: {custom_prompt.strip()}\n"
            if custom_prompt.strip()
            else ""
        )
        notes: List[str] = []
        for idx, chunk in enumerate(chunks, start=1):
            notes.append(
                llm.chat(
                    system_prompt,
                    f"Это фрагмент {idx} из {len(chunks)} расшифровки длинной записи:\n```text\n{chunk}\n```\n\n"
                    f"Составь {lang} подробный конспект этого фрагмента списком: кто что сказал, факты, мнения, "
                    f"решения, задачи и сроки — каждый пункт с таймкодом `[ММ:СС]`. {focus}"
                    "Пиши только то, что есть в тексте фрагмента.",
                )
            )
        transcript_text = "\n\n".join(f"### Фрагмент {i}\n{n}" for i, n in enumerate(notes, start=1))
        source_label = (
            "Запись длинная, поэтому ниже приведены последовательные конспекты всех её фрагментов "
            "(с таймкодами исходной записи)"
        )

    user_message = (
        f"{source_label}:\n"
        f"```text\n{transcript_text}\n```\n\n"
        f"Задание:\n{instruction}\n\n"
        "Напоминание: пиши ТОЛЬКО о том, что реально сказано в записи. "
        "Не выдумывай посторонних людей, встреч или проектов."
    )
    return llm.chat(system_prompt, user_message)


# --- A title for a recording, from its content ---
_GENERIC_TITLE_WORDS = {
    "запись", "записи", "аудио", "видео", "звук", "встреча", "созвон", "голосовая", "заметка", "компьютера",
    "новая", "новый", "без", "названия", "микрофон", "экран",
    "recording", "record", "rec", "audio", "video", "vid", "voice", "note", "memo", "meeting", "call", "zoom",
    "teams", "screen", "screencast", "capture", "computer", "new", "untitled", "track", "mic", "system",
    "img", "mov", "dsc", "gmt", "file", "clip", "output", "export", "final", "copy", "копия",
}


def is_generic_title(title: str) -> bool:
    """True for titles that say nothing about the content: 'Запись 14-05', 'zoom_0', 'VID_20240512_1302',
    'Recording (3)'. Such titles may be replaced by one made from the transcript."""
    words = re.findall(r"[^\W\d_]+", (title or "").casefold())
    return all(w in _GENERIC_TITLE_WORDS or len(w) <= 1 for w in words)


def generate_meeting_title(
    segments: List[Dict[str, Any]],
    speakers: Dict[str, Dict[str, Any]],
    answer_lang: Optional[str] = "ru",
    model_name: Optional[str] = None,
    local_only: bool = False,
) -> str:
    """A short title (3-8 words) for the recording from the beginning of its transcript."""
    from app.llm import target

    llm = target(model_name, start_ollama=False, local_only=local_only)
    text = "\n".join(build_transcript_lines(segments, speakers))[:7000]
    if not text.strip():
        raise RuntimeError("Транскрипт пуст")
    lang = _lang_phrase(answer_lang)
    system = (
        "Ты придумываешь короткие точные названия для расшифровок встреч, звонков и записей. "
        f"Отвечай {lang}. Выведи только название: 3–8 слов, без кавычек, без точки в конце, без даты, "
        "без слов «Встреча», «Запись», «Обсуждение» в начале, если можно обойтись без них."
    )
    raw = llm.chat(system, f"Расшифровка:\n{text}\n\nНазвание:")
    return clean_title(raw)


def clean_title(raw: str) -> str:
    line = next((ln.strip() for ln in (raw or "").splitlines() if ln.strip()), "")
    line = re.sub(r"^(название|title)\s*[:：-]\s*", "", line, flags=re.IGNORECASE)
    prev = None
    while line != prev:  # «Title». or "Title." in any order
        prev = line
        line = re.sub(r"[.。]+$", "", line.strip().strip("\"'«»“”`*#")).strip()
    return line[:120]
