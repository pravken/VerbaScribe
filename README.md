# VerbaScribe — Local AI Speech, Audio & Video Intelligence Studio

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Models: MIT & Apache 2.0](https://img.shields.io/badge/AI%20Models-MIT%20%7C%20Apache%202.0-success.svg)](LICENSE)
[![Platform: Windows](https://img.shields.io/badge/Platform-Windows%2010%2F11-0078D6.svg)](#)
[![Privacy: 100% Offline](https://img.shields.io/badge/Privacy-100%25%20On--Device-30D158.svg)](#)

[English](#english) | [Русский](#русский)

---

<a name="english"></a>
## English

**VerbaScribe** is a desktop AI studio for **100% local, free, and private** speech-to-text transcription, speaker diarization, studio voice cleanup, and intelligent content analysis across audio and video media: interviews, podcasts, lectures, streams, research recordings, voice memos, and conferences.

All computation is performed **strictly on your computer** (NVIDIA GPU CUDA or CPU). No audio, video, or transcript data ever leaves your device — delivering complete privacy, offline resilience, and corporate compliance without cloud subscriptions or API keys.

### Key Features

- **Local Speech Recognition (`faster-whisper` + `Silero VAD`)**:
  - Support for `large-v3-turbo` (Whisper Turbo), `large-v3` (Whisper Pro), `medium` (Whisper Medium), and `small` (Whisper Light).
  - Automatic compute precision detection (`float16`, `int8_float16`, `int8`) matching your GPU VRAM (4 GB to 24 GB) or CPU.
- **Voice-Based Speaker Diarization (`SpeechBrain ECAPA-TDNN`)**:
  - 100% local, token-free voice clustering with automatic single-speaker detection and intelligent thresholding.
  - 1-click voice sample preview per speaker and batch speaker renaming across the entire transcript.
- **Studio Voice Denoise & Loudnorm**:
  - Automatic FFmpeg filter pipeline (`highpass` + `lowpass` + adaptive spectral denoiser `afftdn` + click removal `adeclick` + dynamic loudness normalization `dynaudnorm`).
  - Real-time "Studio Voice" switch inside the player using Web Audio API compressor and biquad filter.
- **Filler Word Stripping (`Clean Speech`)**:
  - Automatic and manual removal of verbal hesitations and filler words (*"uh", "um", "like", "you know", "э-э", "ммм", "ну типа"*) while preserving exact word-level timestamps.
- **On-Device LLM Summaries, Action Items & Q&A (`Ollama`)**:
  - Structured meeting protocols, action item lists, executive summaries (TL;DR), and interactive question-answering powered by permissive Apache 2.0 open models (`Qwen 2.5 7B / 14B / 1.5B`, `Mistral 7B`).
- **Interactive Player with Waveform & Word-Click Navigation**:
  - SoundCloud-style interactive waveform, click any word to seek immediately, follow-speech auto-scroll, volume slider, playback speeds (`1.0x – 2.0x`), and segment merging.
- **Built-in Audio Recording Studio**:
  - Record from microphone, system audio (loopback), or both channels simultaneously with real-time waveform visualization and live dB signal meter.
- **Global Full-Text Search**:
  - Instant search across all library recordings with direct jumps to matching timestamps.
- **Custom Vocabulary Dictionary**:
  - Add domain terminology, names, and acronyms for accurate Whisper transcription.
- **Native Multi-Format Export**:
  - Save to `Word (.docx)`, `Markdown (.md)`, `Text (.txt)`, `Subtitles (.srt)`, and `JSON (.json)` via the native OS Save dialog with folder selection.

### Quick Start

The repository is distributed **without bulky AI model weights** (~85 KB code bundle). Necessary models are downloaded on demand in 1 click through the built-in Model Manager on first launch:

1. Clone repository:
   ```bash
   git clone https://github.com/pravken/VerbaScribe.git
   cd VerbaScribe
   ```
2. Launch **`VerbaScribe.exe`** (or `start.bat`):
   - On the first run, the launcher automatically creates a local virtual environment (`.venv`) and installs dependencies via `setup.bat`.
   - The native desktop studio window opens with a first-run setup wizard where you can download Whisper and Ollama models with live progress bars and logs.

> **Requirements**: Windows 10/11 (64-bit), FFmpeg in system `PATH` (e.g. `winget install Gyan.FFmpeg`). NVIDIA GPU with CUDA recommended; modern CPU supported.

### License & Commercial Use

VerbaScribe and all default AI models use permissive open-source licenses (**MIT** and **Apache 2.0**), permitting free commercial and corporate on-premise usage:

| Component | License | Commercial / Corporate Use |
| :--- | :--- | :--- |
| **VerbaScribe** | **MIT License** | Allowed |
| **OpenAI Whisper / faster-whisper** | **MIT License** | Allowed |
| **Silero VAD** | **MIT License** | Allowed |
| **SpeechBrain (ECAPA-TDNN)** | **Apache 2.0** | Allowed |
| **Qwen 2.5 (7B / 14B / 1.5B) & Mistral 7B** | **Apache 2.0** | Allowed |
| **Ollama / FastAPI / PyWebView** | **MIT / BSD-3** | Allowed |

---

<a name="русский"></a>
## Русский

**VerbaScribe** — это настольная ИИ-студия для **100% локального, бесплатного и приватного** распознавания речи, разделения спикеров по голосам, студийной очистки звука и интеллектуального анализа любых аудио- и видеоматериалов: интервью, подкастов, лекций, трансляций, исследований, голосовых заметок и рабочих совещаний.

Все вычисления выполняются **строго на вашем компьютере** (NVIDIA GPU CUDA или CPU). Никакие медиафайлы или тексты никогда не отправляются в облако — полностью автономная работа с соблюдением конфиденциальности и корпоративных требований ИБ.

### Ключевые возможности

- **Локальное распознавание речи (`faster-whisper` + `Silero VAD`)**:
  - Модели `large-v3-turbo` (Whisper Turbo), `large-v3` (Whisper Pro), `medium` (Whisper Medium), `small` (Whisper Light).
  - Автоматический подбор точности (`float16`, `int8_float16`, `int8`) под видеокарту (от 4 ГБ VRAM) или процессор.
- **Разделение спикеров по голосам (`SpeechBrain ECAPA-TDNN`)**:
  - Полностью локальная кластеризация тембров голоса без токенов и регистраций, автоматическое определение количества участников.
  - Прослушивание образца голоса в 1 клик и пакетное переименование спикера во всей записи.
- **Студийная очистка голоса и шумоподавление**:
  - Автоматический фильтр FFmpeg (`afftdn` + `adeclick` + `dynaudnorm`) и живой переключатель «Чистый голос» прямо в плеере.
- **Удаление слов-паразитов (`Clean Speech`)**:
  - Очистка от заиканий и голосовых хезитаций (*«э-э», «ммм», «ну типа», «uh», «um»*) с сохранением пословных таймкодов.
- **Локальный ИИ-анализ, структура и Q&A (`Ollama`)**:
  - Протоколы, списки задач (Action Items), краткая выжимка (TL;DR) и ответы на любые вопросы по содержанию через открытые модели с лицензией Apache 2.0 (`Qwen 2.5`, `Mistral 7B`).
- **Интерактивный плеер с визуализацией звуковой дорожки**:
  - Звуковая волна в стиле SoundCloud, клик по слову перематывает на нужную секунду, автопрокрутка за речью, регулятор громкости и скорости `1.0x – 2.0x`.
- **Встроенная студия записи звука**:
  - Запись микрофона, системного звука (динамики) или обоих каналов одновременно с индикатором уровня сигнала (dB) и живой волной.
- **Полнотекстовый поиск по всей медиатеке**:
  - Мгновенный поиск любого слова или фразы по всем сохранённым записям.
- **Пользовательский словарь терминов**:
  - Добавление профильных терминов, имён и названий для точного распознавания.
- **Экспорт в 5 форматов**:
  - `Word (.docx)`, `Markdown (.md)`, `Text (.txt)`, `Subtitles (.srt)`, `JSON (.json)` через системное окно с выбором папки.

### Быстрый старт

Программа поставляется без тяжёлых файлов нейросетей (~85 КБ). Модели скачиваются в 1 клик через встроенный менеджер при первом запуске:

1. Склонируйте репозиторий:
   ```bash
   git clone https://github.com/pravken/VerbaScribe.git
   cd VerbaScribe
   ```
2. Запустите **`VerbaScribe.exe`** (или `start.bat`):
   - При первом запуске автоматически создаётся виртуальное окружение `.venv` и устанавливаются зависимости (`setup.bat`).
   - Откроется окно **VerbaScribe** с мастером настройки для скачивания нужных моделей.

> **Требования**: Windows 10/11 (64-bit), FFmpeg в `PATH` (`winget install Gyan.FFmpeg`). Видеокарта NVIDIA (CUDA) или современный CPU.
