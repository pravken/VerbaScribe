import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from app.hardware import clear_vram, get_hardware_info, register_cuda_dlls

register_cuda_dlls()

from faster_whisper import WhisperModel

from app.audio_utils import extract_audio_16k_mono, format_timestamp, get_media_duration
from app.database import (
    AUDIO_DIR,
    MODELS_DIR,
    get_all_settings,
    get_dictionary_hotwords,
    get_meeting,
    save_meeting_results,
    save_segments_backup,
    update_meeting_progress,
)
from app.diarizer import diarize_and_merge_segments
from app.ollama_client import generate_meeting_summary, get_ollama_status
from app.text_utils import (
    clean_filler_words_in_segments,
    is_hallucination,
    is_low_quality_segment,
)

# Single worker queue ensures batch jobs run one after another without VRAM collisions
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="whisper_worker")

# Jobs that are queued or running, and jobs the user asked to cancel
_jobs_lock = threading.Lock()
_active_jobs: Set[int] = set()
_cancel_requested: Set[int] = set()


class JobCancelled(Exception):
    pass


def is_job_active(meeting_id: int) -> bool:
    with _jobs_lock:
        return meeting_id in _active_jobs


def request_cancel(meeting_id: int) -> bool:
    """Marks a queued/running job for cancellation. It stops at the next checkpoint."""
    with _jobs_lock:
        if meeting_id not in _active_jobs:
            return False
        _cancel_requested.add(meeting_id)
        return True


def _check_cancel(meeting_id: int) -> None:
    with _jobs_lock:
        if meeting_id in _cancel_requested:
            raise JobCancelled()


def clean_meeting_transcript(meeting_id: int) -> Dict[str, Any]:
    m = get_meeting(meeting_id)
    if not m:
        raise ValueError("Встреча не найдена")
    segments = m.get("segments") or []
    cleaned, removed = clean_filler_words_in_segments(segments)
    if removed:
        save_segments_backup(meeting_id, segments)
        save_meeting_results(meeting_id, segments=cleaned)
    updated = get_meeting(meeting_id)
    return {"meeting": updated, "removed_count": removed}


def enqueue_meeting_job(meeting_id: int) -> bool:
    """Queues a job unless the same recording is already queued or being processed."""
    with _jobs_lock:
        if meeting_id in _active_jobs:
            return False
        _active_jobs.add(meeting_id)
        _cancel_requested.discard(meeting_id)
    _executor.submit(_run_job, meeting_id)
    return True


def _run_job(meeting_id: int) -> None:
    try:
        _process_meeting_job(meeting_id)
    finally:
        with _jobs_lock:
            _active_jobs.discard(meeting_id)
            _cancel_requested.discard(meeting_id)


def _load_whisper_with_fallback(model_name: str, device: str, compute_type: str) -> WhisperModel:
    """
    Loads faster-whisper model with automatic fallback across compute_types and CPU
    so the program works reliably on ANY user's GPU or CPU.
    """
    from app.model_manager import get_whisper_model_path

    register_cuda_dlls()
    whisper_cache = MODELS_DIR / "whisper"
    whisper_cache.mkdir(parents=True, exist_ok=True)
    model_or_path = get_whisper_model_path(model_name)

    attempts = [
        (device, compute_type),
    ]
    if device == "cuda":
        if compute_type != "int8_float16":
            attempts.append(("cuda", "int8_float16"))
        if compute_type != "int8":
            attempts.append(("cuda", "int8"))
        attempts.append(("cpu", "int8"))
    elif compute_type != "int8":
        attempts.append(("cpu", "int8"))

    last_err = None
    for dev, ctype in attempts:
        try:
            return WhisperModel(
                model_or_path,
                device=dev,
                compute_type=ctype,
                download_root=str(whisper_cache),
            )
        except Exception as e:
            last_err = e
            continue

    raise RuntimeError(f"Не удалось инициализировать модель Whisper ({model_name}): {last_err}")


def _process_meeting_job(meeting_id: int) -> None:
    meeting = get_meeting(meeting_id)
    if not meeting:
        return
    whisper_model = None

    settings = get_all_settings()
    hw = get_hardware_info()

    media_path = Path(meeting["media_path"])
    if not media_path.exists():
        update_meeting_progress(
            meeting_id,
            status="failed",
            progress=0,
            stage_text="Ошибка: исходный медиафайл не найден",
            error_message=f"Файл не найден: {media_path}",
        )
        return

    try:
        # 1. Extract 16kHz mono WAV + Studio Voice Denoising & Loudness Normalization
        enhance_audio = settings.get("audio_enhance", "true").lower() == "true"
        update_meeting_progress(
            meeting_id,
            status="extracting_audio",
            progress=4,
            stage_text=(
                "Студийная очистка голоса и подавление шума (FFmpeg Denoise + Loudnorm)..."
                if enhance_audio
                else "Извлечение аудиодорожки (FFmpeg 16 kHz mono)..."
            ),
            error_message="",
        )

        wav_path = AUDIO_DIR / f"meeting_{meeting_id}.wav"
        extract_audio_16k_mono(media_path, wav_path, enhance_audio=enhance_audio)
        duration = get_media_duration(wav_path) or get_media_duration(media_path)

        update_meeting_progress(
            meeting_id,
            progress=10,
            stage_text=f"Звук очищен и выровнен ({format_timestamp(duration)}). Загрузка модели Whisper...",
            duration=duration,
            audio_path=str(wav_path),
        )

        # 2. Load Whisper model
        from app.model_manager import format_whisper_model_name

        model_name = meeting.get("model_name") or settings.get("whisper_model") or "large-v3-turbo"
        pretty_whisper = format_whisper_model_name(model_name)
        device = settings.get("device") or hw["recommended_device"]
        compute_type = settings.get("compute_type") or hw["recommended_compute_type"]
        lang_setting = meeting.get("language") or settings.get("language") or "auto"
        language = None if lang_setting in ("auto", "", None) else lang_setting
        vad_enabled = settings.get("vad_filter", "true").lower() == "true"

        _check_cancel(meeting_id)
        whisper_model = _load_whisper_with_fallback(model_name, device, compute_type)

        # `hotwords` is applied to every 30s window; `initial_prompt` would only affect the first
        # window because condition_on_previous_text is disabled.
        hotwords = get_dictionary_hotwords()
        update_meeting_progress(
            meeting_id,
            status="transcribing",
            progress=15,
            stage_text=f"Распознавание речи — {pretty_whisper}",
        )

        segments_iter, info = whisper_model.transcribe(
            str(wav_path),
            language=language,
            hotwords=hotwords or None,
            word_timestamps=True,
            vad_filter=vad_enabled,
            vad_parameters=dict(min_silence_duration_ms=450, speech_pad_ms=250),
            condition_on_previous_text=False,
        )

        detected_lang = getattr(info, "language", None) or (language or "ru")
        total_dur = max(1.0, float(duration or getattr(info, "duration", 1.0) or 1.0))

        raw_segments: List[Dict[str, Any]] = []
        for seg in segments_iter:
            _check_cancel(meeting_id)
            text = (seg.text or "").strip()
            if is_hallucination(text) or is_low_quality_segment(
                getattr(seg, "no_speech_prob", None),
                getattr(seg, "avg_logprob", None),
                getattr(seg, "compression_ratio", None),
            ):
                continue

            words_list = []
            if seg.words:
                for w in seg.words:
                    words_list.append(
                        {
                            "start": round(float(w.start), 2),
                            "end": round(float(w.end), 2),
                            "word": w.word,
                            "prob": round(float(getattr(w, "probability", 1.0)), 2),
                        }
                    )

            raw_segments.append(
                {
                    "start": round(float(seg.start), 2),
                    "end": round(float(seg.end), 2),
                    "text": text,
                    "words": words_list,
                }
            )

            # Update progress between 16% and 65%
            ratio = min(1.0, float(seg.end) / total_dur)
            pct = 16 + int(49 * ratio)
            update_meeting_progress(
                meeting_id,
                progress=pct,
                stage_text=(
                    f"Транскрибация: {format_timestamp(seg.end)} из {format_timestamp(total_dur)} "
                    f"({int(ratio * 100)}%)"
                ),
            )

        # Explicitly unload Whisper from VRAM before Diarization & LLM
        whisper_model = None
        clear_vram()
        _check_cancel(meeting_id)

        # 3. Speaker Diarization (token-free ECAPA-TDNN clustering)
        diarization_enabled = settings.get("diarization_enabled", "true").lower() == "true"
        num_speakers_hint = int(meeting.get("num_speakers_hint") or settings.get("num_speakers") or 0)

        update_meeting_progress(
            meeting_id,
            status="diarizing",
            progress=68,
            stage_text="Разделение участников встречи по голосам...",
        )

        def _diar_cb(pct: int, msg: str) -> None:
            update_meeting_progress(meeting_id, progress=pct, stage_text=msg)

        speakers_meta, merged_segments = diarize_and_merge_segments(
            wav_path=wav_path,
            raw_segments=raw_segments,
            device=device,
            num_speakers_hint=num_speakers_hint,
            diarization_enabled=diarization_enabled,
            progress_callback=_diar_cb,
        )

        _check_cancel(meeting_id)
        if settings.get("clean_fillers", "true").lower() == "true":
            cleaned_segments, removed = clean_filler_words_in_segments(merged_segments)
            if removed:
                # Keep the unmodified recognition result so the cleanup can be undone
                save_segments_backup(meeting_id, merged_segments)
            merged_segments = cleaned_segments

        save_meeting_results(
            meeting_id,
            speakers=speakers_meta,
            segments=merged_segments,
            language=detected_lang,
        )

        clear_vram()

        # 4. Automatic AI Summary via local Ollama (if enabled and Ollama has a model)
        auto_summary = settings.get("auto_summary", "true").lower() == "true"
        summaries = meeting.get("summaries") or {}

        if auto_summary and merged_segments:
            ollama_st = get_ollama_status()
            if ollama_st["running"] and ollama_st["models"]:
                update_meeting_progress(
                    meeting_id,
                    status="summarizing",
                    progress=90,
                    stage_text="Генерация AI-протокола встречи через локальную Ollama...",
                )
                try:
                    summary_md = generate_meeting_summary(
                        segments=merged_segments,
                        speakers=speakers_meta,
                        template_key="protocol",
                        answer_lang=detected_lang,
                    )
                    summaries["protocol"] = summary_md
                    save_meeting_results(meeting_id, summaries=summaries)
                except Exception as sum_err:
                    summaries["error"] = str(sum_err)
                    save_meeting_results(meeting_id, summaries=summaries)

        clear_vram()

        update_meeting_progress(
            meeting_id,
            status="completed",
            progress=100,
            stage_text=f"Готово! Участников: {len(speakers_meta)}, реплик: {len(merged_segments)}",
        )

    except JobCancelled:
        whisper_model = None
        clear_vram()
        update_meeting_progress(
            meeting_id,
            status="cancelled",
            stage_text="Обработка отменена",
            error_message="",
        )
    except Exception as e:
        whisper_model = None
        clear_vram()
        tb = traceback.format_exc()
        update_meeting_progress(
            meeting_id,
            status="failed",
            stage_text=f"Ошибка: {str(e)[:140]}",
            error_message=f"{e}\n\n{tb[-800:]}",
        )
