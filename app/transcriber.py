from __future__ import annotations  # type hints name WhisperModel, which is imported only when used

import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from app.hardware import clear_vram, get_hardware_info, register_cuda_dlls

register_cuda_dlls()

# faster-whisper (CTranslate2) and the speaker model (PyTorch) are imported when a job needs them,
# not when the app starts: the window opens in seconds even from a cold disk

from app.audio_utils import extract_audio_16k_mono, format_timestamp, get_media_duration
from app.database import (
    AUDIO_DIR,
    MODELS_DIR,
    get_all_settings,
    get_dictionary_hotwords,
    get_meeting,
    save_meeting_dub,
    save_meeting_results,
    merge_summaries,
    save_segments_backup,
    update_meeting_progress,
)
from app.ollama_client import generate_meeting_summary
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


def is_transcribing(meeting_id: int) -> bool:
    """A recognition job of this recording is queued or running (it will replace the transcript)."""
    with _jobs_lock:
        return any(q["id"] == meeting_id and q["kind"] == "transcribe" for q in _queue)


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
    from app import idle_unload

    idle_unload.touch()
    with _jobs_lock:
        _running_id = meeting_id
        for q in _queue:
            if q["id"] == meeting_id:
                q["started_at"] = time.time()
    _publish_queue_changed()


def _queue_finish(meeting_id: int) -> None:
    global _running_id
    from app import idle_unload

    idle_unload.touch()
    with _jobs_lock:
        _queue[:] = [q for q in _queue if q["id"] != meeting_id]
        if _running_id == meeting_id:
            _running_id = None
    _publish_queue_changed()


def queue_snapshot() -> List[Dict[str, Any]]:
    """Running job first, then waiting jobs in the order they will run."""
    from app.database import meeting_briefs

    with _jobs_lock:
        items = [dict(q) for q in _queue]
        running = _running_id
        cancelling = set(_cancel_requested)
    if not items:
        return []
    meetings = meeting_briefs(list({q["id"] for q in items}))
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
            from app import asr_onnx

            asr_onnx.release_gpu()  # the video memory goes back to the summary model
        _run_pending_exports(meeting_id)


def _run_pending_exports(meeting_id: int) -> None:
    """Exports that waited for this recording's job (see app/pending_exports.py)."""
    try:
        from app.pending_exports import process

        process(meeting_id)
    except Exception:
        traceback.print_exc()


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
        _run_pending_exports(meeting_id)


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
        _run_pending_exports(meeting_id)


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
    update_meeting_progress(meeting_id, progress=1, stage_text="Встраивание субтитров: подготовка...")
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


def _cut_runner(meeting_id: int, kind: str, intervals: List[Tuple[float, float]], has_video: bool, out_path: str) -> str:
    from app.ffmpeg_manager import tool_cmd
    from app.media_cut import DENOISE_FILTER, run_cut

    meeting = get_meeting(meeting_id)
    if not meeting:
        raise RuntimeError("Запись не найдена")
    media = Path(meeting["media_path"])
    if not media.exists():
        raise RuntimeError(f"Исходный файл не найден: {media}")
    label = {"highlights": "Нарезка ключевых моментов", "denoise": "Запись без шума"}.get(kind, "Запись без пауз и слов-паразитов")
    update_meeting_progress(meeting_id, progress=1, stage_text=f"{label}: подготовка...")
    out = run_cut(tool_cmd("ffmpeg"), media, intervals, has_video, Path(out_path),
                  lambda r: update_meeting_progress(meeting_id, progress=max(1, int(99 * r)), stage_text=f"{label}: {int(100 * r)}%"),
                  lambda: _check_cancel(meeting_id), afilter=DENOISE_FILTER if kind == "denoise" else "")
    return f"{label} готова: {out}"


def enqueue_cut_job(meeting_id: int, kind: str, intervals: List[Tuple[float, float]], has_video: bool, out_path: str) -> bool:
    return _enqueue_side_job(
        meeting_id, "cut", kind, "rendering", "Нарезка в очереди...", _cut_runner, kind, intervals, has_video, out_path,
    )


# runner -> (name for error messages, text after a cancel)
_SIDE_JOB_NAMES = {
    _cut_runner: ("нарезка записи", "Нарезка отменена"),
    _translate_runner: ("перевод", "Перевод отменён"),
    _burn_runner: ("встраивание субтитров", "Встраивание субтитров отменено"),
}


def enqueue_translate_job(meeting_id: int, target_lang: str, model: Optional[str] = None, only_changed: bool = False) -> bool:
    return _enqueue_side_job(
        meeting_id, "translate", target_lang, "translating", "Перевод в очереди...",
        _translate_runner, target_lang, model, only_changed,
    )


def enqueue_burn_subtitles_job(meeting_id: int, lang: str, srt_text: str, out_path: str) -> bool:
    return _enqueue_side_job(
        meeting_id, "burn_subs", lang, "rendering", "Встраивание субтитров в очереди...",
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
            from faster_whisper import WhisperModel

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


# About how much VRAM a job needs for Whisper plus the speaker model, in GB
_JOB_VRAM_GB = {"large-v3": 5.0, "large-v3-turbo": 3.5, "medium": 3.0, "small": 2.0}
LIVE_WAIT_POLL_SEC = 5.0


def _wait_for_vram_after_live(meeting_id: int, model_name: str) -> None:
    """While a recording with live captions runs, its caption model shares the GPU. A job that would
    not fit next to it waits for the recording to end instead of running out of memory in the
    middle of both (which can abort the job or stop the captions)."""
    from app import live

    need = _JOB_VRAM_GB.get(model_name, 4.0)
    waited = False
    while live.active_session_count() and _free_vram_gb() < need:
        if not waited:
            update_meeting_progress(meeting_id, stage_text="Ожидание: видеопамять занята субтитрами идущей записи...")
            waited = True
        _check_cancel(meeting_id)
        time.sleep(LIVE_WAIT_POLL_SEC)
    if waited:
        update_meeting_progress(meeting_id, stage_text="Загрузка модели Whisper...")


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


_preloading_name: Optional[str] = None


def _preload_whisper(model_name: Optional[str] = None) -> None:
    global _preloading, _preloading_name
    try:
        settings = get_all_settings()
        hw = get_hardware_info()
        _get_whisper_model(
            model_name or settings.get("whisper_model") or "large-v3-turbo",
            settings.get("device") or hw["recommended_device"],
            settings.get("compute_type") or hw["recommended_compute_type"],
        )
    except Exception:
        traceback.print_exc()
    finally:
        with _jobs_lock:
            _preloading = False
            _preloading_name = None


def enqueue_whisper_preload(model_name: Optional[str] = None) -> bool:
    """Loads a Whisper model (the one from the settings by default) in the job worker, so it never
    races a job. A transcription with the same model reuses it. False when the queue is busy."""
    global _preloading, _preloading_name
    with _jobs_lock:
        if _queue or _preloading:
            return False
        _preloading = True
        _preloading_name = model_name or get_all_settings().get("whisper_model") or "large-v3-turbo"
    _executor.submit(_preload_whisper, model_name)
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
    multilingual: bool = False,
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
        from faster_whisper import BatchedInferencePipeline

        return BatchedInferencePipeline(model=whisper_model).transcribe(
            str(wav_path),
            language=language,
            hotwords=hotwords or None,
            word_timestamps=True,
            vad_filter=vad_enabled,
            vad_parameters=vad_parameters if vad_enabled else None,
            clip_timestamps=clips,
            batch_size=batch_size,
            multilingual=multilingual,
        )
    return whisper_model.transcribe(
        str(wav_path),
        language=language,
        hotwords=hotwords or None,
        word_timestamps=True,
        vad_filter=vad_enabled,
        vad_parameters=vad_parameters,
        condition_on_previous_text=False,
        multilingual=multilingual,
    )


PARTIAL_EVERY_SEC = 20.0


class _PartialTranscript:
    """Shows the text while it is recognized: every PARTIAL_EVERY_SEC the phrases so far are saved
    (one speaker until diarization runs) and the page is told to reload them."""

    def __init__(self, meeting_id: int):
        self.meeting_id = meeting_id
        self.last = time.time()
        # A recording transcribed again keeps its transcript (edits, speaker names) until the new
        # one is complete: a cancel or a failure must not leave only a fragment of it
        m = get_meeting(meeting_id) or {}
        self.enabled = not (m.get("segments") or [])

    def push(self, raw_segments: List[Dict[str, Any]], force: bool = False) -> None:
        # Longer transcripts are saved less often: each save writes the whole text so far
        every = PARTIAL_EVERY_SEC * max(1, len(raw_segments) // 300 + 1)
        if not self.enabled or not raw_segments or (not force and time.time() - self.last < every):
            return
        self.last = time.time()
        from app import events

        partial = [{**seg, "id": i, "speaker": "SPEAKER_00"} for i, seg in enumerate(raw_segments)]
        save_meeting_results(self.meeting_id, segments=partial, reindex=False,
                             speakers={"SPEAKER_00": {"name": "Участник 1", "color": "#0A84FF"}})
        events.publish({"type": "segments", "id": self.meeting_id})


def _collect_segments(meeting_id: int, segments_iter, total_dur: float) -> List[Dict[str, Any]]:
    raw_segments: List[Dict[str, Any]] = []
    partial = _PartialTranscript(meeting_id)
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

        partial.push(raw_segments)
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


def _after_transcription(
    meeting_id: int,
    segments: List[Dict[str, Any]],
    speakers: Dict[str, Any],
    lang: str,
    settings: Dict[str, str],
) -> List[str]:
    """Optional steps that must never fail the transcription; returns short notes for the status."""
    from app import obsidian
    from app.database import update_meeting_fields
    from app.ollama_client import generate_meeting_title, is_generic_title

    notes: List[str] = []
    meeting = get_meeting(meeting_id) or {}
    if (
        segments
        and settings.get("auto_title", "true").lower() == "true"
        and meeting.get("title_is_default")
        and is_generic_title(meeting.get("title") or "")
    ):
        from app import llm
        from app.database import is_local_only

        local_only = is_local_only(meeting)
        if llm.available(settings, local_only=local_only):
            update_meeting_progress(meeting_id, progress=94, stage_text="Название записи по содержанию...")
            try:
                title = generate_meeting_title(segments, speakers, answer_lang=lang, local_only=local_only)
                if title:
                    update_meeting_fields(meeting_id, title=title, title_is_default=0)
                    from app.naming import rename_recording_file

                    rename_recording_file(meeting_id)
            except Exception as e:
                print(f"[title] meeting {meeting_id}: {e}", flush=True)
    try:  # a recurring meeting joins the series of the earlier ones (app/series.py)
        from app import series

        series.auto_assign(meeting_id)
    except Exception as e:
        print(f"[series] meeting {meeting_id}: {e}", flush=True)
    if obsidian.auto_export_enabled(settings):
        update_meeting_progress(meeting_id, progress=97, stage_text="Сохранение заметки в Obsidian...")
        try:
            # The job is not "completed" yet: the note is written for the finished transcript
            path = obsidian.export_meeting(meeting_id, generate=True, in_job=True)
            notes.append(f"заметка Obsidian: {path.name}")
        except Exception as e:
            notes.append(f"Obsidian: {str(e)[:120]}")
    return notes


def _process_meeting_job(meeting_id: int) -> None:
    meeting = get_meeting(meeting_id)
    if not meeting:
        return
    from app.database import update_meeting_fields

    update_meeting_fields(meeting_id, transcript_verified=0)  # a new transcript: nobody has checked it yet
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
        _check_cancel(meeting_id)  # cancelled while waiting: do not spend minutes extracting audio
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
        # "mixed": people switch languages (Russian and English in one call): detected per window
        mixed = lang_setting == "mixed"
        language = None if lang_setting in ("auto", "mixed", "", None) else lang_setting
        vad_enabled = settings.get("vad_filter", "true").lower() == "true"

        _check_cancel(meeting_id)
        from app import asr_onnx

        from app.model_manager import WHISPER_REPLACED

        model_name = {**WHISPER_REPLACED, **asr_onnx.REPLACED}.get(model_name, model_name)
        onnx_engine = asr_onnx.is_onnx_model(model_name)
        if onnx_engine:
            # GigaAM / Parakeet on ONNX Runtime (app/asr_onnx.py): on the GPU when its build is installed
            # and there is room (app/onnx_gpu.py), else on the CPU
            if mixed and len(asr_onnx.ONNX_MODELS[model_name]["languages"]) == 1:
                raise RuntimeError(f"{asr_onnx.ONNX_MODELS[model_name]['name']} понимает только один язык. Для записей на "
                                   "нескольких языках выберите Parakeet или Whisper.")
            if not asr_onnx.supports_language(model_name, language):
                raise RuntimeError(f"{asr_onnx.ONNX_MODELS[model_name]['name']} не распознаёт язык «{language}». "
                                   "Выберите Whisper или другую модель для этой записи.")
            from app import onnx_gpu

            use_gpu = onnx_gpu.gpu_for_job(model_name)
            update_meeting_progress(meeting_id, status="transcribing", progress=15,
                                    stage_text=f"Распознавание речи — {asr_onnx.ONNX_MODELS[model_name]['name']}"
                                    + (" на видеокарте" if use_gpu else ""))
            total_dur = max(1.0, float(duration or 1.0))

            where = {"gpu": " на видеокарте" if use_gpu else ""}

            def _onnx_progress(ratio: float) -> None:
                update_meeting_progress(meeting_id, progress=16 + int(49 * ratio),
                                        stage_text=f"Транскрибация{where['gpu']}: {format_timestamp(ratio * total_dur)} из {format_timestamp(total_dur)}")

            partial = _PartialTranscript(meeting_id)
            onnx_done: List[Dict[str, Any]] = []

            def _onnx_progress_and_partial(ratio: float) -> None:
                _onnx_progress(ratio)
                partial.push(onnx_done)

            try:
                raw_segments = asr_onnx.transcribe(model_name, wav_path, total_dur, _onnx_progress_and_partial,
                                                   lambda: _check_cancel(meeting_id), out=onnx_done, gpu=use_gpu)
            except JobCancelled:
                raise
            except Exception:
                if not use_gpu:
                    raise
                traceback.print_exc()  # CUDA out of memory, a missing DLL: the CPU does the same work
                asr_onnx.release(model_name, gpu=True)
                onnx_done.clear()
                where["gpu"] = " на процессоре"
                update_meeting_progress(meeting_id, stage_text="Видеокарта не справилась — распознавание на процессоре")
                raw_segments = asr_onnx.transcribe(model_name, wav_path, total_dur, _onnx_progress_and_partial,
                                                   lambda: _check_cancel(meeting_id), out=onnx_done)
            text_all = " ".join(s["text"] for s in raw_segments)
            detected_lang = language or asr_onnx.guess_language(text_all)
            whisper_model = None
            reused = False
        else:
            if device == "cuda" and _whisper_cache["key"] != (model_name, device, compute_type):
                _wait_for_vram_after_live(meeting_id, model_name)
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
            decode = dict(duration=total_dur, language=language, hotwords=hotwords, vad_enabled=vad_enabled, multilingual=mixed)

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

        from app.diarizer import diarize_and_merge_segments

        speakers_meta, merged_segments = diarize_and_merge_segments(
            wav_path=wav_path,
            raw_segments=raw_segments,
            device=device,
            num_speakers_hint=num_speakers_hint,
            diarization_enabled=diarization_enabled,
            progress_callback=_diar_cb,
        )
        # Speakers whose voice was named in earlier recordings get that name again
        try:
            from app.voice_profiles import apply_to_speakers

            named = apply_to_speakers(speakers_meta)
            if named:
                print(f"[voice profiles] recognised {named} speaker(s) in meeting {meeting_id}")
        except Exception:
            traceback.print_exc()

        _check_cancel(meeting_id)
        save_segments_backup(meeting_id, [])  # a backup of the previous run does not belong to this one
        if settings.get("clean_fillers", "true").lower() == "true":
            cleaned_segments, removed = clean_filler_words_in_segments(merged_segments)
            if removed:
                # Keep the unmodified recognition result so the cleanup can be undone
                save_segments_backup(meeting_id, merged_segments)
            merged_segments = cleaned_segments

        if mixed:
            # Each phrase keeps its own language: shown in the transcript, used by translation
            for seg in merged_segments:
                seg["lang"] = asr_onnx.guess_language(seg.get("text") or "")
            langs = [seg["lang"] for seg in merged_segments]
            detected_lang = max(set(langs), key=langs.count) if langs else detected_lang

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
            from app import llm

            from app.database import is_local_only

            local_only = is_local_only(get_meeting(meeting_id))
            if llm.available(settings, local_only=local_only):
                cloud = llm.uses_cloud(settings) and not local_only
                if not cloud and device == "cuda" and _free_vram_gb() < _SUMMARY_MIN_FREE_VRAM_GB:
                    release_whisper_model()
                update_meeting_progress(
                    meeting_id,
                    status="summarizing",
                    progress=90,
                    stage_text="Формирование обзора записи (" + ("облачная модель" if cloud else "Ollama") + ")...",
                )
                try:
                    summary_md = generate_meeting_summary(
                        segments=merged_segments,
                        speakers=speakers_meta,
                        template_key="protocol",
                        user_notes=(get_meeting(meeting_id) or {}).get("user_notes") or "",
                        answer_lang=detected_lang,
                        local_only=local_only,
                    )
                    from app.grounding import ground_summary

                    if settings.get("summary_grounding", "true").lower() == "true":
                        summary_md = ground_summary(summary_md, merged_segments, speakers_meta)
                    summaries["protocol"] = summary_md
                    merge_summaries(meeting_id, {"protocol": summary_md}, remove=("error",))
                    if settings.get("auto_commitments", "true").lower() == "true":
                        try:
                            from app import commitments

                            commitments.extract(meeting_id)
                        except Exception as c_err:
                            print(f"[commitments] meeting {meeting_id}: {c_err}", flush=True)
                except Exception as sum_err:
                    summaries["error"] = str(sum_err)
                    merge_summaries(meeting_id, {"error": str(sum_err)})

        clear_vram()

        # 5. A title from the content instead of a file name, and the note in Obsidian
        notes = _after_transcription(meeting_id, merged_segments, speakers_meta, detected_lang, settings)
        from app import semantic

        semantic.kick()  # indexed for search by meaning once the queue is free

        update_meeting_progress(
            meeting_id,
            status="completed",
            progress=100,
            stage_text=f"Готово! Участников: {len(speakers_meta)}, реплик: {len(merged_segments)}" + "".join(f"; {n}" for n in notes),
        )
        if settings.get("delete_media_after", "false").lower() == "true":
            # Privacy setting: the transcript stays, the sound goes
            from app.database import remove_media

            remove_media(meeting_id)

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
