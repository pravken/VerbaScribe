import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional

from faster_whisper import WhisperModel

from app.audio_utils import extract_audio_16k_mono, format_timestamp, get_media_duration
from app.database import (
    AUDIO_DIR,
    MODELS_DIR,
    get_all_settings,
    get_dictionary_prompt,
    get_meeting,
    save_meeting_results,
    update_meeting_progress,
)
from app.diarizer import diarize_and_merge_segments
from app.hardware import clear_vram, get_hardware_info, register_cuda_dlls
from app.ollama_client import generate_meeting_summary, get_ollama_status

# Single worker queue ensures batch jobs run one after another without VRAM collisions
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="whisper_worker")

# Known Whisper hallucination phrases on Russian/English silence
HALLUCINATION_SUBSTRINGS = (
    "субтитры делал",
    "субтитры сделал",
    "редактор субтитров",
    "корректор а.егорова",
    "а.синецкая",
    "dimatorzok",
    "продолжение следует",
    "подпишись на канал",
    "ставьте лайки",
    "amara.org",
)


# Vocal hesitations & filler tokens stripped when clean_fillers is active
FILLER_TOKENS = {
    "э",
    "ээ",
    "эээ",
    "э-э",
    "э-э-э",
    "эм",
    "эмм",
    "мм",
    "ммм",
    "м-м",
    "а-а",
    "ааа",
    "хм",
    "хмм",
    "uh",
    "uhh",
    "um",
    "umm",
    "erm",
    "er",
    "hmm",
    "hm",
}

FILLER_PHRASE_PATTERNS = (
    (r"(?i)\b(э+[\-\s]*э+|м+[\-\s]*м+|э+м+|uh+|um+|er+m*)\b[,.\s]*", ""),
    (r"(?i)\bну\s+типа\b[,.\s]*", ""),
    (r"(?i)\bкак\s+бы\b[,.\s]*", ""),
    (r"(?i)\bвот\s+это\s+самое\b[,.\s]*", ""),
    (r"(?i)\b(\w+)\s+\1\b", r"\1"),  # immediate stutter repetition: "я я", "мы мы"
)


def _is_hallucination(text: str) -> bool:
    t = text.strip().lower()
    if not t:
        return True
    for bad in HALLUCINATION_SUBSTRINGS:
        if bad in t and len(t) < 65:
            return True
    return False


def clean_filler_words_in_segments(
    segments: List[Dict[str, Any]],
) -> tuple[List[Dict[str, Any]], int]:
    """
    Removes vocal hesitations (э-э, эм, ммм, uh, um), filler phrases (ну типа, как бы),
    and stuttered duplicate words while preserving word-level timestamps.
    Returns (cleaned_segments, removed_count).
    """
    import re

    removed_count = 0
    cleaned_segments: List[Dict[str, Any]] = []

    for seg in segments:
        seg_copy = dict(seg)
        words = list(seg_copy.get("words") or [])
        if words:
            filtered_words = []
            prev_norm = ""
            for w in words:
                raw_w = str(w.get("word", ""))
                norm = re.sub(r"[^\w\-а-яёa-z]", "", raw_w.lower())
                if norm in FILLER_TOKENS:
                    removed_count += 1
                    continue
                if norm and norm == prev_norm and len(norm) <= 3:
                    removed_count += 1
                    continue
                prev_norm = norm
                filtered_words.append(w)
            seg_copy["words"] = filtered_words

        orig_text = str(seg_copy.get("text") or "").strip()
        new_text = orig_text
        for pat, repl in FILLER_PHRASE_PATTERNS:
            new_text, n_sub = re.subn(pat, repl, new_text)
            removed_count += n_sub

        new_text = re.sub(r"\s{2,}", " ", new_text).strip(" ,;-")
        if new_text and new_text[0].islower() and orig_text and orig_text[0].isupper():
            new_text = new_text[0].upper() + new_text[1:]

        if new_text:
            seg_copy["text"] = new_text
            cleaned_segments.append(seg_copy)

    for idx, s in enumerate(cleaned_segments):
        s["id"] = idx + 1

    return cleaned_segments, removed_count


def clean_meeting_transcript(meeting_id: int) -> Dict[str, Any]:
    m = get_meeting(meeting_id)
    if not m:
        raise ValueError("Встреча не найдена")
    segments = m.get("segments") or []
    cleaned, removed = clean_filler_words_in_segments(segments)
    save_meeting_results(meeting_id, segments=cleaned)
    updated = get_meeting(meeting_id)
    return {"meeting": updated, "removed_count": removed}


def enqueue_meeting_job(meeting_id: int) -> None:
    _executor.submit(_process_meeting_job, meeting_id)


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
        model_name = meeting.get("model_name") or settings.get("whisper_model") or "large-v3-turbo"
        device = settings.get("device") or hw["recommended_device"]
        compute_type = settings.get("compute_type") or hw["recommended_compute_type"]
        lang_setting = meeting.get("language") or settings.get("language") or "auto"
        language = None if lang_setting in ("auto", "", None) else lang_setting
        vad_enabled = settings.get("vad_filter", "true").lower() == "true"

        whisper_model = _load_whisper_with_fallback(model_name, device, compute_type)

        dict_prompt = get_dictionary_prompt()
        update_meeting_progress(
            meeting_id,
            status="transcribing",
            progress=15,
            stage_text=f"Распознавание речи ({model_name})...",
        )

        segments_iter, info = whisper_model.transcribe(
            str(wav_path),
            language=language,
            initial_prompt=dict_prompt if dict_prompt else None,
            word_timestamps=True,
            vad_filter=vad_enabled,
            vad_parameters=dict(min_silence_duration_ms=450, speech_pad_ms=250),
            condition_on_previous_text=False,
        )

        detected_lang = getattr(info, "language", None) or (language or "ru")
        total_dur = max(1.0, float(duration or getattr(info, "duration", 1.0) or 1.0))

        raw_segments: List[Dict[str, Any]] = []
        for seg in segments_iter:
            text = (seg.text or "").strip()
            if _is_hallucination(text):
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
        del whisper_model
        clear_vram()

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

        if settings.get("clean_fillers", "true").lower() == "true":
            merged_segments, _ = clean_filler_words_in_segments(merged_segments)

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

    except Exception as e:
        clear_vram()
        tb = traceback.format_exc()
        update_meeting_progress(
            meeting_id,
            status="failed",
            stage_text=f"Ошибка: {str(e)[:140]}",
            error_message=f"{e}\n\n{tb[-800:]}",
        )
