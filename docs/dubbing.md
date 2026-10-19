# Translate & Dub — plan, estimates and prototype notes

Status: **prototype** (branch `feat/translate-dub`). Works end to end on a completed recording; not yet
tuned for long recordings or many speakers.

## Goal

Take a finished recording (segments with word timestamps + diarized speakers), translate every phrase
with the local Ollama model, re-voice it in the original speaker's voice, keep the background
(music/ambience), and write a new, clearly labelled video/audio file. Everything local, free, and
permissively licensed.

## Model choice (licenses verified 2026-10-18)

| Role | Choice | License | Notes |
| :--- | :--- | :--- | :--- |
| Voice-cloning TTS | **Chatterbox Multilingual** (Resemble AI), `chatterbox-tts==0.1.7`, checkpoint `t3_mtl23ls_v2` | MIT (code and weights) | 23 languages incl. **ru** and **en**; zero-shot cloning from ~10 s; every output carries an imperceptible **Perth watermark** |
| Fallback TTS (rejected) | OpenVoice V2 | MIT | V2 natively supports only EN/ES/FR/ZH/JA/KO — **no Russian**, so not usable as a drop-in fallback |
| Voice / background separation | **Demucs** `htdemucs` (`demucs==4.1.0`) | MIT | background = mix − vocals stem; weights come from the `adefossez/HTDemucs` Hugging Face repo, which has no license tag of its own — they are the Demucs project's released models (MIT repository) |
| Translation | existing Ollama client (Qwen 2.5 / Mistral) | Apache 2.0 | 7B+ recommended; 1.5B works but is literal and sometimes drops nuance |
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
                             │     tts:      Chatterbox, conditioning cached per speaker reference
                             ├─ speaker refs: ~10 s cut from each speaker's longest phrases in the clean vocals stem
                             ├─ measure: clips needing > 1.35× speed-up are re-translated shorter once and re-synthesized
                             ├─ fit: trim silence → atempo (≤1.6×) → hard trim with 80 ms fade if still too long
                             ├─ mix: background + voice; per-phrase gain matched to the original vocal RMS; peak-limited
                             └─ mux: video copied, AI-dub audio as default track + original audio as 2nd track, SRT of the translation
```

Why a separate process / venv:

* `chatterbox-tts` pins `numpy<2`, `transformers==5.2.0`, `diffusers==0.29.0` — incompatible with the main app's env.
* Cancel = kill the process: immediate, and all VRAM goes back to the driver.
* The feature stays optional: users who never dub never download ~6 GB of packages and ~3 GB of weights.

Paths: outputs go to `<data folder>/dubs` (`app.paths.DATA_DIR`, default `%LOCALAPPDATA%\VerbaScribe`); dub paths are stored relative to the data folder so moving it keeps them working. `.venv-dub` sits next to the program (`APP_DIR`), weights in `MODELS_DIR/dub`. The worker only receives absolute paths.

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
  and Chatterbox's built-in Perth audio watermark (detectable with `perth.PerthImplicitWatermarker().get_watermark`).

## VRAM / speed estimates

Measured on the prototype clip (recording #3: 33.6 s, Russian, 1 speaker, 5 phrases → English/German),
RTX 3090 24 GB, Ollama `qwen2.5:1.5b`:

| Stage | Cold run (first job) | Warm run | Notes |
| :--- | ---: | ---: | :--- |
| Phrase split + audio extract | 0.5 s | 0.1 s | |
| Translation (Ollama) | 5 s | 3.6 s | up to ~60 s when Ollama has to load the model first |
| Demucs separation (incl. model load) | 7.6 s | 4.8 s | ~1 GB VRAM; ≈ 40–60× real time once loaded |
| Chatterbox load + first phrase | 61 s | 19 s | cold = loading 3 GB of weights from disk the first time |
| Chatterbox synthesis | 36 s | 31 s | ≈ 1.1 s compute per second of generated speech (RTF ≈ 1.1) |
| Re-translate + re-synthesize long phrases | – | 7.5 s | 1 of 5 phrases needed it in the warm run |
| Time-fit, mix, mux | 1.4 s | 1.4 s | FFmpeg + numpy, CPU |
| **Total** | **94.7 s** | **56.5 s** | |

VRAM (never used at the same time — the LLM is unloaded with `keep_alive=0` before synthesis, and Whisper is not
loaded during a dub):

| Component | VRAM |
| :--- | :--- |
| Chatterbox Multilingual (0.5B T3 + S3Gen, fp32) | **3.6 GB peak** allocated by torch (≈ 4.5 GB incl. CUDA context) |
| Demucs htdemucs | ≈ 1 GB (segment-wise processing) |
| Ollama translation model | 1.5B ≈ 1.5 GB · 7B ≈ 5.5 GB · 14B ≈ 10 GB |
| **Peak for the whole job** | max(LLM, ~4.5 GB) → a 6 GB GPU works with a ≤ 1.5B LLM, 8 GB with a 7B LLM |

Extrapolation for longer recordings (GPU, warm): synthesis dominates at ~65–70 s per minute of speech; Demucs adds
~5 s and translation ~5–10 s (7B) per minute; plus ~25 s fixed model loading. So **≈ 1.3–1.5 minutes of processing per
minute of speech**, i.e. a 1-hour meeting ≈ 80–90 minutes. On CPU, Chatterbox is autoregressive and is expected to be
10–20× slower (not measured) — not practical beyond short clips.

Disk: `.venv-dub` ≈ 5.2 GB, weights in `models/dub` ≈ 3.2 GB.

Quality checks on the result:

* Re-transcribing the English dub track with faster-whisper `large-v3-turbo` detects English (p = 0.99) and returns the
  translated sentences nearly word for word → the speech is intelligible.
* `perth.PerthImplicitWatermarker().get_watermark()` returns **1.0** on the final AAC dub track (after mixing with the
  background and encoding) and 0.0 on the original audio.
* Translation quality with the 1.5B model is the weakest link: it drops or invents details ("сосисочки" → "no
  sausages"; German output was noticeably worse). Use a 7B+ model for anything beyond a demo.

## Limitations (prototype)

* **No lip-sync.** The video is untouched; only the audio track changes.
* Timing is phrase-level: clips are sped up (max 1.6×, artifacts start to be audible above ~1.3×) or trimmed with
  a fade; too-short clips are not slowed down (leaves a pause instead).
* Overlapping speech is voiced one phrase at a time (phrases can overlap in the mix; diarization errors become
  wrong voices).
* Emotion/prosody of the original is only loosely carried over (Chatterbox `exaggeration` is global).
* Cross-language cloning keeps some source-language accent; `dub_cfg_weight` (default 0.3) trades accent for pacing.
* Translation quality depends on the Ollama model; small models (1.5B) may mistranslate idioms.
* Demucs leaves some vocal bleed in the background on noisy recordings; the original voice can be faintly audible.
* Speakers with < ~3 s of total speech get a weak clone.
* Windows/NVIDIA tested only (RTX 3090). CPU works in principle but is very slow (Chatterbox is autoregressive).

## Running without the UI

```bash
.venv\Scripts\python.exe -m tools.dub_cli <recording_id> <target_lang>
```

Prints per-stage timings and stores the result like a UI job.

## Settings (Settings → Speech synthesis; the noise options also in the Dub window)

| Key | Default | Meaning |
| :--- | :--- | :--- |
| `dub_cfg_weight` | `0.3` | Chatterbox CFG weight (lower = less accent transfer from the reference) |
| `dub_exaggeration` | `0.5` | Chatterbox emotion exaggeration |
| `dub_stretch` | `atempo` | `rubberband` uses FFmpeg's rubberband filter if available |
| `dub_keep_original_track` | `true` | keep the original audio as a second track |
| `dub_keep_workdir` | `false` | keep intermediate WAVs in `<data folder>/dubs/work_*` for debugging (no UI) |
| `dub_tts_workers` | `auto` | parallel Chatterbox processes (`auto` = by free VRAM, or `1`–`3`) |
| `dub_room_tone` | `true` | add the recording's own noise floor (from the separated voice between phrases) under the synthetic voice |
| `dub_denoise` | `false` | "Remove noise": FFmpeg `afftdn` on the dub background and on the original track in the dub file; room tone is then off |

## Next steps

1. Run TTS in batches / with `torch.compile` to raise GPU utilisation; stream phrases to the UI for preview.
2. Per-phrase review/edit of the translation before synthesis (reuse the transcript editor).
3. Duration-aware translation loop (ask for N syllables, measure with a cheap phoneme counter before TTS).
4. Optional per-speaker reference picker in the "Speakers" tab.
