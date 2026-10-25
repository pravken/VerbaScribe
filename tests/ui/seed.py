"""Demo data for the browser scenarios (tests/ui/scenarios.mjs): three transcribed recordings and their
tasks in VERBASCRIBE_DATA_DIR, which must be a temporary folder, never the user's own data.

    VERBASCRIBE_DATA_DIR=/tmp/vs-data PYTHONPATH=tests/stubs python tests/ui/seed.py
"""
import math
import os
import struct
import sys
import tempfile
import wave
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
data_dir = os.environ.get("VERBASCRIBE_DATA_DIR", "")
temp_roots = [tempfile.gettempdir(), os.environ.get("RUNNER_TEMP", "") or tempfile.gettempdir()]
if not data_dir or not any(Path(data_dir).resolve().is_relative_to(Path(r).resolve()) for r in temp_roots):
    sys.exit("VERBASCRIBE_DATA_DIR must be a temporary folder")

from app import database as db  # noqa: E402 - after the data folder is checked

db.init_db()
wav = os.path.join(data_dir, "demo.wav")
with wave.open(wav, "w") as w:  # 60 s of speech-like bursts with pauses
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(16000)
    w.writeframes(b"".join(struct.pack("<h", int((3000 if (i // 16000) % 6 != 5 else 60) * math.sin(i / 8)))
                           for i in range(16000 * 60)))
SPEAKERS = {"SPEAKER_00": ("Мария", "#64D2FF"), "SPEAKER_01": ("Олег", "#FFB340"), "SPEAKER_02": ("Анна", "#C9A2FF")}
LINES = ["Ну, давайте начнём. Сегодня три темы.", "Первая тема — план на месяц.", "Я подготовлю список к пятнице.",
         "Хорошо, тогда вторая тема.", "По срокам нужно уточнить.", "Анна, посмотришь до среды?", "Да, посмотрю.",
         "Третья тема — итоги прошлой недели.", "Всё сделано, кроме одного пункта.", "Отлично, на этом всё."]


def segments():
    out = []
    for i, text in enumerate(LINES):
        start, words, t = i * 6.0, [], i * 6.0
        for word in text.split():
            words.append({"word": " " + word, "start": round(t, 2), "end": round(t + 0.35, 2)})
            t += 0.4
        out.append({"id": i + 1, "start": start, "end": t + 0.2, "speaker": list(SPEAKERS)[i % 3], "text": text, "words": words})
    return out


ids = []
for title in ("Планёрка отдела", "Созвон по проекту", "Разбор итогов недели"):
    m = db.create_meeting(title, "demo.wav", wav, language="ru")
    db.save_meeting_results(
        m["id"], speakers={k: {"id": k, "name": n, "color": c} for k, (n, c) in SPEAKERS.items()}, segments=segments(),
        summaries={"protocol": "## Итоги\n- План согласован\n- Сроки уточнить", "tldr": "Обсудили план и сроки.",
                   "chapters": "- [00:00] Начало — темы встречи\n- [00:18] Сроки — что уточнить\n- [00:42] Итоги — прошлая неделя"},
        language="ru", transcript_model="gigaam-v3")
    conn = db.get_connection()
    conn.execute("UPDATE meetings SET status = 'completed', progress = 100, duration = 60 WHERE id = ?", (m["id"],))
    conn.commit()
    conn.close()
    ids.append(m["id"])

from app import commitments  # noqa: E402

today = date.today()
commitments.create("Подготовить список к пятнице", who="Олег", who_kind="part", due_date=(today + timedelta(days=2)).isoformat(), meeting_id=ids[0])
commitments.create("Посмотреть сроки", who="Анна", who_kind="part", due_date=(today + timedelta(days=1)).isoformat(), meeting_id=ids[0])
print("seeded", *ids)
