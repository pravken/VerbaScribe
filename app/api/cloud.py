"""Cloud language models for analysis (app/llm.py) and opening links."""

import os
import sys
from typing import Any, Dict
from fastapi import HTTPException
from pydantic import BaseModel

from fastapi import APIRouter


router = APIRouter()

# --- Cloud language models for analysis (app/llm.py) ---
@router.get("/api/llm/status")
def api_llm_status() -> Dict[str, Any]:
    from app.llm import status

    return status()

class LlmKeyPayload(BaseModel):
    provider: str
    key: str

@router.post("/api/llm/key")
def api_llm_set_key(payload: LlmKeyPayload) -> Dict[str, Any]:
    """Stores an API key (encrypted with DPAPI on Windows); it is never sent back to the page.
    The models the key can use are fetched at once; an unavailable chosen model is replaced."""
    from app import secret_store
    from app.llm import PROVIDERS, ensure_model, key_name, status

    if payload.provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail="Неизвестный сервис")
    key = payload.key.strip()
    if not key or len(key) > 500 or any(c.isspace() for c in key):
        raise HTTPException(status_code=400, detail="Ключ выглядит неверно")
    secret_store.set_secret(key_name(payload.provider), key)
    picked = ensure_model(payload.provider)
    return {**status(), "models": picked["models"], "model_changed": picked["changed"], "model": picked["model"]}

@router.delete("/api/llm/key/{provider}")
def api_llm_delete_key(provider: str) -> Dict[str, Any]:
    from app import secret_store
    from app.llm import PROVIDERS, key_name, status

    if provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail="Неизвестный сервис")
    secret_store.delete_secret(key_name(provider))
    return status()

@router.get("/api/llm/models/{provider}")
def api_llm_models(provider: str) -> Dict[str, Any]:
    from app.llm import PROVIDERS, list_models, pick_default_model

    if provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail="Неизвестный сервис")
    models = list_models(provider)
    return {"models": models, "recommended": pick_default_model(provider, models)}

class OpenUrlPayload(BaseModel):
    url: str

@router.post("/api/open-url")
def api_open_url(payload: OpenUrlPayload) -> Dict[str, Any]:
    """Opens a web link in the default browser (the app window itself never navigates away)."""
    import webbrowser
    from urllib.parse import urlparse

    u = urlparse(payload.url.strip())
    if u.scheme == "mailto":  # a draft in the mail app (no address: the user picks the recipients)
        if len(payload.url) > 8000:
            raise HTTPException(status_code=400, detail="Слишком длинное письмо")
        if sys.platform == "win32":
            os.startfile(payload.url.strip())  # noqa: S606 - a mailto: link the user asked to open
        else:
            webbrowser.open(payload.url.strip())
        return {"ok": True}
    if u.scheme not in ("https", "http") or not u.netloc:
        raise HTTPException(status_code=400, detail="Можно открыть только веб-ссылку")
    webbrowser.open(u.geturl())
    return {"ok": True}

@router.post("/api/llm/test/{provider}")
def api_llm_test(provider: str) -> Dict[str, Any]:
    """Sends a two-word request to check the key, the address and the model."""
    from app.llm import PROVIDERS, LlmError, test_connection

    if provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail="Неизвестный сервис")
    try:
        return test_connection(provider)
    except LlmError as e:
        raise HTTPException(status_code=502, detail=str(e))
