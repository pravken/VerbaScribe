import io
import json
from typing import Any, Dict, Tuple

from docx import Document
from docx.shared import Pt, RGBColor

from app.audio_utils import format_timestamp


def _hex_to_rgb(hex_color: str) -> Tuple[int, int, int]:
    h = hex_color.lstrip("#")
    if len(h) == 6:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return 79, 70, 229


def export_markdown(meeting: Dict[str, Any]) -> str:
    title = meeting.get("title", "Встреча")
    created_at = meeting.get("created_at", "")
    duration_str = format_timestamp(meeting.get("duration", 0.0))
    speakers = meeting.get("speakers") or {}
    segments = meeting.get("segments") or []
    summaries = meeting.get("summaries") or {}

    lines = [
        f"# {title}",
        "",
        f"- **Дата обработки:** {created_at}",
        f"- **Длительность:** {duration_str}",
        f"- **Модель:** `{meeting.get('model_name', 'large-v3-turbo')}`",
        f"- **Участники ({len(speakers)}):** "
        + (", ".join(s.get("name", k) for k, s in speakers.items()) or "Не определены"),
        "",
    ]

    if summaries.get("protocol"):
        lines.extend(
            [
                "---",
                "",
                summaries["protocol"],
                "",
            ]
        )
    elif summaries.get("tldr"):
        lines.extend(
            [
                "---",
                "",
                summaries["tldr"],
                "",
            ]
        )

    lines.extend(
        [
            "---",
            "",
            "## Полная расшифровка встречи",
            "",
        ]
    )

    for seg in segments:
        spk_id = seg.get("speaker", "SPEAKER_00")
        spk_name = speakers.get(spk_id, {}).get("name", spk_id)
        ts_start = format_timestamp(seg.get("start", 0.0))
        ts_end = format_timestamp(seg.get("end", 0.0))
        lines.append(f"**[{ts_start} – {ts_end}] {spk_name}:**")
        lines.append(f"{seg.get('text', '').strip()}")
        if seg.get("secondary_text"):
            lines.append(f"*{seg['secondary_text'].strip()}*")
        lines.append("")

    return "\n".join(lines)


def export_txt(meeting: Dict[str, Any]) -> str:
    title = meeting.get("title", "Встреча")
    created_at = meeting.get("created_at", "")
    duration_str = format_timestamp(meeting.get("duration", 0.0))
    speakers = meeting.get("speakers") or {}
    segments = meeting.get("segments") or []
    summaries = meeting.get("summaries") or {}

    lines = [
        f"ПРОТОКОЛ ВСТРЕЧИ: {title}",
        f"Дата: {created_at} | Длительность: {duration_str}",
    ]
    if meeting.get("translation_note"):
        lines.append(f"Перевод: {meeting['translation_note']}")
    lines += ["=" * 60, ""]

    if summaries.get("protocol"):
        lines.extend(
            [
                "ИИ-САММАРИ ВСТРЕЧИ:",
                "-" * 40,
                summaries["protocol"],
                "",
                "=" * 60,
                "",
            ]
        )

    lines.append("РАСШИФРОВКА ПО РОЛЯМ:")
    lines.append("-" * 40)

    for seg in segments:
        spk_id = seg.get("speaker", "SPEAKER_00")
        spk_name = speakers.get(spk_id, {}).get("name", spk_id)
        ts_start = format_timestamp(seg.get("start", 0.0))
        lines.append(f"[{ts_start}] {spk_name}: {seg.get('text', '').strip()}")
        if seg.get("secondary_text"):
            lines.append(f"    {seg['secondary_text'].strip()}")

    return "\n".join(lines)


def export_srt(meeting: Dict[str, Any]) -> str:
    speakers = meeting.get("speakers") or {}
    segments = meeting.get("segments") or []

    blocks = []
    for idx, seg in enumerate(segments, start=1):
        spk_id = seg.get("speaker", "SPEAKER_00")
        spk_name = speakers.get(spk_id, {}).get("name", spk_id)
        start_srt = format_timestamp(seg.get("start", 0.0), srt=True)
        end_srt = format_timestamp(seg.get("end", 0.0), srt=True)
        text = seg.get("text", "").strip()
        if seg.get("secondary_text"):
            text += "\n" + seg["secondary_text"].strip()
        blocks.append(f"{idx}\n{start_srt} --> {end_srt}\n[{spk_name}] {text}")

    return "\n\n".join(blocks)


def export_json(meeting: Dict[str, Any]) -> str:
    payload = {
        "id": meeting.get("id"),
        "title": meeting.get("title"),
        "filename": meeting.get("filename"),
        "duration": meeting.get("duration"),
        "language": meeting.get("language"),
        "model_name": meeting.get("model_name"),
        "created_at": meeting.get("created_at"),
        "speakers": meeting.get("speakers", {}),
        "summaries": meeting.get("summaries", {}),
        "segments": meeting.get("segments", []),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def export_docx(meeting: Dict[str, Any]) -> bytes:
    doc = Document()

    title = meeting.get("title", "Протокол встречи")
    speakers = meeting.get("speakers") or {}
    segments = meeting.get("segments") or []
    summaries = meeting.get("summaries") or {}

    doc.add_heading(title, level=0)

    meta_p = doc.add_paragraph()
    meta_p.add_run(f"Дата: {meeting.get('created_at', '')}  |  ").italic = True
    meta_p.add_run(
        f"Длительность: {format_timestamp(meeting.get('duration', 0.0))}  |  "
    ).italic = True
    participant_names = ", ".join(s.get("name", k) for k, s in speakers.items())
    meta_p.add_run(f"Участники: {participant_names or '1'}").italic = True
    if meeting.get("translation_note"):
        meta_p.add_run(f"\nПеревод: {meeting['translation_note']}").italic = True

    if summaries.get("protocol"):
        doc.add_heading("AI-Саммари встречи", level=1)
        for line in summaries["protocol"].splitlines():
            clean = line.strip()
            if not clean:
                continue
            if clean.startswith("## "):
                doc.add_heading(clean.replace("## ", ""), level=2)
            elif clean.startswith("### "):
                doc.add_heading(clean.replace("### ", ""), level=3)
            else:
                doc.add_paragraph(clean)

    doc.add_heading("Расшифровка встречи по участникам", level=1)

    for seg in segments:
        spk_id = seg.get("speaker", "SPEAKER_00")
        spk_info = speakers.get(spk_id, {})
        spk_name = spk_info.get("name", spk_id)
        spk_color = spk_info.get("color", "#4f46e5")
        r, g, b = _hex_to_rgb(spk_color)

        ts_start = format_timestamp(seg.get("start", 0.0))
        p = doc.add_paragraph()

        ts_run = p.add_run(f"[{ts_start}] ")
        ts_run.font.size = Pt(9.5)
        ts_run.font.color.rgb = RGBColor(120, 120, 130)

        spk_run = p.add_run(f"{spk_name}: ")
        spk_run.bold = True
        spk_run.font.color.rgb = RGBColor(r, g, b)

        txt_run = p.add_run(seg.get("text", "").strip())
        txt_run.font.size = Pt(11)

        if seg.get("secondary_text"):
            tr_p = doc.add_paragraph()
            tr_p.paragraph_format.left_indent = Pt(18)
            tr_run = tr_p.add_run(seg["secondary_text"].strip())
            tr_run.italic = True
            tr_run.font.size = Pt(10.5)
            tr_run.font.color.rgb = RGBColor(90, 90, 110)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
