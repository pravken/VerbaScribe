"""
Conversation analytics of a recording, from its segments and word timings: who talked how long and
how fast, the longest monologues, interruptions, questions, and how much of the time was silence.
"""

from typing import Any, Dict, List

INTERRUPT_OVERLAP_SEC = 0.3  # a turn that starts this long before the previous one ends cuts in


def conversation_analytics(m: Dict[str, Any]) -> Dict[str, Any]:
    segs = sorted((s for s in m.get("segments") or [] if (s.get("text") or "").strip()), key=lambda s: float(s.get("start") or 0))
    speakers = m.get("speakers") or {}
    duration = float(m.get("duration") or 0) or (float(segs[-1].get("end") or 0) if segs else 0.0)
    per: Dict[str, Dict[str, Any]] = {}
    # A turn: consecutive segments of one speaker
    turns: List[Dict[str, Any]] = []
    for s in segs:
        spk = s.get("speaker") or "SPEAKER_00"
        start, end = float(s.get("start") or 0), float(s.get("end") or 0)
        words = len(s.get("words") or []) or len((s.get("text") or "").split())
        p = per.setdefault(spk, {"seconds": 0.0, "words": 0, "turns": 0, "questions": 0, "interruptions": 0, "longest": 0.0})
        p["seconds"] += max(0.0, end - start)
        p["words"] += words
        p["questions"] += (s.get("text") or "").count("?")
        if turns and turns[-1]["speaker"] == spk and start - turns[-1]["end"] < 2.0:
            turns[-1]["end"] = max(turns[-1]["end"], end)
            continue
        if turns and turns[-1]["speaker"] != spk and start < turns[-1]["end"] - INTERRUPT_OVERLAP_SEC:
            p["interruptions"] += 1
        turns.append({"speaker": spk, "start": start, "end": end})
        p["turns"] += 1
    for t in turns:
        p = per[t["speaker"]]
        p["longest"] = max(p["longest"], t["end"] - t["start"])
    talk = sum(p["seconds"] for p in per.values())
    rows = []
    for spk, p in sorted(per.items(), key=lambda kv: -kv[1]["seconds"]):
        rows.append({
            "speaker": spk, "name": (speakers.get(spk) or {}).get("name") or spk, "color": (speakers.get(spk) or {}).get("color") or "#0A84FF",
            "seconds": round(p["seconds"], 1), "share": round(100 * p["seconds"] / talk, 1) if talk else 0.0,
            "turns": p["turns"], "words": p["words"],
            "wpm": round(p["words"] / (p["seconds"] / 60)) if p["seconds"] >= 10 else None,
            "longest": round(p["longest"], 1), "questions": p["questions"], "interruptions": p["interruptions"],
        })
    longest = sorted(turns, key=lambda t: t["start"] - t["end"])[:3]
    return {
        "duration": round(duration, 1), "talk_seconds": round(talk, 1),
        "silence_share": round(100 * max(0.0, duration - talk) / duration, 1) if duration else 0.0,
        "turns": len(turns), "avg_turn": round(sum(t["end"] - t["start"] for t in turns) / len(turns), 1) if turns else 0.0,
        "speakers": rows,
        "longest_monologues": [{"speaker": t["speaker"], "name": (speakers.get(t["speaker"]) or {}).get("name") or t["speaker"],
                                "start": round(t["start"], 1), "seconds": round(t["end"] - t["start"], 1)} for t in longest],
    }
