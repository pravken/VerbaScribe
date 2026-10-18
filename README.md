# VerbaScribe

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Platform: Windows](https://img.shields.io/badge/Platform-Windows%2010%2F11-0078D6.svg)](#requirements)

[English](#english) | [Русский](#русский)

---

<a name="english"></a>
## English

**VerbaScribe** is a Windows desktop app for transcribing audio and video on your own computer. It recognizes speech, splits it by speaker, and can optionally summarize the transcript or answer questions about it with a local language model through [Ollama](https://ollama.com).

Recordings and transcripts are processed and stored locally (NVIDIA GPU via CUDA, or CPU). The app needs no account or API key. Network access is described [below](#network-access).

### Features

- **Speech recognition** with [faster-whisper](https://github.com/SYSTRAN/faster-whisper) and Silero VAD. Models: `large-v3-turbo`, `large-v3`, `medium`, `small`. Compute precision (`float16`, `int8_float16`, `int8`) is chosen from available VRAM, with fallback to CPU.
- **Speaker separation** by clustering SpeechBrain ECAPA-TDNN voice embeddings. Works without tokens. Accuracy drops with overlapping speech, very short replies, or similar voices, so check the result and fix speakers where needed.
- **Optional audio cleanup** before recognition: FFmpeg filters for noise reduction, click removal and loudness leveling. A simple voice filter is also available in the player.
- **Filler-word removal** ("uh", "um", "э-э", "ммм"…). The original text is kept and can be restored.
- **Local summaries and Q&A** via Ollama (default models: Qwen 2.5 and Mistral 7B). Language models can make mistakes; treat summaries as a draft, not a source of truth. Long recordings are summarized in parts.
- **Transcript editor and player**: click a word to jump to it, edit segments, merge them, rename speakers, waveform, playback speed.
- **Recording** from a microphone, system audio, or both.
- **Search** across all recordings.
- **Custom vocabulary** (names, terms) passed to Whisper as hotwords.
- **Export** to Word (`.docx`), Markdown, plain text, SRT subtitles and JSON.

### Requirements

- Windows 10/11 (64-bit)
- [FFmpeg](https://ffmpeg.org): either in `PATH` (e.g. `winget install Gyan.FFmpeg`) or downloaded from **Settings → Download FFmpeg** (a pinned build, checked by SHA-256)
- An NVIDIA GPU with CUDA is recommended; a CPU works but is noticeably slower
- Disk space for models: about 0.5–3 GB per Whisper model, plus Ollama models if you use summaries

### Getting started

**Option A — release archive.** Download `VerbaScribe-vX.Y.Z.zip` from [Releases](https://github.com/pravken/VerbaScribe/releases), check it against `SHA256SUMS.txt`, unpack it and run `VerbaScribe.exe`. The launcher is built from [`build_exe.py`](build_exe.py) by the [release workflow](.github/workflows/release.yml).

**Option B — from source.**

```bash
git clone https://github.com/pravken/VerbaScribe.git
cd VerbaScribe
start.bat
```

On first launch `setup.bat` creates a Python virtual environment (`.venv`) and installs the exact package versions from `requirements.txt` (a lock file with SHA-256 hashes, generated from `requirements.in`). The app then opens a setup screen where you choose which Whisper model to download. To build the launcher yourself, run `python build_exe.py`; it uses the C# compiler bundled with the .NET Framework.

<a name="network-access"></a>
### Network access

VerbaScribe has no telemetry. It connects to the internet only for these actions:

| When | Where |
| :--- | :--- |
| First-time setup (`setup.bat`) | PyPI and download.pytorch.org (Python packages); winget, if `uv` is missing |
| You download a Whisper or speaker model | Hugging Face (`huggingface.co`) |
| You download an Ollama model | Ollama's model registry, via your local Ollama |
| You confirm installing Ollama | winget (package `Ollama.Ollama`) |

| You click **Download FFmpeg** in Settings | github.com (release `9.0.2` of `GyanD/codexffmpeg`) |

The user interface, fonts and scripts are served locally. The built-in server listens only on `127.0.0.1`. Every launch creates a random session token, so other websites open in your browser cannot read or control the app.

### Where your data is stored

Recordings, extracted audio, the database and exports are kept in `%LOCALAPPDATA%\VerbaScribe` (shown in Settings). Set the `VERBASCRIBE_DATA_DIR` environment variable to use another folder. Speech models stay in the program's `models` folder (`VERBASCRIBE_MODELS_DIR` overrides it). Earlier versions kept data in the program's `data` folder; it is moved automatically on the first start (copied, verified, then removed, with a `MOVED.txt` note left behind). Files you added by disk path are never moved.

### Licenses

VerbaScribe's own code is released under the [MIT License](LICENSE). Models and libraries keep their own licenses; check them yourself before commercial use:

| Component | License |
| :--- | :--- |
| Whisper models (OpenAI) / faster-whisper | MIT |
| Silero VAD | MIT |
| SpeechBrain ECAPA-TDNN (`spkrec-ecapa-voxceleb`) | Apache 2.0 |
| Qwen 2.5 1.5B / 7B / 14B, Mistral 7B | Apache 2.0 |
| Ollama, FastAPI, pywebview | MIT / BSD-3 |
| FFmpeg (optional download, Gyan.dev essentials build) | GPL v3 |
| Inter, JetBrains Mono fonts | SIL Open Font License |

Not every Qwen 2.5 size is Apache 2.0 (the 3B and 72B models use a different license). If you download other models through Ollama, check their terms.

### Development

```bash
.venv\Scripts\python.exe -m unittest discover -s tests -t .
```

Front-end libraries (Tailwind CSS, marked, DOMPurify, fonts) are pinned in `package.json`, and their built copies are committed to `static/vendor/`. The UI is `static/index.html` (markup), `static/app.js` (logic), `static/i18n.js` (Russian and English strings) and `static/styles.css`. You only need Node.js if you change Tailwind classes in `index.html` or `app.js`. In that case, rebuild with:

```bash
npm install
npm run vendor
```

To update Python dependencies, edit `requirements.in` and regenerate the lock (command at the top of `requirements.in`).

---

<a name="русский"></a>
## Русский

**VerbaScribe** — настольное приложение для Windows, которое расшифровывает аудио и видео на вашем компьютере. Оно распознаёт речь, разделяет её по спикерам и, если нужно, составляет краткое содержание или отвечает на вопросы по тексту с помощью локальной языковой модели через [Ollama](https://ollama.com).

Записи и расшифровки обрабатываются и хранятся локально (видеокарта NVIDIA через CUDA или процессор). Аккаунт и API-ключи не нужны. Какие сетевые подключения бывают, описано [ниже](#сеть).

### Возможности

- **Распознавание речи**: [faster-whisper](https://github.com/SYSTRAN/faster-whisper) и Silero VAD. Модели `large-v3-turbo`, `large-v3`, `medium`, `small`. Точность вычислений (`float16`, `int8_float16`, `int8`) подбирается под объём видеопамяти, при необходимости работа переходит на CPU.
- **Разделение по спикерам**: кластеризация голосовых эмбеддингов SpeechBrain ECAPA-TDNN, без токенов. Точность снижается при перебиваниях, очень коротких репликах и похожих голосах, поэтому результат стоит проверять и при необходимости поправлять спикеров вручную.
- **Очистка звука перед распознаванием** (по желанию): фильтры FFmpeg для шумоподавления, удаления щелчков и выравнивания громкости. В плеере также есть простой голосовой фильтр.
- **Удаление слов-паразитов** («э-э», «ммм», «uh», «um»…). Исходный текст сохраняется, его можно вернуть.
- **Локальные саммари и ответы на вопросы** через Ollama (модели по умолчанию — Qwen 2.5 и Mistral 7B). Языковые модели могут ошибаться, поэтому считайте саммари черновиком, а не источником истины. Длинные записи обрабатываются по частям.
- **Редактор расшифровки и плеер**: клик по слову перематывает к нему, реплики можно править и склеивать, спикеров — переименовывать; есть звуковая дорожка и выбор скорости.
- **Запись** с микрофона, системного звука или обоих источников.
- **Поиск** по всем записям.
- **Словарь** имён и терминов, который передаётся в Whisper как hotwords.
- **Экспорт** в Word (`.docx`), Markdown, текст, субтитры SRT и JSON.

### Требования

- Windows 10/11 (64-bit)
- [FFmpeg](https://ffmpeg.org): в `PATH` (например, `winget install Gyan.FFmpeg`) или скачанный из **Настройки → Скачать FFmpeg** (закреплённая сборка с проверкой SHA-256)
- Желательна видеокарта NVIDIA с CUDA; на процессоре работает, но заметно медленнее
- Место под модели: примерно 0,5–3 ГБ на модель Whisper и ещё место под модели Ollama, если нужны саммари

### Как начать

**Вариант А — архив релиза.** Скачайте `VerbaScribe-vX.Y.Z.zip` со страницы [Releases](https://github.com/pravken/VerbaScribe/releases), сверьте его с `SHA256SUMS.txt`, распакуйте и запустите `VerbaScribe.exe`. Лаунчер собирается из [`build_exe.py`](build_exe.py) в [workflow релиза](.github/workflows/release.yml).

**Вариант Б — из исходников.**

```bash
git clone https://github.com/pravken/VerbaScribe.git
cd VerbaScribe
start.bat
```

При первом запуске `setup.bat` создаёт виртуальное окружение Python (`.venv`) и ставит точные версии пакетов из `requirements.txt` (lock-файл с хешами SHA-256, собирается из `requirements.in`). Затем открывается экран настройки, где вы выбираете модель Whisper для скачивания. Чтобы собрать лаунчер самостоятельно, выполните `python build_exe.py`; он использует компилятор C# из комплекта .NET Framework.

<a name="сеть"></a>
### Сетевые подключения

Телеметрии в VerbaScribe нет. В интернет приложение обращается только в этих случаях:

| Когда | Куда |
| :--- | :--- |
| Первичная установка (`setup.bat`) | PyPI и download.pytorch.org (пакеты Python); winget, если нет `uv` |
| Вы скачиваете модель Whisper или модель спикеров | Hugging Face (`huggingface.co`) |
| Вы скачиваете модель Ollama | Реестр моделей Ollama, через вашу локальную Ollama |
| Вы подтверждаете установку Ollama | winget (пакет `Ollama.Ollama`) |

| Вы нажимаете **Скачать FFmpeg** в настройках | github.com (релиз `9.0.2` репозитория `GyanD/codexffmpeg`) |

Интерфейс, шрифты и скрипты загружаются локально. Встроенный сервер слушает только `127.0.0.1`. При каждом запуске создаётся случайный токен сессии, поэтому другие сайты, открытые в браузере, не могут читать данные приложения или управлять им.

### Где хранятся данные

Записи, извлечённый звук, база и экспорт лежат в `%LOCALAPPDATA%\VerbaScribe` (путь виден в настройках). Другую папку можно задать переменной окружения `VERBASCRIBE_DATA_DIR`. Модели распознавания остаются в папке программы `models` (переопределяется `VERBASCRIBE_MODELS_DIR`). Раньше данные хранились в папке программы `data`; при первом запуске она переносится автоматически (копирование, проверка, затем удаление; на старом месте остаётся `MOVED.txt`). Файлы, добавленные по пути на диске, не переносятся.

### Лицензии

Код VerbaScribe распространяется по [лицензии MIT](LICENSE). У моделей и библиотек свои лицензии; перед коммерческим использованием проверьте их самостоятельно (таблица выше, в английском разделе). Не все размеры Qwen 2.5 выпущены под Apache 2.0: у моделей 3B и 72B другая лицензия. Если скачиваете через Ollama другие модели, проверьте и их условия.

### Разработка

Тесты: `.venv\Scripts\python.exe -m unittest discover -s tests -t .`

Фронтенд-библиотеки (Tailwind CSS, marked, DOMPurify, шрифты) закреплены по версиям в `package.json`, а их собранные копии лежат в `static/vendor/`. Интерфейс: `static/index.html` (разметка), `static/app.js` (логика), `static/i18n.js` (строки на русском и английском), `static/styles.css`. Node.js нужен, только если вы меняете классы Tailwind в `index.html` или `app.js`. После этого пересоберите библиотеки командами `npm install` и `npm run vendor`.
