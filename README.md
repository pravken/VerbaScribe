# VerbaScribe

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Platform: Windows](https://img.shields.io/badge/Platform-Windows%2010%2F11-0078D6.svg)](#requirements)

[English](#english) | [Русский](#русский)

---

<a name="english"></a>
## English

**VerbaScribe** is a Windows desktop app for transcribing audio and video on your own computer. It recognizes speech, splits it by speaker, and can optionally summarize the transcript or answer questions about it with a local language model through [Ollama](https://ollama.com).

Recordings and transcripts are processed and stored locally (NVIDIA GPU via CUDA, or CPU). The app needs no account or API key. If you want, analysis can use a cloud model instead (OpenAI, Anthropic Claude, Google Gemini or an OpenAI-compatible service) with your own API key; it is off by default. Network access is described [below](#network-access).

### Features

- **Speech recognition** with [faster-whisper](https://github.com/SYSTRAN/faster-whisper) and Silero VAD. Models: `large-v3-turbo`, `large-v3`, `medium`, `small`. Compute precision (`float16`, `int8_float16`, `int8`) is chosen from available VRAM, with fallback to CPU.
- **Speaker separation** by clustering SpeechBrain ECAPA-TDNN voice embeddings. Works without tokens. Accuracy drops with overlapping speech, very short replies, or similar voices, so check the result and fix speakers where needed.
- **Optional audio cleanup** before recognition: FFmpeg filters for noise reduction, click removal and loudness leveling. A simple voice filter is also available in the player.
- **Filler-word removal** ("uh", "um", "э-э", "ммм"…). The original text is kept and can be restored.
- **Local summaries and Q&A** via Ollama (default models: Qwen 2.5 and Mistral 7B), or optionally a **cloud model over its API** (OpenAI, Claude, Gemini, OpenAI-compatible) with your key; long recordings then fit in one request. Language models can make mistakes; treat summaries as a draft, not a source of truth. Long recordings are summarized in parts.
- **Transcript editor and player**: click a word to jump to it, edit segments, merge them, rename speakers, waveform, playback speed.
- **Recording** from a microphone, system audio, or both, with saved presets for one-click starts. System audio is captured directly (WASAPI loopback), without a screen-share prompt.
- **Three parts of the app**: the **Library** (recordings, transcripts, analysis), the **Studio** (recording with live captions and real-time translation that is typed in without flicker; can fill the whole window) and **Ask** (questions about one recording, a folder or the whole library).
- **Live captions** during recording, optionally translated as you speak.
- **Chapters** with titles generated for long recordings, and **Q&A across a folder or the whole library**: a small folder is read in full, a large one is searched; answers cite moments of the recordings.
- **Folders in the library**: recordings made in the app go to *Recordings*, imported files to *Imported*; create nested folders and sort by dragging. Videos show a thumbnail; the **Details** tab shows where the file is, its format, dates and processing state.
- **Names from the content**: a recording with a generic title ("Recording 14-05", "zoom_0") gets one from its transcript after processing.
- **Voice profiles**: once you name a speaker, their voice is recognised in new recordings (stored only in the local database).
- **Tray icon** that shows what the app is doing and starts or stops a recording from its menu.
- **Automatic update check** with one-click install from GitHub Releases; the download is verified against the release checksums, and your data, models and settings stay untouched.
- **Search** across all recordings.
- **Memory and load**: CPU, RAM and VRAM meters in the sidebar; models are unloaded after a configurable time without use (Settings → General).
- **Recordings folder** of your choice (Settings → Recording); imported copies and files added by their path are kept apart.
- **Convenience**: drag files onto the window to import them, keyboard shortcuts (press `?` for the list), and a "Subtitles" player layout with the current phrase under the video.
- **Custom vocabulary** (names, terms) passed to Whisper as hotwords.
- **Export** to Word (`.docx`), Markdown, plain text, SRT subtitles, JSON and video with subtitles, all from one menu. A translated or dubbed export that does not exist yet is made first and then saved to the export folder by itself.
- **Obsidian**: a note per meeting (properties, summary, overview, tasks as a checklist, transcript) named `{date} {title}`, saved by hand or automatically after processing (Settings → Export).
- **Translate & dub (prototype, optional)**: translates a finished recording with Ollama and re-voices it in each speaker's cloned voice over the original background. See [Translate & dub](#translate-dub).
- **Translate text**: machine translation of a transcript with your local Ollama model, shown under or instead of the original, exported as translated or bilingual files and as video subtitles. See [Translate text](#translate-text).

### Requirements

- Windows 10/11 (64-bit)
- [FFmpeg](https://ffmpeg.org): either in `PATH` (e.g. `winget install Gyan.FFmpeg`) or downloaded from **Settings → Download FFmpeg** (a pinned build, checked by SHA-256)
- An NVIDIA GPU with CUDA and a recent driver is recommended; a CPU works but is noticeably slower
- An internet connection for the first start, which installs the Python environment
- Disk space: about 7 GB for the Python environment (`.venv`, mostly PyTorch with CUDA), about 0.5–3 GB per Whisper model, plus Ollama models if you use summaries

### Getting started

**Option A — release archive.** Download `VerbaScribe-vX.Y.Z.zip` from [Releases](https://github.com/pravken/VerbaScribe/releases), check it against `SHA256SUMS.txt`, unpack it and run `VerbaScribe.exe`. The launcher is built from [`build_exe.py`](build_exe.py) by the [release workflow](.github/workflows/release.yml).

Unpack the archive to a regular folder you can write to, such as `D:\VerbaScribe`, not `Program Files`: the app creates `.venv` and `models` next to itself. The `.exe` alone is not enough, it is only the launcher for the program files in the archive. On the first start the launcher offers to install the Python environment (`setup.bat`). This downloads several gigabytes and usually takes 10–20 minutes, depending on your connection. Later starts take seconds, and new versions are installed from **Settings → General**.

**Option B — from source.**

```bash
git clone https://github.com/pravken/VerbaScribe.git
cd VerbaScribe
start.bat
```

On first launch `setup.bat` creates a Python virtual environment (`.venv`) and installs the exact package versions from `requirements.txt` (a lock file with SHA-256 hashes, generated from `requirements.in`). The app then opens a setup screen where you choose which Whisper model to download. To build the launcher yourself, run `python build_exe.py`; it uses the C# compiler bundled with the .NET Framework.

In a folder cloned with git the app still reports new versions but never installs them itself: update it with `git pull` (and run `setup.bat` again if `requirements.txt` changed).

<a name="translate-text"></a>
### Translate text

The **Translate** button in a recording's header translates every segment with the selected Ollama model on this
computer (nothing is sent anywhere). It runs as a job in the processing queue, with progress, time estimate and **Cancel**;
what was translated before a cancel is kept.

- **Viewing:** the transcript toolbar gets an **Original / Translation / Both** switch. Playback highlighting and
  click-to-seek work on translated text at segment level (a translation has no word timings).
- **Edits:** a segment edited or merged after translation is marked *outdated* and keeps its old translation until you
  click **Update** in the Translate window, which re-translates only changed and new segments.
- **Export:** translated Word, Markdown, text and SRT, plus bilingual Word and SRT, from the **Export** menu.
- **Video subtitles** (from the Translate window): a separate `.srt`; a copy of the video with a switchable subtitle track
  (streams are copied, takes seconds; `mov_text` in MP4, SRT in MKV, with a language tag); or a copy with subtitles burned
  into the picture (re-encoded with libx264 as a queue job). Files go to the `exports` subfolder of the data folder or
  wherever you choose; the original file is never modified.
- **Limitations:** quality depends on the model, so check names and terms; segments are translated in batches of 10
  with only those lines as context; long segments are split into several subtitles with time shared in proportion to
  text length, so timing inside a segment is approximate; burning in re-encodes the whole video and copies the audio
  as is (an audio codec MP4 cannot hold makes the job fail).

<a name="translate-dub"></a>
### Translate & dub (prototype)

The **Dub** button in a recording's header translates every phrase with your local Ollama model, synthesizes it in the
original speaker's voice with [Chatterbox Multilingual](https://github.com/resemble-ai/chatterbox) (23 languages,
including Russian and English), keeps the music and ambience separated by [Demucs](https://github.com/adefossez/demucs),
fits each phrase to the original timing and saves a new file to the `dubs` subfolder of the data folder (`%LOCALAPPDATA%\VerbaScribe\dubs`). It runs as a normal job in the
processing queue, with progress and **Cancel**.

- **Install once:** click **Install** in the Dub window (or run `setup_dub.bat`). It creates a separate environment `.venv-dub` (~6 GB of packages); about
  3 GB of model weights are downloaded into `models\dub\` on the first job. The separate environment is needed
  because Chatterbox pins library versions that conflict with the main app.
- **Requirements:** NVIDIA GPU with ~4 GB free VRAM for the TTS (measured peak 3.5 GB) plus whatever your Ollama
  model needs during translation (the LLM is unloaded before synthesis). A 7B+ Ollama model is recommended for
  translation quality.
- **Speed (RTX 3090):** a 34-second clip took ~95 s on the first run (including model loading) and ~57 s warm;
  expect roughly 1.3–1.5 minutes of processing per minute of speech. Details in [docs/dubbing.md](docs/dubbing.md).
- **Consent and labelling:** the app asks you to confirm that the speakers agreed to having their voices cloned.
  Output files carry `.AI-dub.<lang>.` in the name, an "AI-generated dub" notice in the file metadata, an
  "AI dub … synthetic cloned voice" audio-track title and Chatterbox's inaudible Perth watermark. The original
  audio is kept as a second track, and the translation is saved as `.srt` next to the file.
- **Limitations:** no lip-sync; phrases are sped up (max 1.6×) or trimmed to fit; some accent of the source
  language remains; overlapping speech and diarization errors are carried into the dub; translation can be
  wrong, especially with small models.

<a name="network-access"></a>
### Network access

VerbaScribe has no telemetry. It connects to the internet only for these actions:

| When | Where |
| :--- | :--- |
| First-time setup (`setup.bat`) | PyPI and download.pytorch.org (Python packages); winget, if `uv` is missing |
| You download a Whisper or speaker model | Hugging Face (`huggingface.co`) |
| You download an Ollama model | Ollama's model registry, via your local Ollama |
| You confirm installing Ollama | winget (package `Ollama.Ollama`) |
| You install the dubbing environment (optional) | PyPI and download.pytorch.org |
| Your first dubbing job | Hugging Face (Chatterbox and Demucs weights) |
| You click **Download FFmpeg** in Settings | github.com (release `9.0.2` of `GyanD/codexffmpeg`) |
| App start, at most every 6 hours (can be turned off in Settings → General) | api.github.com: the number of the latest VerbaScribe release, nothing about you or your computer |
| You click **Install** for an update | github.com: the release archive and `SHA256SUMS.txt`; PyPI and download.pytorch.org if the new version needs other Python packages |
| Only if you turned on a cloud model in Settings → Analysis: an overview, a question, a title or an Obsidian note is made | The chosen service (`api.openai.com`, `api.anthropic.com`, `generativelanguage.googleapis.com` or your Base URL): the transcript text of that recording, or the fragments found for a library question. Audio and video are never sent. The API key is stored encrypted with Windows DPAPI |

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
| PyTorch, torchaudio | BSD-3 |
| SoundCard (system audio recording) | BSD-3 |
| FFmpeg (optional download, Gyan.dev essentials build) | GPL v3 |
| Chatterbox Multilingual TTS (optional dubbing) | MIT |
| Demucs `htdemucs` (optional dubbing) | MIT |
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

Записи и расшифровки обрабатываются и хранятся локально (видеокарта NVIDIA через CUDA или процессор). Аккаунт и API-ключи не нужны. По желанию анализ можно выполнять облачной моделью (OpenAI, Anthropic Claude, Google Gemini или OpenAI-совместимый сервис) с вашим API-ключом; по умолчанию это выключено. Какие сетевые подключения бывают, описано [ниже](#сеть).

### Возможности

- **Распознавание речи**: [faster-whisper](https://github.com/SYSTRAN/faster-whisper) и Silero VAD. Модели `large-v3-turbo`, `large-v3`, `medium`, `small`. Точность вычислений (`float16`, `int8_float16`, `int8`) подбирается под объём видеопамяти, при необходимости работа переходит на CPU.
- **Разделение по спикерам**: кластеризация голосовых эмбеддингов SpeechBrain ECAPA-TDNN, без токенов. Точность снижается при перебиваниях, очень коротких репликах и похожих голосах, поэтому результат стоит проверять и при необходимости поправлять спикеров вручную.
- **Очистка звука перед распознаванием** (по желанию): фильтры FFmpeg для шумоподавления, удаления щелчков и выравнивания громкости. В плеере также есть простой голосовой фильтр.
- **Удаление слов-паразитов** («э-э», «ммм», «uh», «um»…). Исходный текст сохраняется, его можно вернуть.
- **Локальные саммари и ответы на вопросы** через Ollama (модели по умолчанию — Qwen 2.5 и Mistral 7B) или, по желанию, **облачной моделью по API** (OpenAI, Claude, Gemini, OpenAI-совместимые) с вашим ключом; длинные записи тогда обрабатываются одним запросом. Языковые модели могут ошибаться, поэтому считайте саммари черновиком, а не источником истины. Длинные записи обрабатываются по частям.
- **Редактор расшифровки и плеер**: клик по слову перематывает к нему, реплики можно править и склеивать, спикеров — переименовывать; есть звуковая дорожка и выбор скорости.
- **Запись** с микрофона, системного звука или обоих источников, с пресетами для запуска в один клик. Системный звук захватывается напрямую (WASAPI loopback), без запроса на показ экрана.
- **Три раздела программы**: **Медиатека** (записи, расшифровки, анализ), **Студия** (запись с живыми субтитрами и переводом в реальном времени, текст печатается без мерцания; можно развернуть на всё окно) и **Вопросы** (к одной записи, к папке или ко всей медиатеке).
- **Живые субтитры** во время записи, по желанию — сразу с переводом.
- **Главы** с заголовками для длинных записей и **вопросы к папке или ко всей медиатеке**: небольшая папка читается целиком, большая — через поиск; в ответе есть ссылки на моменты записей.
- **Папки в медиатеке**: записи из программы попадают в «Записи», импортированные файлы — в «Импорт»; можно создавать вложенные папки и раскладывать записи перетаскиванием. У видео есть превью, а на вкладке **«Сведения»** видно, где лежит файл, его формат, даты и состояние обработки.
- **Названия по содержанию**: запись с безликим названием («Запись 14-05», «zoom_0») после обработки получает название по тексту.
- **Профили голосов**: стоит один раз назвать участника по имени, и в новых записях его голос узнаётся автоматически (хранится только в локальной базе).
- **Значок в трее** показывает, чем занято приложение, и запускает или останавливает запись из меню.
- **Проверка обновлений** и установка в один клик из GitHub Releases; архив сверяется с контрольными суммами релиза, ваши данные, модели и настройки не затрагиваются.
- **Поиск** по всем записям.
- **Память и нагрузка**: индикаторы CPU, RAM и VRAM в боковой панели; модели выгружаются из памяти через заданное время простоя (Настройки → Общие).
- **Папка для записей** на ваш выбор (Настройки → Запись); импортированные копии и файлы, добавленные по пути, хранятся отдельно.
- **Удобство**: файлы можно перетащить в окно, есть горячие клавиши (список — по клавише `?`) и вид плеера «Субтитры» с текущей фразой под видео.
- **Словарь** имён и терминов, который передаётся в Whisper как hotwords.
- **Экспорт** в Word (`.docx`), Markdown, текст, субтитры SRT, JSON и видео с субтитрами — всё в одном меню. Если нужного перевода или дубляжа ещё нет, он сначала выполняется, а файл затем сам сохраняется в папку экспорта.
- **Obsidian**: заметка на каждую встречу (свойства, резюме, обзор, задачи чек-листом, расшифровка) с именем `{дата} {название}` — вручную или автоматически после обработки (Настройки → Экспорт).
- **Перевод и дубляж (прототип, по желанию)**: готовая запись переводится через Ollama и озвучивается клонами голосов участников поверх исходного фона. Подробнее — в разделе [Перевод и дубляж](#дубляж).
- **Перевод текста**: машинный перевод расшифровки локальной моделью Ollama — под оригиналом или вместо него, экспорт переведённых и двуязычных файлов, субтитры для видео. Подробнее — в разделе [Перевод текста](#перевод-текста).

### Требования

- Windows 10/11 (64-bit)
- [FFmpeg](https://ffmpeg.org): в `PATH` (например, `winget install Gyan.FFmpeg`) или скачанный из **Настройки → Скачать FFmpeg** (закреплённая сборка с проверкой SHA-256)
- Желательна видеокарта NVIDIA с CUDA и свежим драйвером; на процессоре работает, но заметно медленнее
- Интернет при первом запуске: в это время устанавливается окружение Python
- Место на диске: около 7 ГБ под окружение Python (`.venv`, в основном PyTorch с CUDA), примерно 0,5–3 ГБ на модель Whisper и ещё место под модели Ollama, если нужны саммари

### Как начать

**Вариант А — архив релиза.** Скачайте `VerbaScribe-vX.Y.Z.zip` со страницы [Releases](https://github.com/pravken/VerbaScribe/releases), сверьте его с `SHA256SUMS.txt`, распакуйте и запустите `VerbaScribe.exe`. Лаунчер собирается из [`build_exe.py`](build_exe.py) в [workflow релиза](.github/workflows/release.yml).

Распакуйте архив в обычную папку с правом записи, например `D:\VerbaScribe`, а не в `Program Files`: рядом с программой создаются `.venv` и `models`. Одного `.exe` недостаточно, это лишь лаунчер для файлов программы из архива. При первом запуске лаунчер предложит установить окружение Python (`setup.bat`). Скачивается несколько гигабайт, обычно это занимает 10–20 минут, в зависимости от скорости интернета. Следующие запуски занимают секунды, а новые версии устанавливаются из **Настройки → Общие**.

**Вариант Б — из исходников.**

```bash
git clone https://github.com/pravken/VerbaScribe.git
cd VerbaScribe
start.bat
```

При первом запуске `setup.bat` создаёт виртуальное окружение Python (`.venv`) и ставит точные версии пакетов из `requirements.txt` (lock-файл с хешами SHA-256, собирается из `requirements.in`). Затем открывается экран настройки, где вы выбираете модель Whisper для скачивания. Чтобы собрать лаунчер самостоятельно, выполните `python build_exe.py`; он использует компилятор C# из комплекта .NET Framework.

В папке, склонированной через git, приложение сообщает о новых версиях, но само их не устанавливает: обновляйте её командой `git pull` (и снова запустите `setup.bat`, если изменился `requirements.txt`).

<a name="дубляж"></a>
<a name="перевод-текста"></a>
### Перевод текста

Кнопка **Перевод** в шапке записи переводит каждую реплику выбранной моделью Ollama на этом компьютере (текст никуда
не отправляется). Работает как задача в очереди обработки, с прогрессом, оценкой времени и кнопкой **Отменить**;
переведённое до отмены сохраняется.

- **Просмотр:** в панели над текстом появляется переключатель **Оригинал / Перевод / Оба**. Подсветка при воспроизведении
  и перемотка по клику работают и в переводе — на уровне реплики (у перевода нет таймкодов отдельных слов).
- **Правки:** реплика, изменённая или объединённая после перевода, помечается как *устаревшая* и показывает старый
  перевод, пока вы не нажмёте **Обновить** в окне «Перевод» — тогда переводятся только изменённые и новые реплики.
- **Экспорт:** переведённые Word, Markdown, текст и SRT, а также двуязычные Word и SRT — в меню **Экспорт**.
- **Субтитры для видео** (в окне «Перевод»): отдельный `.srt`; копия видео с отключаемой дорожкой субтитров (потоки
  копируются, занимает секунды; `mov_text` в MP4, SRT в MKV, с меткой языка); или копия с субтитрами, вшитыми в изображение
  (перекодирование libx264, задача в очереди). Файлы сохраняются в подпапку `exports` папки данных или туда, куда вы
  укажете; исходный файл никогда не изменяется.
- **Ограничения:** качество зависит от модели — имена и термины стоит проверить; реплики переводятся пачками по 10,
  контекстом служат только они; длинные реплики делятся на несколько субтитров, время делится пропорционально длине
  текста, поэтому тайминг внутри реплики приблизительный; вшивание перекодирует всё видео, а звук копирует как есть
  (если кодек звука не подходит для MP4, задача завершится ошибкой).

### Перевод и дубляж (прототип)

Кнопка **Дубляж** в шапке записи переводит каждую фразу локальной моделью Ollama, озвучивает её голосом участника
через [Chatterbox Multilingual](https://github.com/resemble-ai/chatterbox) (23 языка, в том числе русский и английский),
сохраняет музыку и фон, отделённые [Demucs](https://github.com/adefossez/demucs), подгоняет фразы под исходный тайминг
и записывает новый файл в подпапку `dubs` папки данных (`%LOCALAPPDATA%\VerbaScribe\dubs`). Работает как обычная задача в очереди обработки, с прогрессом и кнопкой
**Отменить**.

- **Установка один раз:** нажмите **Установить** в окне «Дубляж» (или запустите `setup_dub.bat`). Создаётся отдельное окружение `.venv-dub` (~6 ГБ пакетов);
  ещё ~3 ГБ весов моделей скачиваются в `models\dub\` при первом дубляже. Отдельное окружение нужно, потому что
  Chatterbox жёстко фиксирует версии библиотек, несовместимые с основным приложением.
- **Требования:** видеокарта NVIDIA, ~4 ГБ свободной видеопамяти под синтез (замеренный пик 3,5 ГБ) плюс память под
  модель Ollama на этапе перевода (перед синтезом LLM выгружается). Для качества перевода рекомендуется модель 7B+.
- **Скорость (RTX 3090):** клип на 34 секунды — ~95 с при первом запуске (с загрузкой моделей) и ~57 с повторно;
  ориентир — 1,3–1,5 минуты обработки на минуту речи. Подробности в [docs/dubbing.md](docs/dubbing.md).
- **Согласие и маркировка:** приложение просит подтвердить, что участники согласны на клонирование голоса.
  Итоговые файлы помечены: `.AI-dub.<язык>.` в имени, пометка «AI-generated dub» в метаданных, название аудиодорожки
  «AI dub … synthetic cloned voice» и неслышимый водяной знак Perth от Chatterbox. Оригинальный звук сохраняется второй
  дорожкой, перевод — рядом в `.srt`.
- **Ограничения:** нет синхронизации губ; фразы ускоряются (до 1,6×) или обрезаются под тайминг; остаётся акцент
  исходного языка; перебивания и ошибки диаризации переходят в дубляж; перевод может быть неточным, особенно на
  маленьких моделях.

<a name="сеть"></a>
### Сетевые подключения

Телеметрии в VerbaScribe нет. В интернет приложение обращается только в этих случаях:

| Когда | Куда |
| :--- | :--- |
| Первичная установка (`setup.bat`) | PyPI и download.pytorch.org (пакеты Python); winget, если нет `uv` |
| Вы скачиваете модель Whisper или модель спикеров | Hugging Face (`huggingface.co`) |
| Вы скачиваете модель Ollama | Реестр моделей Ollama, через вашу локальную Ollama |
| Вы подтверждаете установку Ollama | winget (пакет `Ollama.Ollama`) |
| Вы устанавливаете окружение дубляжа (по желанию) | PyPI и download.pytorch.org |
| Первый запуск дубляжа | Hugging Face (веса Chatterbox и Demucs) |
| Вы нажимаете **Скачать FFmpeg** в настройках | github.com (релиз `9.0.2` репозитория `GyanD/codexffmpeg`) |
| Запуск приложения, не чаще раза в 6 часов (отключается в «Настройки → Общие») | api.github.com: номер последней версии VerbaScribe, без каких-либо данных о вас или компьютере |
| Вы нажимаете **Установить** для обновления | github.com: архив релиза и `SHA256SUMS.txt`; PyPI и download.pytorch.org, если новой версии нужны другие пакеты Python |
| Только если в «Настройки → Анализ» включена облачная модель: создаётся обзор, ответ на вопрос, название или заметка Obsidian | Выбранный сервис (`api.openai.com`, `api.anthropic.com`, `generativelanguage.googleapis.com` или ваш Base URL): текст расшифровки этой записи, а для вопросов по медиатеке — найденные фрагменты. Аудио и видео не отправляются. API-ключ хранится зашифрованным (Windows DPAPI) |

Интерфейс, шрифты и скрипты загружаются локально. Встроенный сервер слушает только `127.0.0.1`. При каждом запуске создаётся случайный токен сессии, поэтому другие сайты, открытые в браузере, не могут читать данные приложения или управлять им.

### Где хранятся данные

Записи, извлечённый звук, база и экспорт лежат в `%LOCALAPPDATA%\VerbaScribe` (путь виден в настройках). Другую папку можно задать переменной окружения `VERBASCRIBE_DATA_DIR`. Модели распознавания остаются в папке программы `models` (переопределяется `VERBASCRIBE_MODELS_DIR`). Раньше данные хранились в папке программы `data`; при первом запуске она переносится автоматически (копирование, проверка, затем удаление; на старом месте остаётся `MOVED.txt`). Файлы, добавленные по пути на диске, не переносятся.

### Лицензии

Код VerbaScribe распространяется по [лицензии MIT](LICENSE). У моделей и библиотек свои лицензии; перед коммерческим использованием проверьте их самостоятельно (таблица выше, в английском разделе). Не все размеры Qwen 2.5 выпущены под Apache 2.0: у моделей 3B и 72B другая лицензия. Если скачиваете через Ollama другие модели, проверьте и их условия.

### Разработка

Тесты: `.venv\Scripts\python.exe -m unittest discover -s tests -t .`

Фронтенд-библиотеки (Tailwind CSS, marked, DOMPurify, шрифты) закреплены по версиям в `package.json`, а их собранные копии лежат в `static/vendor/`. Интерфейс: `static/index.html` (разметка), `static/app.js` (логика), `static/i18n.js` (строки на русском и английском), `static/styles.css`. Node.js нужен, только если вы меняете классы Tailwind в `index.html` или `app.js`. После этого пересоберите библиотеки командами `npm install` и `npm run vendor`.
