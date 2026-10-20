"""
Building export files of a recording (shared by the API and by exports that wait for a job).
Errors are ValueError (bad request) and LookupError (something missing); the API turns them into
HTTP errors.
"""

from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from app.exporter import export_docx, export_json, export_markdown, export_srt, export_txt

EXPORT_VARIANTS = {"translated", "bilingual"}
DOC_FORMATS = ("docx", "md", "txt", "srt", "json")


def export_source(m: Dict[str, Any], lang: Optional[str], variant: str) -> Tuple[Dict[str, Any], str]:
    """The meeting to export (translated copy if `lang` is given) and a file name suffix."""
    if not lang:
        return m, ""
    from app.translation import meeting_for_export

    if variant not in EXPORT_VARIANTS:
        raise ValueError("Неизвестный вариант экспорта перевода")
    src = meeting_for_export(m, lang, variant)  # ValueError when there is no such translation
    return src, f".{lang}" if variant == "translated" else f".{lang}+orig"


def safe_title(m: Dict[str, Any]) -> str:
    return "".join(c if c.isalnum() or c in " -_" else "_" for c in m.get("title", "meeting")).strip() or "meeting"


def build_export_bytes(
    m: Dict[str, Any], fmt: str, lang: Optional[str] = None, variant: str = "translated"
) -> Tuple[bytes, str, str]:
    """(content, media type, file name)."""
    fmt = fmt.lower()
    if lang and fmt == "json":
        raise ValueError("Перевод экспортируется в DOCX, MD, TXT или SRT")
    if lang and variant == "bilingual" and fmt not in ("srt", "docx"):
        raise ValueError("Двуязычный экспорт доступен в SRT и DOCX")
    try:
        src, suffix = export_source(m, lang, variant)
    except ValueError as e:
        if lang and variant in EXPORT_VARIANTS:
            raise LookupError(str(e)) from e
        raise
    base = f"{safe_title(m)}{suffix}"
    if fmt == "md":
        return export_markdown(src).encode("utf-8"), "text/markdown; charset=utf-8", f"{base}.md"
    if fmt == "txt":
        return export_txt(src).encode("utf-8"), "text/plain; charset=utf-8", f"{base}.txt"
    if fmt == "srt":
        if lang:
            from app.translation import build_cues, cues_to_srt

            cues = build_cues(
                m.get("segments") or [], (m.get("translations") or {}).get(lang), variant=variant,
                speakers=m.get("speakers"), with_speaker=False,
            )
            return cues_to_srt(cues).encode("utf-8"), "text/plain; charset=utf-8", f"{base}.srt"
        return export_srt(src).encode("utf-8"), "text/plain; charset=utf-8", f"{base}.srt"
    if fmt == "json":
        return export_json(src).encode("utf-8"), "application/json; charset=utf-8", f"{base}.json"
    if fmt == "docx":
        return (
            export_docx(src),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            f"{base}.docx",
        )
    raise ValueError(f"Неизвестный формат экспорта: {fmt}")


def default_export_dir() -> Path:
    """The last used export folder, or the user's Documents folder."""
    from app.database import get_all_settings

    saved = (get_all_settings().get("export_dir") or "").strip()
    if saved and Path(saved).is_dir():
        return Path(saved)
    docs = Path.home() / "Documents"
    return docs if docs.is_dir() else Path.home()


def unique_path(folder: Path, name: str) -> Path:
    """`name` in `folder`, with " (2)", " (3)" … added when the file already exists."""
    p = folder / name
    if not p.exists():
        return p
    stem, suffix = p.stem, p.suffix
    # "talk.ru+orig.srt": keep the language part together with the extension
    for i in range(2, 1000):
        cand = folder / f"{stem} ({i}){suffix}"
        if not cand.exists():
            return cand
    return p
