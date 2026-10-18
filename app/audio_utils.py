import subprocess
from pathlib import Path

# Speech enhancement filter chain for FFmpeg:
# 1. highpass=f=80       -> removes mic rumble, AC hum, table vibrations (< 80 Hz)
# 2. lowpass=f=7600      -> removes high-frequency electronic hiss above speech band
# 3. afftdn=nf=-25:tn=1  -> adaptive FFT spectral denoiser with automatic noise floor tracking
# 4. adeclick            -> removes keyboard clicks, mouse pops, and mouth clicks
# 5. dynaudnorm          -> levels quiet and loud speakers on a call to uniform studio volume
STUDIO_VOICE_FILTER = "highpass=f=80,lowpass=f=7600,afftdn=nf=-25:tn=1,adeclick,dynaudnorm=f=150:g=15:p=0.92"


def extract_audio_16k_mono(
    input_media_path: str | Path,
    output_wav_path: str | Path,
    enhance_audio: bool = True,
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

    if enhance_audio:
        enhanced_cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(in_path),
            "-vn",
            "-af",
            STUDIO_VOICE_FILTER,
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
        "ffmpeg",
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
        "ffprobe",
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


def format_timestamp(seconds: float, srt: bool = False) -> str:
    """
    Formats seconds as HH:MM:SS or MM:SS (or HH:MM:SS,mmm for SRT subtitles).
    """
    seconds = max(0.0, float(seconds))
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    if millis >= 1000:
        millis = 999

    if srt:
        return f"{hrs:02d}:{mins:02d}:{secs:02d},{millis:03d}"
    if hrs > 0:
        return f"{hrs:02d}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"
