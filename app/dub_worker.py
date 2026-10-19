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
  {"cmd": "download", "models": ["chatterbox", "demucs"]}
      -> fetches the weights into HF_HOME without loading them on the GPU
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
_CJK_LANGS = {"zh", "ja", "ko"}


def _patch_alignment_analyzer() -> None:
    """
    Chatterbox's hallucination check slices off the last 5 text tokens of the alignment matrix and
    crashes (IndexError on max()) for very short phrases such as "Ok." Skip that check for such a
    step instead of failing the phrase.
    """
    try:
        from chatterbox.models.t3.inference.alignment_stream_analyzer import AlignmentStreamAnalyzer
    except Exception:
        return
    if getattr(AlignmentStreamAnalyzer, "_verbascribe_patched", False):
        return
    original_step = AlignmentStreamAnalyzer.step

    def step(self, logits, next_token=None):
        try:
            return original_step(self, logits, next_token=next_token)
        except IndexError:
            return logits

    AlignmentStreamAnalyzer.step = step
    AlignmentStreamAnalyzer._verbascribe_patched = True


def _generate(model, text: str, lang: str, cfg_weight: float):
    """
    Same as ChatterboxMultilingualTTS.generate (0.1.7), but the number of speech tokens is capped by
    the text length instead of a fixed 1000 (~40 s), so a run-on generation stops early.
    """
    import torch
    import torch.nn.functional as F
    from chatterbox.mtl_tts import punc_norm
    from chatterbox.models.s3tokenizer import drop_invalid_tokens

    expected_sec = max(0.6, len(text.strip()) / (6.0 if lang in _CJK_LANGS else 14.0))
    max_new_tokens = int(expected_sec * 25 * 2.5) + 60  # S3 speech tokens: 25 per second
    text_tokens = model.tokenizer.text_to_tokens(punc_norm(text), language_id=lang.lower()).to(model.device)
    text_tokens = torch.cat([text_tokens, text_tokens], dim=0)  # two sequences for CFG
    text_tokens = F.pad(text_tokens, (1, 0), value=model.t3.hp.start_text_token)
    text_tokens = F.pad(text_tokens, (0, 1), value=model.t3.hp.stop_text_token)
    with torch.inference_mode():
        speech_tokens = model.t3.inference(
            t3_cond=model.conds.t3, text_tokens=text_tokens, max_new_tokens=max_new_tokens,
            temperature=0.8, cfg_weight=cfg_weight, repetition_penalty=2.0, min_p=0.05, top_p=1.0,
        )
        speech_tokens = drop_invalid_tokens(speech_tokens[0]).to(model.device)
        if speech_tokens.numel() == 0:
            raise RuntimeError("no speech tokens generated")
        wav, _ = model.s3gen.inference(speech_tokens=speech_tokens, ref_dict=model.conds.gen)
        wav = wav.squeeze(0).detach().cpu().numpy()
        wav = model.watermarker.apply_watermark(wav, sample_rate=model.sr)
    return torch.from_numpy(wav).unsqueeze(0)


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

        _patch_alignment_analyzer()

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
        # One phrase must never fail a whole dub: retry once, then leave it silent
        wav, error = None, ""
        for _attempt in range(2):
            try:
                wav = _generate(_tts_model, it["text"], it["lang"], float(req.get("cfg_weight", 0.5)))
                break
            except Exception as e:  # noqa: BLE001
                error = f"{type(e).__name__}: {e}"[:300]
                print(f"[dub_worker] phrase {it['id']} failed: {error}", file=sys.stderr)
                if dev == "cuda":
                    torch.cuda.empty_cache()
        if wav is None:
            wav = torch.zeros(1, int(0.3 * _tts_model.sr))
        out = Path(it["out"])
        out.parent.mkdir(parents=True, exist_ok=True)
        ta.save(str(out), wav.cpu(), _tts_model.sr)
        dur = wav.shape[-1] / _tts_model.sr
        res = {"id": it["id"], "out": str(out), "duration": round(dur, 3), "gen_sec": round(time.time() - t0, 2)}
        if error and wav.abs().max() == 0:
            res["failed"] = error
        results.append(res)
        _reply({"ok": True, "event": "progress", "done": i + 1, "total": len(items)})
    vram = torch.cuda.max_memory_allocated() / 2**30 if dev == "cuda" else 0.0
    return {"ok": True, "results": results, "sr": _tts_model.sr, "peak_vram_gb": round(vram, 2)}


# Same file list as ChatterboxMultilingualTTS.from_pretrained (0.1.7)
_CHATTERBOX_FILES = ["ve.pt", "t3_mtl23ls_v2.safetensors", "s3gen.pt", "grapheme_mtl_merged_expanded_v1.json",
                     "conds.pt", "Cangjie5_TC.json"]


def cmd_download(req):
    from huggingface_hub import hf_hub_download, snapshot_download

    done = []
    for name in req.get("models") or []:
        if name == "chatterbox":
            snapshot_download(repo_id="ResembleAI/chatterbox", repo_type="model", revision="main",
                              allow_patterns=_CHATTERBOX_FILES, token=os.getenv("HF_TOKEN"))
        elif name == "demucs":
            import yaml

            with open(hf_hub_download("adefossez/HTDemucs", "htdemucs.yaml")) as f:
                bag = yaml.safe_load(f)
            for sig in bag["models"]:
                hf_hub_download("adefossez/HTDemucs", f"{sig}.safetensors")
        else:
            raise RuntimeError(f"unknown model {name!r}")
        done.append(name)
        _reply({"ok": True, "event": "progress", "done": len(done), "total": len(req.get("models") or [])})
    return {"ok": True, "downloaded": done}


def main() -> None:
    handlers = {"separate": cmd_separate, "tts": cmd_tts, "download": cmd_download}
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
