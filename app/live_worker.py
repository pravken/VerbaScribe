"""
Separate process that transcribes live-caption windows (see app/live.py).

Live captions run next to other GPU work (a translation model loading in Ollama, a job in the
queue). A native failure there (CUDA out of memory inside CTranslate2 can abort the process) must
not take the whole app down, so the Whisper model for captions lives in this worker.

Protocol (binary stdin, text stdout):
  request:  one JSON line {"lang": "ru" | null, "bytes": N}, then N bytes of 16 kHz mono PCM16
  response: one JSON line {"ok": true, "segments": [[start, end, text], ...], "language": "ru", "prob": 0.98}
The first line written is {"ok": true, "event": "ready", "device": "cuda"}.
"""

import json
import os
import sys
import traceback


def main() -> None:
    # Libraries print to stdout; the protocol gets its own copy of the stream
    proto = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr

    import numpy as np

    from app.database import get_all_settings
    from app.hardware import get_hardware_info
    from app.live import caption_model
    from app.transcriber import _load_whisper_with_fallback

    settings = get_all_settings()
    hw = get_hardware_info()
    device = settings.get("device") or hw["recommended_device"]
    name = caption_model(settings, hw)
    model = _load_whisper_with_fallback(
        name,
        device,
        settings.get("compute_type") or hw["recommended_compute_type"],
    )
    proto.write(json.dumps({"ok": True, "event": "ready", "device": device, "model": name}) + "\n")

    stdin = sys.stdin.buffer
    while True:
        header = stdin.readline()
        if not header:
            return
        try:
            req = json.loads(header)
            data = stdin.read(int(req["bytes"]))
            audio = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
            segments, info = model.transcribe(
                audio,
                language=req.get("lang") or None,
                beam_size=1,
                best_of=1,
                temperature=0.0,
                condition_on_previous_text=False,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 400},
            )
            out = [[round(s.start, 2), round(s.end, 2), s.text] for s in segments if (s.text or "").strip()]
            proto.write(json.dumps({
                "ok": True, "segments": out,
                "language": getattr(info, "language", None),
                "prob": float(getattr(info, "language_probability", 0.0) or 0.0),
            }, ensure_ascii=False) + "\n")
        except Exception as e:  # a bad window must not end the worker
            traceback.print_exc()
            proto.write(json.dumps({"ok": False, "error": str(e)[:300]}, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
