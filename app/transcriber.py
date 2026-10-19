import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from app.hardware import clear_vram, get_hardware_info, register_cuda_dlls

register_cuda_dlls()

from faster_whisper import BatchedInferencePipeline, WhisperModel

from app.audio_utils import extract_audio_16k_mono, format_timestamp, get_media_duration
from app.database import (
    AUDIO_DIR,
    MODELS_DIR,
    get_all_settings,
    get_dictionary_hotwords,
    get_meeting,
    save_meeting_dub,
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
# Mirror of the executor's FIFO for the UI: which recording is running and which are waiting
_queue: List[Dict[str, Any]] = []
_running_id: Optional[int] = None


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


def _publish_queue_changed() -> None:
    from app import events

    events.publish({"type": "queue"})


def _queue_add(meeting_id: int, kind: str, lang: Optional[str] = None) -> None:
    """Call with _jobs_lock held."""
    _queue.append({"id": meeting_id, "kind": kind, "lang": lang, "queued_at": time.time(), "started_at": None})


def _queue_start(meeting_id: int) -> None:
    global _running_id
    with _jobs_lock:
        _running_id = meeting_id
        for q in _queue:
            if q["id"] == meeting_id:
                q["started_at"] = time.time()
    _publish_queue_changed()


def _queue_finish(meeting_id: int) -> None:
    global _running_id
    with _jobs_lock:
        _queue[:] = [q for q in _queue if q["id"] != meeting_id]
        if _running_id == meeting_id:
            _running_id = None
    _publish_queue_changed()


def queue_snapshot() -> List[Dict[str, Any]]:
    """Running job first, then waiting jobs in the order they will run."""
    from app.database import list_meetings

    with _jobs_lock:
        items = [dict(q) for q in _queue]
        running = _running_id
        cancelling = set(_cancel_requested)
    meetings = {m["id"]: m for m in list_meetings()}
    items.sort(key=lambda q: 0 if q["id"] == running else 1)
    out = []
    waiting_pos = 0
    for q in items:
        m = meetings.get(q["id"])
        if not m:
            continue
        is_running = q["id"] == running
        if not is_running:
            waiting_pos += 1
        out.append(
            {
                **q,
                "title": m.get("title") or "",
                "state": "running" if is_running else "waiting",
                "position": 0 if is_running else waiting_pos,
                "cancelling": q["id"] in cancelling,
                "progress": m.get("progress", 0),
                "stage_text": m.get("stage_text", ""),
            }
        )
    return out


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
        _queue_add(meeting_id, "transcribe")
    _executor.submit(_run_job, meeting_id)
    _publish_queue_changed()
    return True


def _run_job(meeting_id: int) -> None:
    _queue_start(meeting_id)
    try:
        _process_meeting_job(meeting_id)
    finally:
        _queue_finish(meeting_id)
        with _jobs_lock:
            _active_jobs.discard(meeting_id)
            _cancel_requested.discard(meeting_id)
            queue_empty = not _active_jobs
        if queue_empty:
            release_whisper_model()


def enqueue_dub_job(meeting_id: int, target_lang: str) -> bool:
    """Queues a translate-and-dub job in the same single worker, so it never shares VRAM with a transcription."""
    with _jobs_lock:
        if meeting_id in _active_jobs:
            return False
        _active_jobs.add(meeting_id)
        _cancel_requested.discard(meeting_id)
        _queue_add(meeting_id, "dub", target_lang)
    update_meeting_progress(
        meeting_id, status="dubbing", progress=0, stage_text="Дубляж в очереди...", error_message=""
    )
    _executor.submit(_run_dub_job, meeting_id, target_lang)
    _publish_queue_changed()
    return True


def _run_dub_job(meeting_id: int, target_lang: str) -> None:
    from app.dubbing import run_dub_job

    def _progress(pct: int, text: str) -> None:
        update_meeting_progress(meeting_id, progress=pct, stage_text=text)

    # The transcript stays valid whatever happens to the dub, so the recording always goes back to
    # "completed"; the dub result (or error) is reported in stage_text / error_message.
    _queue_start(meeting_id)
    try:
        meeting = get_meeting(meeting_id)
        if not meeting:
            return
        # Whisper may still be cached from a previous job; the dub models need that VRAM
        release_whisper_model()
        entry = run_dub_job(meeting, target_lang, _progress, lambda: _check_cancel(meeting_id))
        save_meeting_dub(meeting_id, entry)
        update_meeting_progress(
            meeting_id,
            status="completed",
            progress=100,
            stage_text=f"Дубляж ({target_lang.upper()}) готов: {Path(entry['path']).name}"
            + (f"; не удалось озвучить фраз: {failed}" if (failed := (entry.get("stats") or {}).get("failed_phrases")) else ""),
            error_message="",
        )
    except JobCancelled:
        update_meeting_progress(meeting_id, status="completed", progress=100, stage_text="Дубляж отменён", error_message="")
    except Exception as e:
        tb = traceback.format_exc()
        update_meeting_progress(
            meeting_id,
            status="completed",
            progress=100,
            stage_text=f"Ошибка дубляжа: {str(e)[:140]}",
            error_message=f"{e}\n\n{tb[-800:]}",
        )
    finally:
        _queue_finish(meeting_id)
        clear_vram()
        with _jobs_lock:
            _active_jobs.discard(meeting_id)
            _cancel_requested.discard(meeting_id)


def _enqueue_side_job(meeting_id: int, kind: str, lang: str, status: str, queued_text: str, runner, *args) -> bool:
    """Queues a job that works on a finished transcript (translation, subtitle rendering)."""
    with _jobs_lock:
        if meeting_id in _active_jobs:
            return False
        _active_jobs.add(meeting_id)
        _cancel_requested.discard(meeting_id)
        _queue_add(meeting_id, kind, lang)
    update_meeting_progress(meeting_id, status=status, progress=0, stage_text=queued_text, error_message="")
    _executor.submit(_run_side_job, meeting_id, runner, *args)
    _publish_queue_changed()
    return True


def _run_side_job(meeting_id: int, runner, *args) -> None:
    # Like a dub, these jobs never invalidate the transcript: the recording always goes back to
    # "completed" and the outcome (done, cancelled, error) is reported in stage_text / error_message.
    _queue_start(meeting_id)
    try:
        done_text = runner(meeting_id, *args)
        update_meeting_progress(meeting_id, status="completed", progress=100, stage_text=done_text, error_message="")
    except JobCancelled:
        update_meeting_progress(meeting_id, status="completed", progress=100, stage_text=_SIDE_JOB_NAMES[runner][1], error_message="")
    except Exception as e:
        tb = traceback.format_exc()
        update_meeting_progress(
            meeting_id,
            status="completed",
            progress=100,
            stage_text=f"Ошибка ({_SIDE_JOB_NAMES[runner][0]}): {str(e)[:140]}",
            error_message=f"{e}\n\n{tb[-800:]}",
        )
    finally:
        _queue_finish(meeting_id)
        with _jobs_lock:
            _active_jobs.discard(meeting_id)
            _cancel_requested.discard(meeting_id)


def _translate_runner(meeting_id: int, target_lang: str, model: Optional[str], only_changed: bool) -> str:
    from app.translation import run_translate_job

    res = run_translate_job(
        meeting_id,
        target_lang,
        model=model,
        only_changed=only_changed,
        progress=lambda pct, text: update_meeting_progress(meeting_id, progress=pct, stage_text=text),
        check_cancel=lambda: _check_cancel(meeting_id),
    )
    if not res.get("translated"):
        return f"Перевод ({target_lang.upper()}) уже актуален"
    return f"Перевод ({target_lang.upper()}) готов: {res['translated']} реплик, модель {res.get('model')}"


def _burn_runner(meeting_id: int, lang: str, srt_text: str, out_path: str) -> str:
    from app.video_subtitles import burn_subtitles, probe_media

    meeting = get_meeting(meeting_id)
    if not meeting:
        raise RuntimeError("Запись не найдена")
    media = Path(meeting["media_path"])
    if not media.exists():
        raise RuntimeError(f"Исходный файл не найден: {media}")
    # Whisper may still be cached; the encoder is CPU-bound, but free the VRAM anyway
    release_whisper_model()
    update_meeting_progress(meeting_id, progress=1, stage_text="Вшивание субтитров: подготовка...")
    duration = probe_media(media)["duration"] or float(meeting.get("duration") or 0.0)
    out = burn_subtitles(
        media,
        srt_text,
        Path(out_path),
        duration,
        lambda pct, text: update_meeting_progress(meeting_id, progress=pct, stage_text=text),
        lambda: _check_cancel(meeting_id),
    )
    return f"Видео с субтитрами ({lang.upper()}) готово: {out}"


# runner -> (name for error messages, text after a cancel)
_SIDE_JOB_NAMES = {
    _translate_runner: ("перевод", "Перевод отменён"),
    _burn_runner: ("вшивание субтитров", "Вшивание субтитров отменено"),
}


def enqueue_translate_job(meeting_id: int, target_lang: str, model: Optional[str] = None, only_changed: bool = False) -> bool:
    return _enqueue_side_job(
        meeting_id, "translate", target_lang, "translating", "Перевод в очереди...",
        _translate_runner, target_lang, model, only_changed,
    )


def enqueue_burn_subtitles_job(meeting_id: int, lang: str, srt_text: str, out_path: str) -> bool:
    return _enqueue_side_job(
        meeting_id, "burn_subs", lang, "rendering", "Вшивание субтитров в очереди...",
        _burn_runner, lang, srt_text, out_path,
    )


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


# --- Whisper model reuse between queued jobs ---
# Loading large-v3 takes several seconds and a lot of disk I/O. When more recordings are waiting
# in the queue and the GPU has room, the model stays loaded for the next job instead of being
# reloaded every time. It is released when the queue becomes empty, before an LLM summary on
# GPUs without enough free memory, and after any error.
_KEEP_MIN_FREE_VRAM_GB = 3.0  # leave room for the speaker model while Whisper stays loaded
_SUMMARY_MIN_FREE_VRAM_GB = 8.0  # below this, free Whisper before Ollama loads its model
_whisper_cache: Dict[str, Any] = {"key": None, "model": None}


def _free_vram_gb() -> float:
    try:
        import torch

        if torch.cuda.is_available():
            free, _total = torch.cuda.mem_get_info(0)
            return free / (1024**3)
    except Exception:
        pass
    return 0.0


def _get_whisper_model(model_name: str, device: str, compute_type: str) -> Tuple[WhisperModel, bool]:
    """Returns (model, reused)."""
    key = (model_name, device, compute_type)
    if _whisper_cache["model"] is not None and _whisper_cache["key"] == key:
        return _whisper_cache["model"], True
    release_whisper_model()
    model = _load_whisper_with_fallback(model_name, device, compute_type)
    _whisper_cache.update(key=key, model=model)
    return model, False


def release_whisper_model() -> None:
    if _whisper_cache["model"] is not None:
        _whisper_cache.update(key=None, model=None)
        clear_vram()


def whisper_cache_key() -> Optional[Tuple[str, str, str]]:
    """(model, device, compute_type) of the Whisper model kept in memory, or None."""
    return _whisper_cache["key"] if _whisper_cache["model"] is not None else None


def running_job_kind() -> Optional[str]:
    """Kind of the job the worker runs now ("transcribe", "dub", "translate", ...), or None."""
    with _jobs_lock:
        for q in _queue:
            if q["id"] == _running_id:
                return q["kind"]
        return "other" if _preloading else None


_preloading = False


def _preload_whisper() -> None:
    global _preloading
    try:
        settings = get_all_settings()
        hw = get_hardware_info()
        _get_whisper_model(
            settings.get("whisper_model") or "large-v3-turbo",
            settings.get("device") or hw["recommended_device"],
            settings.get("compute_type") or hw["recommended_compute_type"],
        )
    except Exception:
        traceback.print_exc()
    finally:
        with _jobs_lock:
            _preloading = False


def enqueue_whisper_preload() -> bool:
    """Loads the Whisper model from the settings in the job worker, so it never races a job.
    The next transcription with the same model reuses it. False when the queue is busy."""
    global _preloading
    with _jobs_lock:
        if _queue or _preloading:
            return False
        _preloading = True
    _executor.submit(_preload_whisper)
    return True


def _other_jobs_waiting(meeting_id: int) -> bool:
    with _jobs_lock:
        return bool(_active_jobs - {meeting_id})


def _should_keep_whisper(meeting_id: int, device: str, settings: Dict[str, str]) -> bool:
    if settings.get("keep_model_loaded", "true").lower() != "true":
        return False
    if not _other_jobs_waiting(meeting_id):
        return False
    return device != "cuda" or _free_vram_gb() >= _KEEP_MIN_FREE_VRAM_GB


def _batch_size_for(device: str, hw: Dict[str, Any]) -> int:
    if device != "cuda":
        return 0  # batching gains little on CPU and raises RAM use: decode sequentially
    vram = float(hw.get("vram_total_gb") or 0.0)
    if vram >= 10:
        return 16
    if vram >= 5.5:
        return 8
    return 4


def _start_transcription(
    whisper_model: WhisperModel,
    wav_path: Path,
    *,
    batch_size: int,
    duration: float,
    language: Optional[str],
    hotwords: str,
    vad_enabled: bool,
):
    vad_parameters = dict(min_silence_duration_ms=450, speech_pad_ms=250)
    if batch_size > 0:
        # BatchedInferencePipeline decodes several ~30 s windows at once (much faster on GPU).
        # Without VAD it needs the windows spelled out.
        clips = None
        if not vad_enabled:
            clips = []
            t = 0.0
            while t < duration - 0.2:
                clips.append({"start": t, "end": min(duration, t + 30.0)})
                t += 30.0
        return BatchedInferencePipeline(model=whisper_model).transcribe(
            str(wav_path),
            language=language,
            hotwords=hotwords or None,
            word_timestamps=True,
            vad_filter=vad_enabled,
            vad_parameters=vad_parameters if vad_enabled else None,
            clip_timestamps=clips,
            batch_size=batch_size,
        )
    return whisper_model.transcribe(
        str(wav_path),
        language=language,
        hotwords=hotwords or None,
        word_timestamps=True,
        vad_filter=vad_enabled,
        vad_parameters=vad_parameters,
        condition_on_previous_text=False,
    )


def _collect_segments(meeting_id: int, segments_iter, total_dur: float) -> List[Dict[str, Any]]:
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
    # Batched decoding yields windows in order, but keep the timeline strictly sorted
    raw_segments.sort(key=lambda s: (s["start"], s["end"]))
    return raw_segments


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
        whisper_model, reused = _get_whisper_model(model_name, device, compute_type)

        # `hotwords` is applied to every 30s window; `initial_prompt` would only affect the first
        # window because condition_on_previous_text is disabled.
        hotwords = get_dictionary_hotwords()
        batch_size = (
            _batch_size_for(device, hw)
            if settings.get("batched_inference", "true").lower() == "true"
            else 0
        )
        update_meeting_progress(
            meeting_id,
            status="transcribing",
            progress=15,
            stage_text=f"Распознавание речи — {pretty_whisper}"
            + (f", пакетами по {batch_size}" if batch_size else "")
            + (" (модель уже загружена)" if reused else ""),
        )
        total_dur = max(1.0, float(duration or 1.0))
        decode = dict(duration=total_dur, language=language, hotwords=hotwords, vad_enabled=vad_enabled)

        try:
            segments_iter, info = _start_transcription(whisper_model, wav_path, batch_size=batch_size, **decode)
            raw_segments = _collect_segments(meeting_id, segments_iter, total_dur)
        except JobCancelled:
            raise
        except Exception as batch_err:
            if not batch_size:
                raise
            # e.g. out of GPU memory with a large batch: redo the file the classic way
            print(f"[transcriber] Batched decoding failed ({batch_err}); retrying sequentially", flush=True)
            clear_vram()
            update_meeting_progress(
                meeting_id, progress=16, stage_text=f"Распознавание речи — {pretty_whisper} (обычный режим)"
            )
            segments_iter, info = _start_transcription(whisper_model, wav_path, batch_size=0, **decode)
            raw_segments = _collect_segments(meeting_id, segments_iter, total_dur)

        detected_lang = getattr(info, "language", None) or (language or "ru")

        # Free Whisper before diarization unless the next queued job can reuse it
        whisper_model = None
        if not _should_keep_whisper(meeting_id, device, settings):
            release_whisper_model()
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
                if device == "cuda" and _free_vram_gb() < _SUMMARY_MIN_FREE_VRAM_GB:
                    release_whisper_model()
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
        release_whisper_model()
        update_meeting_progress(
            meeting_id,
            status="cancelled",
            stage_text="Обработка отменена",
            error_message="",
        )
    except Exception as e:
        whisper_model = None
        release_whisper_model()
        tb = traceback.format_exc()
        update_meeting_progress(
            meeting_id,
            status="failed",
            stage_text=f"Ошибка: {str(e)[:140]}",
            error_message=f"{e}\n\n{tb[-800:]}",
        )
