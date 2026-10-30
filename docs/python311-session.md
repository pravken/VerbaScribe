# Промпт для локальной сессии: переход на Python 3.11

Скопируйте всё, что ниже линии, в новую сессию Claude Code на компьютере с VerbaScribe.

---

Ты работаешь на моём Windows-ПК с установленным VerbaScribe (локальная программа расшифровки встреч).
Нужно перевести основное окружение программы с Python 3.10 на **Python 3.11**. Отвечай мне по-русски.
Правила проекта — в `CLAUDE.md`, прочитай его.

**Зачем.** `pip-audit` в CI нашёл 12 уязвимостей в `filelock` (CVE-2026-112907…-112915, -112935…-112940).
Все исправлены в `filelock` 4.2.1, а ей нужен Python 3.11. Пока они приняты в `.github/workflows/ci.yml`
(шаг `pip-audit`, комментарий над ним). После перехода их нужно убрать оттуда — это и есть цель.

**Почему на ПК.** Lock-файл `requirements.txt` с хешами собирается с индексом PyTorch
(`download.pytorch.org`), а из облачной сессии он закрыт. И обновление существующих установок (их `.venv`
сделана на 3.10) надо проверить на настоящем Windows.

## 0. Подготовка (ничего не ломать)

1. Спроси меня, где установлен VerbaScribe. Дальше это `APP`. Его не трогаем: всё делаем в клоне и копиях.
2. Склонируй репозиторий и создай ветку от `main`:
   ```
   git clone https://github.com/pravken/VerbaScribe.git VerbaScribe-py311
   cd VerbaScribe-py311
   git checkout -b claude/python-311 origin/main
   ```
3. Данные — только копия: `VERBASCRIBE_DATA_DIR=%TEMP%\vs-py311-data` (копия `meetings.db` из
   `%LOCALAPPDATA%\VerbaScribe`), модели — `VERBASCRIBE_MODELS_DIR=%TEMP%\vs-py311-models` или копия нужных.
4. Нужны `uv` и Python 3.11 с python.org (подписанный: Smart App Control). `winget install
   --id=Python.Python.3.11 -e --scope user`, если его нет.

## 1. Lock-файл на 3.11

1. В `requirements.in` поменяй 3.10 на 3.11 в шапке и в команде сборки, затем собери lock той же командой:
   ```
   uv pip compile requirements.in -o requirements.txt --generate-hashes --python-version 3.11 ^
     --python-platform x86_64-pc-windows-msvc --index-strategy unsafe-best-match ^
     --extra-index-url https://download.pytorch.org/whl/cu126 --emit-index-url --upgrade-package filelock
   ```
   Остальные версии не поднимай без нужды (`--upgrade-package` только для `filelock`); посмотри дифф: если
   uv сам сменил что-то ещё, проверь, что лицензии новых версий те же (таблица лицензий в README). Если
   лицензия поменялась — остановись и спроси меня.
2. `pip-audit -r requirements.txt --disable-pip --no-deps`: уязвимостей `filelock` быть не должно. Убери их
   `--ignore-vuln` и абзац про `filelock` в `.github/workflows/ci.yml`. Ограничения `setuptools` (PYSEC-2026-3447,
   GHSA-xmj3-9gf7-h52w) зависят от torch, а не от Python: оставь, если они ещё на месте.
3. `app/onnx_gpu.py`: в `WHEEL_HASHES` добавь хеш колеса `onnxruntime-gpu` той же версии для `cp311` (с PyPI,
   файл `...-cp311-cp311-win_amd64.whl`). Тест `PinnedDownloadsTest` в `tests/test_safeguards.py` проверяет
   `cp310`: переведи его на `cp311`. Нужна ли запись `cp310` дальше — реши по шагу 3 (обновление старых
   установок).

## 2. Новые установки

- `setup.bat`: все 3.10 → 3.11 (winget `Python.Python.3.11`, `py -3.11`, `uv venv --python 3.11`, поиск
  подписанного Python в `Python311`).
- `update.bat` (обновление git-клона): ставит пакеты в существующую `.venv` — см. шаг 3.
- `.github/workflows/ci.yml` и `release.yml`: `python-version: "3.11"`, в шаге проверки lock `--python-version 3.11`.
- README (русский и английский): упоминания Python 3.10.

## 3. Обновление существующих установок (главное)

Сейчас обновление ставит пакеты в ту же `.venv` (`app/updater.py`, около строки 527: `uv pip install --python
.venv\Scripts\python.exe -r requirements.txt`). Lock на 3.11 в окружение 3.10 не встанет. Нужно:

1. Перед установкой пакетов проверить версию Python в `.venv`. Если не 3.11 — собрать **новое** окружение рядом
   (например `.venv-new`) на Python 3.11: подписанный python.org при Smart App Control (как в `setup.bat`), иначе
   uv; поставить в него `requirements.txt`; при успехе заменить старое (старое оставить как `.venv-old` до
   следующего успешного запуска), при ошибке — ничего не менять, и сообщение говорит, что прежняя версия на
   месте. Учти, что установщик работает, когда программа закрыта (`launch_installer`), и что `.venv` в списке
   `PROTECTED`.
2. То же в `update.bat`.
3. Сборка ONNX Runtime для видеокарты (`app/onnx_gpu.py`) ставится в отдельную папку для текущего Python: после
   смены окружения она должна поставиться заново для `cp311`.
4. Окружение дубляжа `.venv-dub` — отдельное. Попробуй его на 3.11 (`app/dub_setup.py`, `setup_dub.bat`,
   `requirements-dub.in`: chatterbox-tts 0.1.7 закрепляет свои версии). Если встаёт и дубляж работает — переводи
   (заодно уходят предупреждения про Pillow 13 в CI); если нет — оставь 3.10 и запиши почему.

## 4. Проверки

- `python -m unittest discover -s tests -t .` на 3.11 и `python -m compileall -q app run.py build_exe.py tests`:
  код возврата 0.
- Браузерный прогон: `tests/ui/smoke.mjs` и `tests/ui/scenarios.mjs` (как в `ci.yml`).
- **Чистая установка:** копия клона в пустую папку, `setup.bat`, запуск: расшифровка GigaAM и Whisper на видеокарте,
  анализ через Ollama.
- **Обновление старой установки:** копия `APP` целиком (с её `.venv` на 3.10) в отдельную папку; тестовый архив
  релиза из ветки (`git archive`, версия в `app/__init__.py` выше текущей); установка его обновлением программы:
  окружение пересоздаётся на 3.11, программа запускается, расшифровка на видеокарте работает. Затем то же, но с
  обрывом сети посреди установки пакетов: прежняя версия остаётся рабочей.
- Дубляж: короткий дубляж записи (если `.venv-dub` переведён — после его обновления тоже).

## 5. Итог

- Коммиты по одной теме, в `claude/python-311`, потом PR в `main`. В `main` не пушь, релиз не выпускай, ключ
  подписи не трогай.
- В `CHANGELOG.md` (раздел следующей версии, ru и en): переход на Python 3.11, обновление один раз
  пересоздаёт окружение и скачивает пакеты заново (сколько ГБ — замерь).
- Отчёт мне: что сделано, результаты проверок, что не получилось и почему.
