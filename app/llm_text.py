"""
Qwen3 and other "thinking" models: Ollama is asked not to think (`think: false`, Ollama 0.9+), and
any <think>…</think> block that still comes back is removed, so answers, titles and translations
never contain the model's reasoning.
"""

import re
from typing import Any, Dict, Optional

_THINK_RE = re.compile(r"<think>.*?(</think>|$)", re.S | re.I)
_THINKING_FAMILIES = ("qwen3", "deepseek-r1", "qwq")


def thinking_off(model: Optional[str]) -> Dict[str, Any]:
    name = (model or "").lower()
    return {"think": False} if name.startswith(_THINKING_FAMILIES) else {}


def strip_think(text: str) -> str:
    return _THINK_RE.sub("", text or "").strip()
