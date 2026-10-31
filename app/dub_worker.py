"""
GPU worker for the "Translate & Dub" feature.

Runs inside the optional .venv-dub environment (VoxCPM2 + Demucs need library versions that
conflict with the main app), so it must NOT import anything from the `app` package.

Protocol: one JSON command per line on stdin, one JSON reply per line on stdout.
Every reply has "ok": true/false; progress lines have "event": "progress". Logs go to stderr.
Running in a separate process also means all VRAM is returned to the OS when the job ends
or is cancelled (the parent simply kills the process).

Commands:
  {"cmd": "separate", "input": "orig.wav", "out_dir": "..."}
      -> {"ok": true, "vocals": "...", "background": "...", "sr": 44100}
  {"cmd": "tts", "items": [{"id", "text", "lang", "ref", "out"}], "cfg_value"}
      -> progress per item, then {"ok": true, "results": [{"id", "out", "duration"}]}
  {"cmd": "load"}
      -> loads VoxCPM2 now (and drops Demucs), so the first "tts" batch starts at once
  {"cmd": "download", "models": ["voxcpm2", "demucs"]}
      -> fetches the weights into HF_HOME without loading them on the GPU
  {"cmd": "quit"}
"""

import json
import os
import sys
import time
import traceback
from pathlib import Path

_stdout = sys.stdout  # the protocol's pipe (main() sends library output to stderr instead)


def _reply(obj) -> None:
    _stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    _stdout.flush()


def _device() -> str:
    import torch

    return "cuda" if torch.cuda.is_available() else "cpu"


# --- Demucs: split voice from music/ambience (MIT) ---
_demucs_model = None


def _load_demucs():
    """demucs get_model("htdemucs") from the fixed commit: the same steps as demucs.hf.get_hf_model
    (demucs 4.1), which always takes main."""
    import yaml
    from demucs.apply import BagOfModels
    from demucs.hf import load_safetensors_model
    from huggingface_hub import hf_hub_download

    with open(hf_hub_download("adefossez/HTDemucs", "htdemucs.yaml", revision=DEMUCS_REVISION)) as f:
        bag = yaml.safe_load(f)
    models = [load_safetensors_model(hf_hub_download("adefossez/HTDemucs", f"{sig}.safetensors", revision=DEMUCS_REVISION))
              for sig in bag["models"]]
    model = BagOfModels(models, bag.get("weights"), bag.get("segment"))
    model.eval()
    return model


def cmd_separate(req):
    global _demucs_model
    import numpy as np
    import soundfile as sf
    import torch
    from demucs.apply import apply_model

    dev = _device()
    if _demucs_model is None:
        _demucs_model = _load_demucs()
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


# --- VoxCPM2: zero-shot voice cloning TTS (OpenBMB, Apache-2.0, 30 languages incl. ru/en) ---
_tts_model = None
# The weights from a fixed commit (bench-report/tts-candidates.md); the package is voxcpm 2.0.3
VOXCPM_REPO = "openbmb/VoxCPM2"
VOXCPM_REVISION = "32279effe8c19989596f05d353d1447f51d9e915"
# What VoxCPM2Model.from_local reads: config, tokenizer, model.safetensors and the AudioVAE
_VOXCPM_FILES = ["*.json", "*.safetensors", "*.pth", "*.model", "*.txt"]
# Demucs weights from a fixed commit too (the code is MIT; the card lost its license line on
# 2026-08-31, so a later commit is taken only after checking it)
DEMUCS_REVISION = "cbc8a9b1a87023b7fd74e7b3412e6321c0eab003"


def _voxcpm_dir() -> str:
    from huggingface_hub import snapshot_download

    return snapshot_download(repo_id=VOXCPM_REPO, repo_type="model", revision=VOXCPM_REVISION,
                             allow_patterns=_VOXCPM_FILES, token=os.getenv("HF_TOKEN"))


def _ensure_tts():
    global _tts_model, _demucs_model
    import torch

    dev = _device()
    if _demucs_model is not None:
        _demucs_model = None
        if dev == "cuda":
            torch.cuda.empty_cache()
    if _tts_model is None:
        from voxcpm import VoxCPM

        t0 = time.time()
        # optimize=False: torch.compile needs Triton, which the Windows builds of torch do not have;
        # load_denoiser=False: the reference is cut from Demucs' clean vocals already
        _tts_model = VoxCPM(voxcpm_model_path=_voxcpm_dir(), zipenhancer_model_path=None, enable_denoiser=False,
                            optimize=False, device=dev)
        print(f"[dub_worker] VoxCPM2 loaded in {time.time() - t0:.1f}s on {dev}", file=sys.stderr)
    return _tts_model


def cmd_load(req):
    _ensure_tts()
    return {"ok": True}


_watermarker = None


def _watermark(wav, sr: int):
    """Perth's inaudible watermark (MIT) on every synthetic phrase, as Chatterbox did itself: the
    VoxCPM2 card asks that generated speech is marked (docs/dubbing.md, consent and marking)."""
    global _watermarker
    import perth

    if _watermarker is None:
        _watermarker = perth.PerthImplicitWatermarker()
    return _watermarker.apply_watermark(wav, sample_rate=sr)


# Russian years and dates take an ordinal in the case the next word asks for: «в 2026 году» is
# «в две тысячи двадцать шестом году», «3 марта» is «третьего марта» (num2words' cardinal is wrong there)
_RU_YEAR_CASE = {"год": "nominative", "года": "genitive", "году": "prepositional", "годом": "instrumental"}
_RU_MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября",
              "октября", "ноября", "декабря")


def _ru_ordinal_case(text: str, start: int, end: int) -> tuple:
    """(case, gender) of a Russian ordinal for the number at text[start:end]; ("", "") for a cardinal."""
    import re

    after = re.match(r"\s+(\w+)", text[end:])
    word = after.group(1).lower() if after else ""
    value = int(text[start:end])
    if word in _RU_MONTHS:
        return ("genitive", "n") if 1 <= value <= 31 else ("", "")  # «третьего (числа) марта»
    case = _RU_YEAR_CASE.get(word, "")
    if case in ("nominative", "genitive", "instrumental") and not 1000 <= value <= 2999:
        return "", ""  # a count of years: «3 года», «1 год» stay cardinal; only «в 40 году» is a year
    if case == "prepositional":
        before = re.search(r"(\w+)\s+$", text[:start])
        if before and before.group(1).lower() in ("к", "по"):  # «к 2026 году»
            case = "dative"
    return case, "m"


def _spell_numbers(text: str, lang: str) -> str:
    """Numbers as words in the target language (num2words, LGPL): read as the language says them,
    not digit by digit or in another language. Left as they are where num2words has no language."""
    import re

    try:
        from num2words import num2words
    except ImportError:
        return text

    def one(m):
        raw = m.group(0)
        try:
            case, gender = _ru_ordinal_case(m.string, m.start(), m.end()) if lang == "ru" and raw.isdigit() else ("", "")
            if case:
                return num2words(int(raw), lang="ru", to="ordinal", case=case, gender=gender)
            if re.fullmatch(r"\d{1,2}:\d{2}", raw):  # a time of day: 14:30
                h, mnt = (int(x) for x in raw.split(":"))
                if not mnt:
                    return num2words(h, lang=lang)
                zero = ("oh " if lang == "en" else num2words(0, lang=lang) + " ") if mnt < 10 else ""
                return f"{num2words(h, lang=lang)} {zero}{num2words(mnt, lang=lang)}"
            value = float(raw.replace(",", ".")) if re.search(r"[.,]\d", raw) else int(raw)
            return num2words(value, lang=lang)
        except (NotImplementedError, ValueError, OverflowError, KeyError):
            return raw

    return re.sub(r"\d{1,2}:\d{2}|\d+(?:[.,]\d+)?", one, text)


# Voice of each reference clip: encoded once per process, not per phrase
_prompt_cache = {}


def _synthesize(model, text: str, ref: str, cfg_value: float):
    """One phrase in the voice of `ref`. The same steps as VoxCPM.generate (voxcpm 2.0.3), with the
    voice of the reference kept between phrases; retry_badcase caps a run-on generation by the text
    length and tries again."""
    import torch

    from voxcpm.model.utils import next_and_close

    tts = model.tts_model
    if ref not in _prompt_cache:
        _prompt_cache[ref] = tts.build_prompt_cache(prompt_text=None, prompt_wav_path=None, reference_wav_path=ref)
    with torch.inference_mode():
        wav, _, _ = next_and_close(tts._generate_with_prompt_cache(
            target_text=" ".join(text.split()), prompt_cache=_prompt_cache[ref], min_len=2, max_len=2000,
            inference_timesteps=10, cfg_value=cfg_value, retry_badcase=True, retry_badcase_max_times=3,
            retry_badcase_ratio_threshold=6.0, streaming=False,
        ))
    return wav.squeeze(0).float().cpu().numpy()


def cmd_tts(req):
    import numpy as np
    import soundfile as sf
    import torch

    dev = _device()
    model = _ensure_tts()
    sr = int(model.tts_model.sample_rate)
    items = req["items"]
    cfg_value = float(req.get("cfg_value", 2.0))
    results = []
    for i, it in enumerate(items):
        t0 = time.time()
        text = _spell_numbers(it["text"], it["lang"])
        # One phrase must never fail a whole dub: retry once, then leave it silent
        wav, error = None, ""
        for _attempt in range(2):
            try:
                wav = _watermark(_synthesize(model, text, it["ref"], cfg_value), sr)
                break
            except Exception as e:  # noqa: BLE001
                error = f"{type(e).__name__}: {e}"[:300]
                print(f"[dub_worker] phrase {it['id']} failed: {error}", file=sys.stderr)
                if dev == "cuda":
                    torch.cuda.empty_cache()
        if wav is None:
            wav = np.zeros(int(0.3 * sr), dtype=np.float32)
        out = Path(it["out"])
        out.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(out), wav, sr)
        dur = len(wav) / sr
        res = {"id": it["id"], "out": str(out), "duration": round(dur, 3), "gen_sec": round(time.time() - t0, 2)}
        if error and not np.abs(wav).max():
            res["failed"] = error
        results.append(res)
        _reply({"ok": True, "event": "progress", "done": i + 1, "total": len(items)})
    vram = torch.cuda.max_memory_allocated() / 2**30 if dev == "cuda" else 0.0
    return {"ok": True, "results": results, "sr": sr, "peak_vram_gb": round(vram, 2)}


def cmd_download(req):
    from huggingface_hub import hf_hub_download

    done = []
    for name in req.get("models") or []:
        if name == "voxcpm2":
            _voxcpm_dir()
        elif name == "demucs":
            import yaml

            with open(hf_hub_download("adefossez/HTDemucs", "htdemucs.yaml", revision=DEMUCS_REVISION)) as f:
                bag = yaml.safe_load(f)
            for sig in bag["models"]:
                hf_hub_download("adefossez/HTDemucs", f"{sig}.safetensors", revision=DEMUCS_REVISION)
        else:
            raise RuntimeError(f"unknown model {name!r}")
        done.append(name)
        _reply({"ok": True, "event": "progress", "done": len(done), "total": len(req.get("models") or [])})
    return {"ok": True, "downloaded": done}


def main() -> None:
    global _stdout
    # Started with `python -I`, so PYTHONIOENCODING is ignored: force UTF-8 pipes explicitly
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    _stdout = sys.stdout
    # Libraries print to stdout; keep it clean for the protocol
    sys.stdout = sys.stderr
    handlers = {"separate": cmd_separate, "tts": cmd_tts, "load": cmd_load, "download": cmd_download}
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
