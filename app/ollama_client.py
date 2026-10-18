import json
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
    "qwen2.5:3b": "Qwen 3B Compact",
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
    # Do not block trying to auto-start if Ollama is not installed or currently installing
    running = ensure_ollama_running(base_url, auto_start=(exe is not None and not _pull_state["active"]))
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
        "installed": find_ollama_executable() is not None or running,
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


def start_model_pull(model_name: str, base_url: Optional[str] = None) -> Dict[str, Any]:
    """
    Starts downloading an Ollama model (and installs Ollama first if missing)
    in a background thread with detailed real-time logs and MB/GB progress.
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


def build_transcript_text_for_llm(
    segments: List[Dict[str, Any]],
    speakers: Dict[str, Dict[str, Any]],
    max_chars: int = 48000,
) -> str:
    lines = []
    for seg in segments:
        spk_id = seg.get("speaker", "SPEAKER_00")
        spk_name = speakers.get(spk_id, {}).get("name", spk_id)
        ts = format_timestamp(seg.get("start", 0.0))
        lines.append(f"[{ts}] {spk_name}: {seg.get('text', '').strip()}")

    full_text = "\n".join(lines)
    if len(full_text) > max_chars:
        half = max_chars // 2
        full_text = (
            full_text[:half]
            + "\n\n... [часть середины длинной встречи сокращена для контекстного окна] ...\n\n"
            + full_text[-half:]
        )
    return full_text


SYSTEM_GROUNDING_PROMPT = (
    "Ты — точный ИИ-аналитик аудио- и видеозаписей в программе VerbaScribe. "
    "Твоя главная задача — анализировать ТОЛЬКО реальный текст предоставленной расшифровки.\n"
    "СТРОГИЕ ПРАВИЛА:\n"
    "1. Опирайся ИСКЛЮЧИТЕЛЬНО на факты, фразы, предметы и имена, которые явно присутствуют в тексте расшифровки.\n"
    "2. КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО выдумывать имена людей, корпоративные встречи, проекты, планы на месяц, "
    "решения или задачи, которых нет в исходном тексте.\n"
    "3. Запись может быть любого жанра: интервью, монолог, обзор, лекция, подкаст, бытовой разговор, видеоролик или беседа. "
    "Описывай ровно то, о чём реально говорят спикеры (даже если речь идёт о еде, бытовых вещах, впечатлениях или юморе).\n"
    "4. Не повторяй одну и ту же фразу несколько раз. Пиши грамотно на русском языке в формате Markdown без эмодзи."
)

SUMMARY_TEMPLATES: Dict[str, str] = {
    "protocol": (
        "Составь точный структурированный обзор приведённой ниже расшифровки записи на русском языке в формате Markdown (без эмодзи).\n\n"
        "Используй следующую структуру:\n"
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
        "Проанализируй приведённую ниже расшифровку и выпиши только те задачи, планы или действия, которые реально упоминаются в тексте.\n\n"
        "ВАЖНО: Если в записи нет никаких рабочих задач, поручений или дедлайнов (например, это рассказ, интервью, обзор или бытовая беседа), "
        "НЕ выдумывай их! В таком случае прямо напиши: «В данной записи не ставились задачи или поручения», и ниже кратко перечисли, "
        "какие действия или события описывали говорящие в своих репликах (с таймкодами `[ММ:СС]`)."
    ),
    "tldr": (
        "Сделай короткую и точную выжимку (TL;DR) этой записи на русском языке строго по фактам из текста (без выдумывания и без эмодзи):\n"
        "- **О чём запись**: 1–2 предложения.\n"
        "- **Ключевые моменты**: 2–5 коротких пунктов с таймкодами `[ММ:СС]` о том, что именно было сказано."
    ),
}


def generate_meeting_summary(
    segments: List[Dict[str, Any]],
    speakers: Dict[str, Dict[str, Any]],
    template_key: str = "protocol",
    custom_prompt: str = "",
    model_name: Optional[str] = None,
    base_url: Optional[str] = None,
) -> str:
    settings = get_all_settings()
    base_url = (base_url or settings.get("ollama_url") or "http://127.0.0.1:11434").replace("localhost", "127.0.0.1").rstrip("/")
    model_name = model_name or settings.get("ollama_model") or "qwen2.5:7b"

    status = get_ollama_status(base_url)
    if not status["running"]:
        raise RuntimeError(
            "Локальный сервер Ollama ещё не установлен или не запущен. Откройте «Настройки» -> выберите модель LLM и нажмите «Скачать»."
        )

    available_models = status["models"]
    if not available_models:
        raise RuntimeError(
            f"В Ollama пока не скачана ни одна модель. Откройте «Настройки» и нажмите кнопку «Скачать» напротив {model_name}."
        )

    selected_model = model_name
    if selected_model not in available_models:
        matches = [m for m in available_models if m.startswith(selected_model.split(":")[0])]
        selected_model = matches[0] if matches else available_models[0]

    transcript_text = build_transcript_text_for_llm(segments, speakers)
    if not transcript_text.strip():
        raise RuntimeError("Транскрипт пуст, нечего анализировать.")

    if custom_prompt.strip():
        instruction = (
            "Ответь на вопрос или выполни задание пользователя СТРОГО на основе приведённой ниже "
            "расшифровки записи. Ничего не додумывай от себя. Указывай имена спикеров и таймкоды `[ММ:СС]`, "
            "где это уместно. Отвечай на русском языке в формате Markdown без эмодзи.\n\n"
            f"Вопрос / задание пользователя: {custom_prompt.strip()}"
        )
    else:
        instruction = SUMMARY_TEMPLATES.get(template_key, SUMMARY_TEMPLATES["protocol"])

    user_message = (
        f"Вот точная расшифровка аудио/видеозаписи:\n"
        f"```text\n{transcript_text}\n```\n\n"
        f"Задание:\n{instruction}\n\n"
        f"Напоминание: пиши ТОЛЬКО о том, что реально сказано в приведённом выше тексте расшифровки. "
        f"Не выдумывай никаких посторонних людей, встреч или проектов."
    )

    with httpx.Client(timeout=300.0) as client:
        resp = client.post(
            f"{base_url}/api/chat",
            json={
                "model": selected_model,
                "messages": [
                    {"role": "system", "content": SYSTEM_GROUNDING_PROMPT},
                    {"role": "user", "content": user_message},
                ],
                "stream": False,
                "options": {
                    "temperature": 0.1,
                    "top_p": 0.85,
                    "repeat_penalty": 1.15,
                    "num_ctx": 16384,
                },
            },
        )
        resp.raise_for_status()
        data = resp.json()
        msg = data.get("message") or {}
        return str(msg.get("content") or data.get("response") or "").strip()
