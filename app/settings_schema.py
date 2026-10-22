"""
What a setting may hold, checked when the page (or anything with the session) saves settings.

A setting decides where transcripts are sent (Ollama, an OpenAI-compatible service), what the app
reads (the calendar) and where it writes (recordings, exports, notes): those take only addresses
and folders of the right kind. Numbers and switches are checked so a bad value cannot stop the
recording or the queue later. Other keys of the page are kept as text of a reasonable size.
"""

import ipaddress
import re
from pathlib import Path, PureWindowsPath
from typing import Any, Dict
from urllib.parse import urlparse

KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
MAX_VALUE_CHARS = 256 * 1024  # summary templates: up to 30 prompts of 4000 characters

BOOLS = {
    "diarization_enabled", "auto_summary", "vad_filter", "audio_enhance", "clean_fillers", "batched_inference",
    "keep_model_loaded", "onnx_gpu", "onnx_gpu_vad", "voice_profiles", "update_check", "quiet_mode",
    "call_auto_record", "semantic_search", "auto_title", "obsidian_auto", "summary_grounding", "auto_commitments",
    "delete_media_after", "update_auto", "obsidian_summary", "obsidian_tasks", "obsidian_chapters",
    "obsidian_transcript", "obsidian_generate", "llm_cloud_consent", "watch_folder_recursive",
    "dub_keep_original_track", "dub_denoise", "dub_room_tone", "dub_keep_workdir",
}
INTS = {"num_speakers": (0, 20), "idle_unload_minutes": (0, 24 * 60), "media_retention_days": (0, 3650),
        "onnx_gpu_batch": (1, 256)}
FLOATS = {"dub_cfg_weight": (0.0, 2.0), "dub_exaggeration": (0.0, 2.0)}
CHOICES = {"llm_provider": {"ollama", "cloud"}, "date_folders": {"off", "day", "month", "year_month"},
           "record_source_mode": {"mic", "system", "both"}}
FOLDERS = {"recordings_dir", "export_dir", "obsidian_vault", "watch_folder"}


def _is_local_host(host: str) -> bool:
    if host in ("localhost",):
        return True
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private or ip.is_link_local


def _url(value: str, key: str, plain_http: str) -> str:
    """plain_http: "any" (Ollama on another computer of the network), "local" (http only to this
    computer or the local network: an API key must not travel in clear text over the internet)."""
    u = urlparse(value)
    if u.scheme not in ("http", "https") or not u.hostname or u.username or u.password:
        raise ValueError(f"{key}: нужен адрес вида http(s)://сервер[:порт]")
    if u.scheme == "http" and plain_http == "local" and not _is_local_host(u.hostname):
        raise ValueError(f"{key}: адрес в интернете должен начинаться с https://")
    return value


def _folder(value: str, key: str) -> str:
    if not (Path(value).is_absolute() or PureWindowsPath(value).is_absolute()):
        raise ValueError(f"{key}: нужен полный путь к папке")
    return value


def _calendar(value: str) -> str:
    if re.match(r"^(https?|webcal)://", value, re.I):
        _url(re.sub(r"^webcal://", "https://", value, flags=re.I), "calendar_ics", "any")
        return value
    if value.lower() == "outlook":
        return value
    p = Path(value)
    if p.suffix.lower() != ".ics" or not p.is_file():
        raise ValueError("calendar_ics: нужна ссылка на календарь или файл .ics")
    return value


def clean(key: str, value: Any) -> str:
    """The value to store, or ValueError with a message for the user."""
    if not KEY_RE.match(str(key)):
        raise ValueError(f"Неизвестная настройка: {str(key)[:40]}")
    if isinstance(value, bool):
        text = "true" if value else "false"
    elif value is None:
        text = ""
    else:
        text = str(value)
    if len(text) > MAX_VALUE_CHARS:
        raise ValueError(f"{key}: слишком длинное значение")
    text_s = text.strip()
    if key in BOOLS:
        if text_s.lower() not in ("true", "false"):
            raise ValueError(f"{key}: нужно true или false")
        return text_s.lower()
    if key in INTS:
        lo, hi = INTS[key]
        try:
            n = int(float(text_s or 0))
        except ValueError:
            raise ValueError(f"{key}: нужно число")
        return str(min(hi, max(lo, n)))
    if key in FLOATS:
        lo, hi = FLOATS[key]
        try:
            x = float(text_s)
        except ValueError:
            raise ValueError(f"{key}: нужно число")
        return str(min(hi, max(lo, x)))
    if key in CHOICES:
        if text_s not in CHOICES[key]:
            raise ValueError(f"{key}: недопустимое значение")
        return text_s
    if key == "ollama_url":
        return _url(text_s, key, "any") if text_s else ""
    if key.startswith("llm_base_url_"):
        return _url(text_s, key, "local") if text_s else ""
    if key == "calendar_ics":
        return _calendar(text_s) if text_s else ""
    if key in FOLDERS:
        return _folder(text_s, key) if text_s else ""
    return text


def as_int(value: Any, default: int = 0) -> int:
    """A stored number that may be anything (a database from an old version or a backup)."""
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def clean_all(settings: Dict[str, Any]) -> Dict[str, str]:
    return {str(k): clean(str(k), v) for k, v in settings.items()}
