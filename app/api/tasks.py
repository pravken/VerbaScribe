"""The Tasks page: the tasks of every recording (app/commitments.py), reassigned, moved and added by
hand, saved for other apps, and the summary of a period."""

from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.database import get_meeting, is_local_only

router = APIRouter()


def _pick_export_path(*args: Any, **kwargs: Any) -> Any:
    # imported when called: app.main imports this router, so a module-level import would be circular
    from app.main import _pick_export_path as pick

    return pick(*args, **kwargs)


def _meetings(tasks: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Title, date and the people who speak, of each recording the tasks come from (the assign menu
    offers them first)."""
    from app.commitments import participants

    out: Dict[str, Dict[str, Any]] = {}
    for mid in sorted({t["meeting_id"] for t in tasks if t["meeting_id"]}):
        m = get_meeting(mid) or {}
        out[str(mid)] = {"title": m.get("title") or "", "date": str(m.get("recorded_at") or m.get("created_at") or "")[:10],
                         "participants": participants(m)}
    return out


@router.get("/api/tasks")
def api_tasks() -> Dict[str, Any]:
    from app import commitments

    tasks = commitments.all_tasks()
    return {"tasks": tasks, "people": commitments.people(tasks), "meetings": _meetings(tasks),
            "today": date.today().isoformat()}


class TaskPayload(BaseModel):
    what: str
    who: str = ""
    who_kind: str = ""
    due_date: str = ""
    meeting_id: int = 0
    at: Optional[float] = None


@router.post("/api/tasks")
def api_create_task(payload: TaskPayload) -> Dict[str, Any]:
    from app import commitments

    try:
        return commitments.create(payload.what, payload.who, payload.who_kind, payload.due_date, payload.meeting_id, payload.at)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class TaskChangePayload(BaseModel):
    who: Optional[str] = None
    who_kind: Optional[str] = None
    due_date: Optional[str] = None  # "" takes the due date off
    what: Optional[str] = None
    status: Optional[str] = None


@router.patch("/api/tasks/{task_id}")
def api_change_task(task_id: int, payload: TaskChangePayload) -> Dict[str, Any]:
    from app import commitments

    try:
        return commitments.update(task_id, payload.model_dump(exclude_none=True))
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/api/tasks/{task_id}")
def api_delete_task(task_id: int) -> Dict[str, Any]:
    from app import commitments

    if not commitments.delete(task_id):
        raise HTTPException(status_code=404, detail="Задача не найдена")
    return {"ok": True}


class GuessPayload(BaseModel):
    text: str


@router.post("/api/tasks/guess")
def api_guess_task(payload: GuessPayload) -> Dict[str, Any]:
    """Who and when the words of a new task name, from the people tasks already have."""
    from app import commitments

    ppl = commitments.people()
    found = commitments.guess(payload.text, [p["name"] for p in ppl])
    kind = next((p["kind"] for p in ppl if p["name"] == found["who"]), "")
    return {**found, "who_kind": kind}


def _chosen(ids: List[int]) -> List[Dict[str, Any]]:
    from app import commitments

    wanted = {int(i) for i in ids}
    items = [t for t in commitments.all_tasks() if t["id"] in wanted]
    if not items:
        raise HTTPException(status_code=404, detail="Нет задач для выгрузки")
    return items


class TaskExportPayload(BaseModel):
    fmt: str = "md"  # md | todoist | csv | obsidian
    ids: List[int] = []
    lang: str = "ru"


@router.post("/api/tasks/export")
def api_export_tasks(payload: TaskExportPayload) -> Dict[str, Any]:
    """The tasks on screen for another app: a Save as dialog, or a note in the Obsidian vault."""
    from app import commitments

    if payload.fmt not in ("md", "todoist", "csv", "obsidian"):
        raise HTTPException(status_code=400, detail="Неизвестный формат")
    items = _chosen(payload.ids)
    if payload.fmt == "obsidian":
        text = ("# Задачи\n\n" if payload.lang != "en" else "# Tasks\n\n") + commitments.export_text(items, "md", payload.lang)
        return _to_vault("Задачи VerbaScribe.md" if payload.lang != "en" else "VerbaScribe tasks.md", text)
    text = commitments.export_text(items, payload.fmt, payload.lang)
    ext = "md" if payload.fmt == "md" else "csv"
    name = f"{'tasks' if payload.lang == 'en' else 'задачи'}{'-todoist' if payload.fmt == 'todoist' else ''}.{ext}"
    return _save_as(name, ext, text)


def _save_as(name: str, ext: str, text: str) -> Dict[str, Any]:
    chosen = _pick_export_path(None, name, ext)
    if not chosen:
        return {"ok": False, "cancelled": True}
    out = Path(chosen)
    # Excel and Todoist read a CSV with a BOM as UTF-8
    out.write_text(text, encoding="utf-8-sig" if ext == "csv" else "utf-8")
    return {"ok": True, "path": str(out)}


def _to_vault(name: str, text: str) -> Dict[str, Any]:
    from app.obsidian import safe_filename, vault_dir

    folder = vault_dir()
    if not folder:
        raise HTTPException(status_code=400, detail="Папка хранилища Obsidian не выбрана в настройках")
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / safe_filename(name)
    out.write_text(text, encoding="utf-8")
    return {"ok": True, "path": str(out)}


class ReportPayload(BaseModel):
    start: str
    end: str
    include: List[str] = ["done", "open", "over"]
    fmt: str = "short"  # short | people | mail
    people: List[str] = []  # only these assignees ("" — the tasks on nobody)
    lang: str = "ru"
    use_model: bool = True


@router.post("/api/tasks/report")
def api_tasks_report(payload: ReportPayload) -> Dict[str, Any]:
    """The summary of a period. The facts are counted here; the analysis model, when there is one,
    tells them as text (and only them). Without a model the counted text is the summary."""
    from app import commitments
    from app.llm import LlmError

    tasks = commitments.all_tasks()
    if payload.people:
        wanted = {p.casefold() for p in payload.people}
        tasks = [t for t in tasks if (t["who"] or "").casefold() in wanted]
    try:
        facts = commitments.report(tasks, payload.start, payload.end, payload.include, payload.fmt, payload.lang)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not payload.use_model or not facts["counts"]["all"]:
        return {**facts, "model": ""}
    local = any(is_local_only(get_meeting(t["meeting_id"])) for t in tasks if t["meeting_id"])
    try:
        return commitments.report_by_model(facts, local_only=local)
    except (LlmError, OSError, RuntimeError):
        return {**facts, "model": "", "model_failed": True}


class ReportSavePayload(BaseModel):
    text: str
    title: str = ""
    target: str = "md"  # md (Save as) | obsidian (a note in the vault)


@router.post("/api/tasks/report/save")
def api_save_tasks_report(payload: ReportSavePayload) -> Dict[str, Any]:
    from app.obsidian import safe_filename

    if not payload.text.strip():
        raise HTTPException(status_code=400, detail="Пустая сводка")
    name = safe_filename(payload.title or "Сводка по задачам") + ".md"
    body = payload.text if payload.text.lstrip().startswith("#") else "# " + payload.text.lstrip()
    if payload.target == "obsidian":
        return _to_vault(name, body + "\n")
    return _save_as(name, "md", body + "\n")
