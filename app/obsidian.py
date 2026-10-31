"""
Notes in an Obsidian vault: one Markdown file per recording with YAML properties, the overview,
tasks, chapters and the transcript (each part can be switched off in the settings).

The file is named by a template, "{date} {title}" by default, where the date is when the meeting
took place (the file date of an imported recording). The note's path is remembered, so a later
export (after renaming, a new summary) updates the same note, and a new title renames it instead of
leaving a copy behind. Exports run automatically after processing when "obsidian_auto" is on.
"""

import re

from app.grounding import readable_summaries
import threading
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.audio_utils import format_timestamp

_INVALID = re.compile(r'[\\/:*?"<>|\x00-\x1f#^\[\]]')
_lock = threading.Lock()

_LABELS = {
    "ru": {"summary": "Резюме", "overview": "Обзор", "tasks": "Задачи", "chapters": "Главы", "transcript": "Расшифровка",
           "participants": "Участники", "untitled": "Запись", "notes": "Заметки со встречи", "promises": "Задачи"},
    "en": {"summary": "Summary", "overview": "Overview", "tasks": "Tasks", "chapters": "Chapters", "transcript": "Transcript",
           "participants": "Participants", "untitled": "Recording", "notes": "Notes from the meeting", "promises": "Tasks"},
}


def _settings() -> Dict[str, str]:
    from app.database import get_all_settings

    return get_all_settings()


def _on(settings: Dict[str, str], key: str, default: str = "true") -> bool:
    return (settings.get(key) or default).lower() == "true"


def meeting_datetime(m: Dict[str, Any]) -> datetime:
    for raw in (m.get("recorded_at"), m.get("created_at")):
        try:
            return datetime.strptime(str(raw or "")[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    return datetime.now()


def safe_filename(name: str, limit: int = 150) -> str:
    """A file name Windows, macOS and Obsidian links all accept."""
    name = _INVALID.sub(" ", name or "")
    name = re.sub(r"\s+", " ", name).strip().strip(".")
    if re.fullmatch(r"(?i)(con|prn|aux|nul|com\d|lpt\d)", name):
        name = f"_{name}"
    return name[:limit].rstrip(" .") or "note"


def note_filename(m: Dict[str, Any], template: str = "") -> str:
    dt = meeting_datetime(m)
    template = (template or "{date} {title}").strip() or "{date} {title}"
    name = (
        template.replace("{date}", dt.strftime("%Y-%m-%d"))
        .replace("{time}", dt.strftime("%H-%M"))
        .replace("{title}", m.get("title") or "")
        .replace("{lang}", (m.get("language") or "").upper())
    )
    return safe_filename(name) + ".md"


def _yaml_str(value: Any) -> str:
    s = str(value if value is not None else "")
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'


def _demote(md: str, levels: int = 1) -> str:
    """Headings of a summary sit below the note's own '## ' sections."""
    out = []
    in_code = False
    for line in (md or "").splitlines():
        if line.lstrip().startswith("```"):
            in_code = not in_code
        if not in_code and re.match(r"^#{1,5}\s", line):
            line = "#" * levels + line
        out.append(line)
    return "\n".join(out).strip()


def build_note(m: Dict[str, Any], settings: Optional[Dict[str, str]] = None) -> str:
    settings = settings or _settings()
    lang = "en" if (m.get("language") or "") == "en" else "ru"
    L = _LABELS[lang]
    dt = meeting_datetime(m)
    speakers = m.get("speakers") or {}
    segments = m.get("segments") or []
    summaries = readable_summaries(m)
    names = [s.get("name") or k for k, s in speakers.items()]

    props = [
        "---",
        f"title: {_yaml_str(m.get('title') or L['untitled'])}",
        f"date: {dt.strftime('%Y-%m-%d')}",
        f"time: {_yaml_str(dt.strftime('%H:%M'))}",
        f"duration: {_yaml_str(format_timestamp(m.get('duration') or 0.0))}",
        f"language: {_yaml_str(m.get('language') or '')}",
        "participants:",
        *[f"  - {_yaml_str(n)}" for n in names],
        f"source: {_yaml_str(m.get('filename') or '')}",
        "tags:",
        "  - verbaliox",
        "  - meeting",
        f"verbaliox_id: {int(m.get('id') or 0)}",
        "---",
        "",
        f"# {m.get('title') or L['untitled']}",
        "",
    ]
    if names:
        props += [f"**{L['participants']}:** " + ", ".join(names), ""]
    body: List[str] = []
    if (m.get("user_notes") or "").strip():
        body += [f"## {L['notes']}", "", m["user_notes"].strip(), ""]
    if _on(settings, "obsidian_summary"):
        if summaries.get("tldr"):
            body += [f"## {L['summary']}", "", _demote(summaries["tldr"]), ""]
        if summaries.get("protocol"):
            body += [f"## {L['overview']}", "", _demote(summaries["protocol"]), ""]
    try:
        from app import commitments

        promised = commitments.for_meetings([int(m["id"])]) if m.get("id") else []
    except Exception:
        promised = []
    if promised:
        body += [f"## {L['promises']}", "", commitments.export_text(promised, "md").strip(), ""]
    if _on(settings, "obsidian_tasks") and summaries.get("tasks"):
        body += [f"## {L['tasks']}", "", _tasks_as_checklist(_demote(summaries["tasks"])), ""]
    if _on(settings, "obsidian_chapters") and summaries.get("chapters"):
        body += [f"## {L['chapters']}", "", _demote(summaries["chapters"]), ""]
    if _on(settings, "obsidian_transcript") and segments:
        body += [f"## {L['transcript']}", ""]
        for seg in segments:
            spk = speakers.get(seg.get("speaker") or "", {}).get("name") or seg.get("speaker") or ""
            body.append(f"**{spk}** `{format_timestamp(seg.get('start') or 0.0)}` {(seg.get('text') or '').strip()}")
            body.append("")
    return "\n".join(props + body).rstrip() + "\n"


def _tasks_as_checklist(md: str) -> str:
    """'- do X' lines become Obsidian tasks ('- [ ] do X'), so they show up in task queries."""
    return "\n".join(re.sub(r"^(\s*)[-*]\s+(?!\[[ xX]\])", r"\1- [ ] ", ln) for ln in md.splitlines())


def vault_dir(settings: Optional[Dict[str, str]] = None) -> Optional[Path]:
    settings = settings or _settings()
    vault = (settings.get("obsidian_vault") or "").strip()
    if not vault:
        return None
    sub = (settings.get("obsidian_subfolder") or "").strip().strip("\\/")
    parts = [safe_filename(p, 80) for p in re.split(r"[\\/]+", sub) if p.strip()] if sub else []
    return Path(vault).expanduser().joinpath(*parts)


def _prepare(meeting_id: int, generate: bool, settings: Dict[str, str]) -> Dict[str, Any]:
    """Makes what the note needs and is missing: a title from the content, the overview and tasks."""
    from app.database import get_meeting, save_meeting_results

    m = get_meeting(meeting_id) or {}
    if not generate or not m.get("segments"):
        return m
    from app.database import is_local_only
    from app.ollama_client import generate_meeting_summary

    summaries = dict(m.get("summaries") or {})
    changed = False
    wanted = ["protocol"] if _on(settings, "obsidian_summary") else []
    if _on(settings, "obsidian_tasks"):
        wanted.append("tasks")
    for key in wanted:
        if summaries.get(key):
            continue
        try:
            from app.grounding import maybe_ground

            summaries[key] = maybe_ground(generate_meeting_summary(
                segments=m["segments"], speakers=m.get("speakers") or {}, template_key=key,
                answer_lang=m.get("language") or "ru",
                local_only=is_local_only(m), user_notes=m.get("user_notes") or "",
            ), key, m)
            changed = True
        except Exception as e:
            print(f"[obsidian] {key} for meeting {meeting_id} failed: {e}", flush=True)
            break  # Ollama is not available: the note is written with what there is
    if changed:
        from app.database import merge_summaries

        merge_summaries(meeting_id, {k: summaries[k] for k in wanted if summaries.get(k)})
        m = get_meeting(meeting_id) or m
    return m


def export_meeting(meeting_id: int, generate: bool = False, in_job: bool = False, explicit: bool = True) -> Path:
    """Writes (or updates) the note of a recording; returns its path. `in_job`: called by the
    transcription job itself, when the transcript is saved but the status is not "completed" yet."""
    from app.database import get_meeting, update_meeting_fields

    settings = _settings()
    folder = vault_dir(settings)
    if folder is None:
        raise ValueError("Папка хранилища Obsidian не выбрана в настройках")
    with _lock:
        m = _prepare(meeting_id, generate and _on(settings, "obsidian_generate"), settings)
        if not m:
            raise LookupError("Встреча не найдена")
        if (m.get("status") != "completed" and not in_job) or not m.get("segments"):
            raise ValueError("Запись ещё не расшифрована")
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / note_filename(m, settings.get("obsidian_filename") or "")
        old = Path(m.get("obsidian_path") or "")
        if old.name and old.is_file() and old.resolve() != target.resolve() and not target.exists():
            # The title (or the template) changed: move the note instead of leaving the old one
            old.replace(target)
        elif target.exists() and (not old.name or old.resolve() != target.resolve()) and not _is_ours(target, meeting_id):
            # Another note already has this name: never overwrite it
            target = _unique(target)
        existing = target.read_text(encoding="utf-8", errors="replace") if target.is_file() else ""
        ours, notes = split_user_notes(existing)
        if in_job is False and not explicit and existing and m.get("obsidian_hash") and _hash(ours) != m["obsidian_hash"]:
            # Changed in Obsidian above the user's section: an automatic update must not undo that
            print(f"[obsidian] {target.name} was edited in Obsidian: not updated automatically", flush=True)
            return target
        body = build_note(m, settings).rstrip() + "\n"
        target.write_text(body + "\n" + (notes or _notes_section(m)), encoding="utf-8")
        update_meeting_fields(meeting_id, obsidian_path=str(target), obsidian_hash=_hash(body))
        return target


# The end of every note belongs to the user: Verbaliox rewrites what is above this heading and
# keeps everything from it on (tasks ticked, own notes)
NOTES_MARKER = "<!-- verbaliox:notes -->"
FORMER_NOTES_MARKER = "<!-- verbascribe:notes -->"  # notes written while the program was called VerbaScribe


def _notes_section(m: Dict[str, Any]) -> str:
    head = "## My notes" if (m.get("language") or "") == "en" else "## Мои заметки"
    return f"{NOTES_MARKER}\n{head}\n\n"


def split_user_notes(text: str) -> tuple:
    """(the part Verbaliox wrote, the user's section from the marker on, or "")."""
    i = text.find(NOTES_MARKER)
    if i < 0 and FORMER_NOTES_MARKER in text:
        text = text.replace(FORMER_NOTES_MARKER, NOTES_MARKER, 1)
        i = text.find(NOTES_MARKER)
    if i < 0:
        return text.rstrip() + "\n" if text else "", ""
    return text[:i].rstrip() + "\n", text[i:]


def _hash(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


def _is_ours(path: Path, meeting_id: int) -> bool:
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:2000]
    except OSError:
        return False
    # verbascribe_id: a note written while the program had its former name
    return re.search(rf"^verba(?:liox|scribe)_id:\s*{int(meeting_id)}\s*$", head, re.MULTILINE) is not None


def _unique(path: Path) -> Path:
    for i in range(2, 1000):
        cand = path.with_name(f"{path.stem} ({i}){path.suffix}")
        if not cand.exists():
            return cand
    return path


def auto_export_enabled(settings: Optional[Dict[str, str]] = None) -> bool:
    settings = settings or _settings()
    return _on(settings, "obsidian_auto", "false") and bool((settings.get("obsidian_vault") or "").strip())


def refresh_if_exported(meeting_id: int) -> None:
    """After a rename or a new summary: update the note in the background if it was saved before."""
    from app.database import get_meeting

    m = get_meeting(meeting_id)
    if not m or not m.get("obsidian_path") or not auto_export_enabled():
        return

    def _run() -> None:
        try:
            export_meeting(meeting_id, generate=False, explicit=False)
        except Exception:
            traceback.print_exc()

    threading.Thread(target=_run, daemon=True, name="obsidian_refresh").start()
