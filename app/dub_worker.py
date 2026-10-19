"""
GPU worker for the "Translate & Dub" feature.

Runs inside the optional .venv-dub environment (Chatterbox + Demucs pin library versions that
conflict with the main app), so it must NOT import anything from the `app` package.

Protocol: one JSON command per line on stdin, one JSON reply per line on stdout.
Every reply has "ok": true/false; progress lines have "event": "progress". Logs go to stderr.
Running in a separate process also means all VRAM is returned to the OS when the job ends
or is cancelled (the parent simply kills the process).

Commands:
  {"cmd": "separate", "input": "orig.wav", "out_dir": "..."}
      -> {"ok": true, "vocals": "...", "background": "...", "sr": 44100}
  {"cmd": "tts", "items": [{"id", "text", "lang", "ref", "out"}], "exaggeration", "cfg_weight"}
      -> progress per item, then {"ok": true, "results": [{"id", "out", "duration"}]}
  {"cmd": "quit"}
"""

import json
import os
import sys
import time
import traceback
from pathlib import Path

# Started with `python -I`, so PYTHONIOENCODING is ignored: force UTF-8 pipes explicitly
for _stream in (sys.stdin, sys.stdout, sys.stderr):
    _stream.reconfigure(encoding="utf-8", errors="replace")
_stdout = sys.stdout
# Libraries print to stdout; keep it clean for the protocol
sys.stdout = sys.stderr


def _reply(obj) -> None:
    _stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    _stdout.flush()


def _device() -> str:
    import torch

    return "cuda" if torch.cuda.is_available() else "cpu"


# --- Demucs: split voice from music/ambience (MIT) ---
_demucs_model = None


def cmd_separate(req):
    global _demucs_model
    import numpy as np
    import soundfile as sf
    import torch
    from demucs.apply import apply_model
    from demucs.pretrained import get_model

    dev = _device()
    if _demucs_model is None:
        _demucs_model = get_model("htdemucs")
        _demucs_model.eval()
    model = _demucs_model
    out_dir = Path(req["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)

    audio, sr = sf.read(req["input"], dtype="float32", always_2d=True)
    if sr != model.samplerate:
        raise RuntimeError(f"Expected {model.samplerate} Hz input, got {sr}")
    wav = torch.from_numpy(audio.T.copy())
    if wav.shape[0] == 1:
        wav = wav.repeat(2, 1)
    ref = wav.mean(0)
    mean, std = ref.mean(), ref.std() + 1e-8
    with torch.no_grad():
        sources = apply_model(
            model, ((wav - mean) / std)[None], device=dev, shifts=0, split=True, overlap=0.25, progress=False
        )[0]
    sources = sources * std + mean
    names = model.sources  # drums, bass, other, vocals
    vocals = sources[names.index("vocals")]
    background = sources.sum(0) - vocals

    v_path, b_path = out_dir / "vocals.wav", out_dir / "background.wav"
    sf.write(v_path, vocals.cpu().numpy().T, sr)
    sf.write(b_path, background.cpu().numpy().T, sr)
    del sources
    if dev == "cuda":
        torch.cuda.empty_cache()
    return {"ok": True, "vocals": str(v_path), "background": str(b_path), "sr": sr}


# --- Chatterbox Multilingual: zero-shot voice cloning TTS (MIT, 23 languages incl. ru/en) ---
_tts_model = None


def cmd_tts(req):
    global _tts_model, _demucs_model
    import torch
    import torchaudio as ta

    dev = _device()
    if _demucs_model is not None:
        _demucs_model = None
        if dev == "cuda":
            torch.cuda.empty_cache()
    if _tts_model is None:
        from chatterbox.mtl_tts import ChatterboxMultilingualTTS

        t0 = time.time()
        _tts_model = ChatterboxMultilingualTTS.from_pretrained(device=dev)
        print(f"[dub_worker] Chatterbox loaded in {time.time() - t0:.1f}s on {dev}", file=sys.stderr)

    items = req["items"]
    exaggeration = float(req.get("exaggeration", 0.5))
    results = []
    current_ref = None
    for i, it in enumerate(items):
        t0 = time.time()
        # Speaker conditioning is computed once per reference clip, not once per phrase
        if it["ref"] != current_ref:
            _tts_model.prepare_conditionals(it["ref"], exaggeration=exaggeration)
            current_ref = it["ref"]
        wav = _tts_model.generate(
            it["text"],
            language_id=it["lang"],
            exaggeration=exaggeration,
            cfg_weight=float(req.get("cfg_weight", 0.5)),
        )
        out = Path(it["out"])
        out.parent.mkdir(parents=True, exist_ok=True)
        ta.save(str(out), wav.cpu(), _tts_model.sr)
        dur = wav.shape[-1] / _tts_model.sr
        results.append({"id": it["id"], "out": str(out), "duration": round(dur, 3), "gen_sec": round(time.time() - t0, 2)})
        _reply({"ok": True, "event": "progress", "done": i + 1, "total": len(items)})
    vram = torch.cuda.max_memory_allocated() / 2**30 if dev == "cuda" else 0.0
    return {"ok": True, "results": results, "sr": _tts_model.sr, "peak_vram_gb": round(vram, 2)}


def main() -> None:
    handlers = {"separate": cmd_separate, "tts": cmd_tts}
    _reply({"ok": True, "event": "ready", "pid": os.getpid()})
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            if req.get("cmd") == "quit":
                _reply({"ok": True, "event": "bye"})
                return
            handler = handlers.get(req.get("cmd"))
            if not handler:
                _reply({"ok": False, "error": f"unknown command {req.get('cmd')!r}"})
                continue
            _reply(handler(req))
        except Exception as e:
            _reply({"ok": False, "error": str(e), "trace": traceback.format_exc()[-1500:]})


if __name__ == "__main__":
    main()
