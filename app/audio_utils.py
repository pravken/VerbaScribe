import subprocess
from pathlib import Path
from typing import List, Optional

from app.ffmpeg_manager import find_tool, tool_cmd

# Speech enhancement filter chain for FFmpeg:
# 1. highpass=f=80       -> removes mic rumble, AC hum, table vibrations (< 80 Hz)
# 2. lowpass=f=7600      -> removes high-frequency electronic hiss above speech band
# 3. afftdn=nf=-25:tn=1  -> adaptive FFT spectral denoiser with automatic noise floor tracking
# 4. adeclick            -> removes keyboard clicks, mouse pops, and mouth clicks
# 5. dynaudnorm          -> levels quiet and loud speakers on a call to uniform studio volume
# The cleanup before recognition, in two parts that can be chosen apart (the "Recognition" row)
VOICE_DENOISE_FILTER = "highpass=f=80,lowpass=f=7600,afftdn=nf=-25:tn=1,adeclick"
VOICE_LEVEL_FILTER = "dynaudnorm=f=150:g=15:p=0.92"
STUDIO_VOICE_FILTER = f"{VOICE_DENOISE_FILTER},{VOICE_LEVEL_FILTER}"
# Only the denoising steps of that chain (no band limit, no levelling), for full-range audio that is
# listened to: the original track and the background of a dub. Removes steady hiss and hum, not
# sudden noises; music and ambience get a little duller.
NOISE_REDUCTION_FILTER = "highpass=f=70,afftdn=nf=-25:tn=1,adeclick"


def extract_audio_16k_mono(
    input_media_path: str | Path,
    output_wav_path: str | Path,
    enhance_audio: bool = True,
    denoise: Optional[bool] = None,
    loudnorm: Optional[bool] = None,
) -> Path:
    """
    Uses FFmpeg to convert any video/audio file into 16kHz 16-bit mono PCM WAV
    suitable for both faster-whisper and speaker diarization.
    When enhance_audio=True, applies studio voice denoising, de-clicking, and dynamic loudness leveling.
    """
    in_path = Path(input_media_path)
    out_path = Path(output_wav_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    creation_flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
    if not find_tool("ffmpeg"):
        raise RuntimeError(
            "FFmpeg не найден. Установите его в Настройках (кнопка «Скачать FFmpeg») "
            "или добавьте ffmpeg в PATH."
        )

    # denoise / loudnorm (None: as enhance_audio): the two parts of the cleanup
    chain = ",".join(f for f, on in ((VOICE_DENOISE_FILTER, enhance_audio if denoise is None else denoise),
                                     (VOICE_LEVEL_FILTER, enhance_audio if loudnorm is None else loudnorm)) if on)
    if chain:
        enhanced_cmd = [
            tool_cmd("ffmpeg"),
            "-y",
            "-i",
            str(in_path),
            "-vn",
            "-af",
            chain,
            "-acodec",
            "pcm_s16le",
            "-ar",
            "16000",
            "-ac",
            "1",
            str(out_path),
        ]
        proc = subprocess.run(
            enhanced_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creation_flags,
        )
        if proc.returncode == 0 and out_path.exists() and out_path.stat().st_size > 100:
            return out_path

    # Fallback: raw 16kHz mono extraction without filter chain
    cmd = [
        tool_cmd("ffmpeg"),
        "-y",
        "-i",
        str(in_path),
        "-vn",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "16000",
        "-ac",
        "1",
        str(out_path),
    ]

    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creation_flags,
    )

    if proc.returncode != 0 or not out_path.exists():
        raise RuntimeError(f"Ошибка извлечения звука через FFmpeg:\n{proc.stderr[-600:]}")

    return out_path


def get_media_duration(media_path: str | Path) -> float:
    """
    Returns duration of media file in seconds using ffprobe or soundfile fallback.
    """
    cmd = [
        tool_cmd("ffprobe"),
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(media_path),
    ]
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return round(float(proc.stdout.strip()), 2)
    except Exception:
        pass

    # Fallback via soundfile if WAV
    try:
        import soundfile as sf

        info = sf.info(str(media_path))
        return round(float(info.duration), 2)
    except Exception:
        return 0.0


PEAK_BARS = 400  # the loudness envelope kept for the player's waveform (it asks for 20-400 bars)


def peaks_file(meeting_id: int) -> Path:
    from app.paths import AUDIO_DIR

    return AUDIO_DIR / f"meeting_{meeting_id}.peaks.json"


def loudness_peaks(wav: Path, bars: int = PEAK_BARS) -> List[float]:
    """RMS loudness of `bars` equal parts of a WAV, scaled to 0..1 (98th percentile = 1)."""
    import numpy as np
    import soundfile as sf

    data, _sr = sf.read(str(wav), dtype="float32", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1)
    if len(data) < bars:
        return []
    usable = len(data) - (len(data) % bars)
    rms = np.sqrt(np.mean(np.square(data[:usable].reshape(bars, -1)), axis=1))
    top = float(np.percentile(rms, 98)) or 1.0
    return [round(float(v), 3) for v in np.clip(rms / top, 0.0, 1.0)]


def resample_peaks(peaks: List[float], bars: int) -> List[float]:
    """Fewer bars from the kept envelope: the loudest of each group (a short loud word stays visible)."""
    if not peaks or bars >= len(peaks):
        return list(peaks)
    step = len(peaks) / bars
    return [max(peaks[int(i * step):max(int(i * step) + 1, int((i + 1) * step))]) for i in range(bars)]


def srt_time(seconds: float) -> str:
    """HH:MM:SS,mmm of subtitles, rounded to the millisecond (1.9996 s is 00:00:02,000)."""
    ms = int(round(max(0.0, float(seconds)) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def format_timestamp(seconds: float, srt: bool = False) -> str:
    """
    Formats seconds as HH:MM:SS or MM:SS (or HH:MM:SS,mmm for SRT subtitles).
    """
    if srt:
        return srt_time(seconds)
    seconds = max(0.0, float(seconds))
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if hrs > 0:
        return f"{hrs:02d}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"
