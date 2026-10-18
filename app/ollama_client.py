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
        "name": "Qwen 2.5 (7B) · Apache 2.0",
        "size": "4.7 GB",
        "vram": "8+ GB VRAM",
        "license": "Apache 2.0",
        "desc_ru": "Оптимальный выбор ★ Отличный русский и английский, 100% свободная коммерческая лицензия",
        "desc_en": "Recommended ★ Excellent RU & EN quality, 100% free commercial Apache 2.0 license",
    },
    {
        "id": "qwen2.5:14b",
        "name": "Qwen 2.5 (14B) · Apache 2.0",
        "size": "9.0 GB",
        "vram": "12–24 GB VRAM",
        "license": "Apache 2.0",
        "desc_ru": "Максимальный интеллект и глубина протоколов (100% свободная лицензия Apache 2.0)",
        "desc_en": "Maximum reasoning & detailed protocols (100% free commercial Apache 2.0 license)",
    },
    {
        "id": "mistral:7b",
        "name": "Mistral v0.3 (7B) · Apache 2.0",
        "size": "4.1 GB",
        "vram": "6–8 GB VRAM",
        "license": "Apache 2.0",
        "desc_ru": "Европейская открытая модель от Mistral AI (100% свободная лицензия Apache 2.0)",
        "desc_en": "Open-weights model by Mistral AI (100% free commercial Apache 2.0 license)",
    },
    {
        "id": "qwen2.5:1.5b",
        "name": "Qwen 2.5 (1.5B) · Apache 2.0",
        "size": "1.0 GB",
        "vram": "2–4 GB / CPU",
        "license": "Apache 2.0",
        "desc_ru": "Сверхлёгкая и быстрая модель для слабых ПК и процессоров (лицензия Apache 2.0)",
        "desc_en": "Ultra-lightweight model for low-VRAM GPUs and CPUs (100% free Apache 2.0 license)",
    },
]

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


def ensure_ollama_running(base_url: str = "http://localhost:11434") -> bool:
    try:
        with httpx.Client(timeout=1.8) as client:
            resp = client.get(f"{base_url.rstrip('/')}/api/tags")
            if resp.status_code == 200:
                return True
    except Exception:
        pass

    exe = find_ollama_executable()
    if exe:
        try:
            subprocess.Popen(
                [exe, "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
            )
            for _ in range(10):
                time.sleep(0.5)
                try:
                    with httpx.Client(timeout=1.5) as client:
                        if client.get(f"{base_url.rstrip('/')}/api/tags").status_code == 200:
                            return True
                except Exception:
                    continue
        except Exception:
            pass
    return False


def get_ollama_status(base_url: Optional[str] = None) -> Dict[str, Any]:
    settings = get_all_settings()
    if not base_url:
        base_url = settings.get("ollama_url", "http://localhost:11434")
    active_model = settings.get("ollama_model", "qwen2.5:7b")

    running = ensure_ollama_running(base_url)
    models: List[str] = []
    if running:
        try:
            with httpx.Client(timeout=2.5) as client:
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
                    "name": m,
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

    _append_log("Сервер Ollama не найден на ПК. Запускаю автоматическую установку Ollama через winget...")
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
        base_url = get_all_settings().get("ollama_url", "http://localhost:11434")

    with _pull_lock:
        if _pull_state["active"]:
            return dict(_pull_state)
        _pull_state.update(
            {
                "active": True,
                "model": model_name,
                "status": f"Подготовка к загрузке модели {model_name}...",
                "progress": 1,
                "downloaded_mb": 0.0,
                "total_mb": 0.0,
                "error": "",
                "logs": [],
            }
        )
        _append_log(f"Запрошена установка модели: {model_name}")

    def _worker():
        try:
            # 1. Check / Install Ollama if not present
            if not ensure_ollama_running(base_url):
                installed_ok = _install_ollama_if_needed()
                if not installed_ok:
                    raise RuntimeError(
                        "Не удалось автоматически установить Ollama. Скачайте установщик с https://ollama.com/download"
                    )
                _append_log("Запуск фоновой службы Ollama...")
                if not ensure_ollama_running(base_url):
                    raise RuntimeError("Ollama установлена, но сервер http://localhost:11434 ещё не ответил. Попробуйте через 5 секунд.")

            _append_log(f"Подключение к локальному серверу Ollama ({base_url})...")
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

                        status_txt = data.get("status", "Загрузка...")
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
                                    "status": f"{status_txt}: {dl_mb} МБ из {tot_mb} МБ ({pct}%)",
                                }
                            )
                            if pct - last_logged_pct >= 10:
                                last_logged_pct = pct
                                _append_log(f"Скачивание {model_name}: {dl_mb} / {tot_mb} МБ ({pct}%)")
                        else:
                            _pull_state["status"] = status_txt
                            if status_txt != last_logged_status:
                                last_logged_status = status_txt
                                _append_log(f"Ollama: {status_txt}")

            # Automatically set this newly downloaded model as the active LLM model!
            update_settings({"ollama_model": model_name})
            _append_log(f"Готово! Модель {model_name} успешно скачана и выбрана как активная.")
            _pull_state.update(
                {
                    "active": False,
                    "status": f"Модель {model_name} установлена и активна!",
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


SUMMARY_TEMPLATES: Dict[str, str] = {
    "protocol": (
        "Ты — профессиональный секретарь и аналитик рабочих встреч. "
        "Проанализируй приведённую ниже расшифровку встречи и составь подробный, хорошо структурированный "
        "протокол на русском языке в формате Markdown.\n\n"
        "Обязательные разделы:\n"
        "## 📌 Краткая суть встречи (Executive Summary)\n"
        "(2–4 предложения о главной цели и итоге созвона)\n\n"
        "## 🗣 Ключевые обсуждаемые темы (с таймкодами)\n"
        "(Основные блоки обсуждения, позиции участников и важные детали с указанием таймкодов вида `[ММ:СС]`)\n\n"
        "## ✅ Принятые решения и договорённости\n"
        "(Чёткий список того, о чём конкретно договорились участники)\n\n"
        "## 📋 Задачи и поручения (Action Items)\n"
        "(Чек-лист `- [ ] **Имя участника**: Задача (срок/контекст, если упоминался)`)\n\n"
        "## ❓ Открытые вопросы и риски\n"
        "(Что осталось нерешённым или требует дополнительного обсуждения)"
    ),
    "tasks": (
        "Ты — проектный менеджер. Внимательно изучи расшифровку встречи и извлеки ВСЕ задачи, "
        "обещания, поручения и следующие шаги на русском языке в формате Markdown.\n\n"
        "Сгруппируй ответ так:\n"
        "## 📋 Задачи по участникам\n"
        "Для каждого участника перечисли его задачи в виде чек-листа `- [ ] Задача [таймкод]`.\n\n"
        "## ⏰ Упомянутые сроки и дедлайны\n"
        "Все даты, сроки и контрольные точки, которые прозвучали на встрече.\n\n"
        "## 🔄 Что нужно проконтролировать к следующей встрече"
    ),
    "tldr": (
        "Сделай максимально ёмкую и понятную выжимку (TL;DR) этой встречи на русском языке "
        "(на 30 секунд чтения) в формате Markdown:\n"
        "1. **Главная тема**: одно предложение.\n"
        "2. **Главные тезисы (до 5 пунктов)**: только самое важное без воды.\n"
        "3. **Итог / Что делаем дальше**: 1–3 главных шага."
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
    base_url = (base_url or settings.get("ollama_url") or "http://localhost:11434").rstrip("/")
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
        raise RuntimeError("Транскрипт пуст, нечего суммаризировать.")

    if custom_prompt.strip():
        instruction = (
            "Ответь на вопрос или выполни задание пользователя строго на основе приведённой ниже "
            "расшифровки встречи. Указывай имена спикеров и таймкоды `[ММ:СС]`, где это уместно. "
            "Отвечай на русском языке в формате Markdown.\n\n"
            f"Задание пользователя: {custom_prompt.strip()}"
        )
    else:
        instruction = SUMMARY_TEMPLATES.get(template_key, SUMMARY_TEMPLATES["protocol"])

    full_prompt = f"{instruction}\n\n---\n### РАСШИФРОВКА ВСТРЕЧИ:\n{transcript_text}\n---\n"

    with httpx.Client(timeout=300.0) as client:
        resp = client.post(
            f"{base_url}/api/generate",
            json={
                "model": selected_model,
                "prompt": full_prompt,
                "stream": False,
                "options": {
                    "temperature": 0.25,
                    "num_ctx": 16384,
                },
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("response", "").strip()
