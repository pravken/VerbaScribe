# Translate & Dub — plan, estimates and prototype notes

Status: part of the app (optional: installed on request into `.venv-dub`). Works end to end on a
completed recording; long recordings need much memory (the app checks it before a dub starts).

## Goal

Take a finished recording (segments with word timestamps + diarized speakers), translate every phrase
with the local Ollama model, re-voice it in the original speaker's voice, keep the background
(music/ambience), and write a new, clearly labelled video/audio file. Everything local, free, and
permissively licensed.

## Model choice (licenses verified 2026-10-18; the voice model since 0.6.0, 2026-10-31)

| Role | Choice | License | Notes |
| :--- | :--- | :--- | :--- |
| Voice-cloning TTS | **VoxCPM2** (OpenBMB), `voxcpm==2.0.3`, `openbmb/VoxCPM2` at `32279eff` | Apache 2.0 (code and weights) | 30 languages incl. **ru** and **en** (23 offered here); zero-shot cloning from ~10 s; correct Russian stress; chosen over Chatterbox V2/V3, Qwen3-TTS and CosyVoice3 in [bench-report/tts-candidates.md](../bench-report/tts-candidates.md); every phrase gets the imperceptible **Perth watermark** (`resemble-perth`, MIT) |
| Earlier TTS (before 0.6.0) | Chatterbox Multilingual V2 (Resemble AI) | MIT | 16 of 92 phrases broke in the bench against 2 for VoxCPM2; its environment pulled in pykakasi (GPL v3) |
| Numbers as words | `num2words` | LGPL 2.1 | in the worker, before synthesis, in the target language |
| Fallback TTS (rejected) | OpenVoice V2 | MIT | V2 natively supports only EN/ES/FR/ZH/JA/KO — **no Russian**, so not usable as a drop-in fallback |
| Voice / background separation | **Demucs** `htdemucs` (`demucs==4.1.0`) | MIT | background = mix − vocals stem; weights come from the `adefossez/HTDemucs` Hugging Face repo, which has no license tag of its own — they are the Demucs project's released models (MIT repository) |
| Translation | local Ollama, the model chosen in Settings (Qwen3.5 4B / Qwen3 8B by default) | Apache 2.0 | 7B+ recommended; small models are literal and sometimes drop nuance |
| Time stretch | FFmpeg `atempo` | LGPL (part of FFmpeg) | `rubberband` filter is optional (`dub_stretch=rubberband`); librubberband is GPL, so it is never bundled, only used if the user's FFmpeg build has it |

Explicitly avoided (non-commercial licenses): XTTS-v2 (CPML), F5-TTS weights (CC-BY-NC), Fish Speech
(CC-BY-NC-SA), NLLB (CC-BY-NC).

## Architecture

```
UI "Dub" button ─► POST /api/meetings/{id}/dub {target_lang, consent:true}
                   └─► transcriber.enqueue_dub_job  (same single-worker queue as transcription → no VRAM collisions)
                        └─► dubbing.run_dub_job  (main venv: FFmpeg, Ollama, numpy mixing)
                             ├─ dub_text.build_dub_units      split segments into ≤12 s phrases (sentence ends, pauses ≥0.6 s)
                             ├─ translate_units (Ollama)      numbered batches of 12, "same spoken length"; LLM unloaded afterwards (keep_alive=0)
                             ├─ DubWorker  ──stdin/stdout JSON──►  app/dub_worker.py in .venv-dub (separate process)
                             │     separate: Demucs → vocals.wav, background.wav
                             │     tts:      VoxCPM2, the voice prompt cached per speaker reference
                             ├─ speaker refs: ~10 s cut from each speaker's longest phrases in the clean vocals stem
                             ├─ measure: clips needing > 1.35× speed-up are re-translated shorter once and re-synthesized
                             ├─ fit: trim silence → atempo (≤1.6×) → hard trim with 80 ms fade if still too long
                             ├─ mix: background + voice; per-phrase gain matched to the original vocal RMS; peak-limited
                             └─ mux: video copied, AI-dub audio as default track + original audio as 2nd track, SRT of the translation
```

Why a separate process / venv:

* `voxcpm` needs its own `transformers` and `librosa`; its PyPI dependency list also pulls in a demo and training stack (gradio, modelscope, datasets, funasr), so it is installed with `--no-deps` and its runtime deps are listed in `requirements-dub.in`.
* Cancel = kill the process: immediate, and all VRAM goes back to the driver.
* The feature stays optional: users who never dub never download ~6 GB of packages and ~5 GB of weights.

Paths: outputs go to `<data folder>/dubs` (`app.paths.DATA_DIR`, default `%LOCALAPPDATA%\Verbaliox`); dub paths are stored relative to the data folder so moving it keeps them working. `.venv-dub` sits next to the program (`APP_DIR`), weights in `MODELS_DIR/dub`. The worker only receives absolute paths.

Before the worker starts, a Whisper model still cached from a previous queued job is released (`release_whisper_model()`). Progress goes through `update_meeting_progress`, so it reaches the UI over SSE like transcription progress.

Job integration: the recording's status becomes `dubbing` while the job runs (the existing progress banner and
**Cancel** button work unchanged); afterwards it always returns to `completed` because the transcript is still
valid. Success/error is reported in `stage_text`; results are stored in the new `dubs_json` column (one entry per
target language, a newer dub replaces the older file). A dub interrupted by an app restart is dropped.

## Consent and marking of generated files

* The UI shows a voice-cloning warning and requires an explicit consent checkbox; the API rejects requests
  without `consent: true`.
* Output files are marked three ways: `.AI-dub.<lang>.` in the file name; container metadata
  (`title … [AI dub EN]`, `comment=AI-GENERATED DUB …`, audio-track title "AI dub (EN) - synthetic cloned voice");
  and the Perth audio watermark the worker puts on every phrase (detectable with `perth.PerthImplicitWatermarker().get_watermark`).

## VRAM / speed estimates

Measured with VoxCPM2 (voxcpm 2.0.3, torch 2.11 + cu126, `optimize=False`) on RTX 3090 24 GB, Ollama `qwen3:8b`
for translation, one synthesis process ("Auto" chose 1: other programs held 10–17 GB of the card). Five dubs of two
short recordings, 2026-10-31:

| Dub | Recording | Phrases | Speech in the dub | Synthesis | Whole job | Failed phrases |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: |
| #68 ru → en | 76.5 s | 18 | 51 s | 78 s | 94 s | 0 |
| #68 ru → de | 76.5 s | 18 | 51 s | 79 s | 83 s | 0 |
| #68 ru → es | 76.5 s | 18 | 51 s | 77 s | 82 s | 0 |
| #53 en → ru | 66.7 s | 13 | 62 s | 104 s | 122 s | 0 |
| #53 en → ja | 66.7 s | 13 | 62 s | 126 s | 168 s | 0 |

* **Synthesis** includes loading VoxCPM2 (21–26 s from disk, the first phrase ≈ 24 s with warm-up). Generation itself
  runs at RTF ≈ 1.1–1.2 for English, German and Spanish, ≈ 1.4 for Russian; Japanese came out at ≈ 2.1 because its
  long phrases were shortened and synthesized again (37 s of the job).
* Demucs separation 5–7 s and translation 7–10 s per job.
* **≈ 1.1–1.8 minutes of processing per minute of recording** (2.5 for Japanese here), plus the fixed ~25 s model load;
  a 1-hour meeting ≈ 70–110 minutes on one process. On CPU VoxCPM2 is not practical (not measured).
* `optimize=True` (torch.compile) needs Triton, which the Windows builds of torch do not ship: it stays off.

VRAM (never used at the same time — the LLM is unloaded with `keep_alive=0` before synthesis, and Whisper is not
loaded during a dub):

| Component | VRAM |
| :--- | :--- |
| VoxCPM2 | **5.9 GB peak** allocated by torch per synthesis process; ≈ 6.5 GB on the card with the CUDA context |
| Demucs htdemucs | ≈ 1 GB (segment-wise processing) |
| Ollama translation model | 1.5B ≈ 1.5 GB · 7B ≈ 5.5 GB · 14B ≈ 10 GB |
| **Peak for the whole job** | max(LLM, ~7 GB) → an 8 GB GPU works with a ≤ 1.5B LLM, 12 GB with a 7B LLM |

"Auto" synthesis processes: `TTS_WORKER_VRAM_GB = 7.5` per process and 1 GB kept free, so a free 24 GB card
(≈ 23 GB free) runs 2 processes and 16 GB free runs 2 (15 // 7.5); with 8–15 GB free it runs 1.

Disk: `.venv-dub` ≈ 4.5 GB, weights in `models/dub` ≈ 5.0 GB (VoxCPM2 4.96 GB, Demucs 84 MB).

Quality checks on the result:

* The owner listened to all five VoxCPM2 dubs above and found no problems with the voice, stress or phrase endings.
  `_cut_trailing_artifacts` (app/dubbing.py) still trims a tail now and then (1 phrase in es, 4 in ja, none in en, de,
  ru), so it stays. Numbers are read as words in the target
  language; Russian years and dates take the ordinal in the right case («в 2026 году» → «в две тысячи двадцать шестом
  году», «3 марта» → «третьего марта»).
* `perth.PerthImplicitWatermarker().get_watermark()` returns **1.0** on the final AAC dub tracks (en, ru, ja: after
  mixing with the background and encoding) and 0.0 on the original audio.
* Translation quality with the 1.5B model is the weakest link: it drops or invents details ("сосисочки" → "no
  sausages"; German output was noticeably worse). Use a 7B+ model for anything beyond a demo.

## Limitations (prototype)

* **No lip-sync.** The video is untouched; only the audio track changes.
* Timing is phrase-level: clips are sped up (max 1.6×, artifacts start to be audible above ~1.3×) or trimmed with
  a fade; too-short clips are not slowed down (leaves a pause instead).
* Overlapping speech is voiced one phrase at a time (phrases can overlap in the mix; diarization errors become
  wrong voices).
* Emotion/prosody of the original is only loosely carried over (the reference sets the voice, not each phrase's emotion).
* Cross-language cloning keeps some source-language accent; `dub_voice_cfg` (default 2.0) trades closeness to the reference for a freer intonation.
* Translation quality depends on the Ollama model; small models (1.5B) may mistranslate idioms.
* Demucs leaves some vocal bleed in the background on noisy recordings; the original voice can be faintly audible.
* Speakers with < ~3 s of total speech get a weak clone.
* Windows/NVIDIA tested only (RTX 3090). CPU works in principle but is very slow (VoxCPM2 is autoregressive).

## Running without the UI

```bash
.venv\Scripts\python.exe -m tools.dub_cli <recording_id> <target_lang>
```

Prints per-stage timings and stores the result like a UI job.

## Settings (Settings → Speech synthesis; the noise options also in the Dub window)

| Key | Default | Meaning |
| :--- | :--- | :--- |
| `dub_voice_cfg` | `2.0` | VoxCPM2 guidance `cfg_value`, 1–3 (higher = closer to the reference voice and manner) |
| `dub_stretch` | `atempo` | `rubberband` uses FFmpeg's rubberband filter if available |
| `dub_keep_original_track` | `true` | keep the original audio as a second track |
| `dub_keep_workdir` | `false` | keep intermediate WAVs in `<data folder>/dubs/work_*` for debugging (no UI) |
| `dub_tts_workers` | `auto` | parallel synthesis processes (`auto` = by free VRAM, or `1`–`3`) |
| `dub_room_tone` | `true` | add the recording's own noise floor (from the separated voice between phrases) under the synthetic voice |
| `dub_denoise` | `false` | "Remove noise": FFmpeg `afftdn` on the dub background and on the original track in the dub file; room tone is then off |

## Next steps

1. Run TTS in batches / with `torch.compile` to raise GPU utilisation; stream phrases to the UI for preview.
2. Per-phrase review/edit of the translation before synthesis (reuse the transcript editor).
3. Duration-aware translation loop (ask for N syllables, measure with a cheap phoneme counter before TTS).
4. Optional per-speaker reference picker in the "Speakers" tab.
