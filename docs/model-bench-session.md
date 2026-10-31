# Промпт для локальной сессии: проверка моделей на ПК

Скопируйте всё, что ниже линии, в новую сессию Claude Code на компьютере с Verbaliox.

---

Ты работаешь на моём Windows-ПК с установленным Verbaliox (локальная программа расшифровки встреч). Нужно сравнить модели распознавания речи и анализа на реальном железе и дать рекомендации, какие модели оставить в программе и какие сделать по умолчанию. Отвечай мне по-русски.

**Условие для всех моделей: бесплатно и законно для коммерческого использования.** Модель с лицензией, запрещающей коммерцию, сразу помечай как «не подходит».

## 0. Подготовка (ничего не ломать)

1. Спроси меня, где установлен Verbaliox (папка с `Verbaliox.exe` и `.venv`). Дальше это `APP`.
2. Склонируй репозиторий рядом (или обнови, если уже есть) и перейди на ветку с новым кодом:
   ```
   git clone https://github.com/pravken/Verbaliox.git Verbaliox-bench
   cd Verbaliox-bench
   git checkout claude/sweet-maxwell-uaextf
   git pull
   ```
3. Python берём из установленной программы: `APP\.venv\Scripts\python.exe` (дальше `PY`). Если там нет каких-то пакетов из `requirements.txt` этой ветки, доустанови их в этот venv.
4. **Мои данные не трогаем.** Сделай копию базы и работай с ней:
   ```
   mkdir %TEMP%\vs-bench-data
   copy "%LOCALAPPDATA%\Verbaliox\meetings.db" %TEMP%\vs-bench-data\
   set VERBALIOX_DATA_DIR=%TEMP%\vs-bench-data
   set VERBALIOX_MODELS_DIR=APP\models
   ```
   Если `meetings.db` лежит в другом месте, найди его: путь показан в «Настройки» программы, строка «Папка данных».
5. Попроси меня закрыть Verbaliox на время замеров: программа делит видеокарту, и время будет неверным.
6. Запусти тесты, чтобы убедиться, что окружение рабочее: `PY -m unittest discover -s tests -t .`

## 1. Проверить лицензии по первоисточникам

Открой карточки моделей на Hugging Face / GitHub / Ollama и запиши точную лицензию и ограничения:

- `nvidia/canary-1b-v2` и его ONNX-экспорт `istupakov/canary-1b-v2-onnx`
- `nvidia/parakeet-tdt-0.6b-v3`, `istupakov/parakeet-tdt-0.6b-v3-onnx`
- GigaAM v3 (`salute-developers/GigaAM`, `istupakov/gigaam-v3-onnx`)
- Qwen3.5 (`Qwen/Qwen3.5-4B`, `Qwen3.5-9B`, `Qwen3.5-27B`), а также `ollama.com/library/qwen3.5`
- Кандидаты, которых пока нет в программе: GigaAM Multilingual (`gigaam-multilingual-large-ctc`), T-one (`t-tech/T-one`)

Для CC-BY-4.0 отметь, какую строку атрибуции нужно указывать.

## 2. Скачать модели

Сначала проверь свободное место на диске и скажи мне, сколько понадобится.

- Распознавание: Whisper `large-v3-turbo` и `large-v3`, GigaAM v3, Parakeet v3, Canary v2. Уже скачанные пропускай. Скачать можно через программу («Настройки → Распознавание») или из кода:
  ```
  PY -c "from app import asr_onnx; asr_onnx.fetch('canary-v2')"
  ```
  Для Whisper используй `app.model_manager.start_models_setup` или кнопку в программе.
- Анализ, через Ollama: `ollama pull qwen3:8b`, `ollama pull qwen3.5:9b`, `ollama pull qwen3.5:4b`. Модель `qwen3.5:27b` качай только если у видеокарты 24 ГБ и больше.

## 3. Проверить, что новые модели вообще работают

- **Canary v2.** Программа передаёт язык в `asr.recognize(..., language="ru")` через обёртку VAD из onnx-asr 0.12 (`app/asr_onnx.py`, функции `_lang_kwargs`, `transcribe`, `recognize_array`). Проверь на русском фрагменте, что получается русский текст, а не английский перевод. Если аргумент не доходит до модели, исправь `app/asr_onnx.py`.
- **Qwen3.5.** Для имён `qwen3*` программа отключает режим размышлений (`app/llm_text.py`, `thinking_off`). Проверь, что в ответе нет `<think>` и что время ответа разумное.

## 4. Замеры

Скрипт `tools/model_bench.py` делает те же замеры, что вкладка «Настройки → Сравнение». Результаты дописываются в `bench-report/results.json`, а таблицы собираются в `bench-report/report.md`.

1. **Публичный набор FLEURS** (Google, CC-BY-4.0; скачивается сам, около 1 ГБ на язык):
   ```
   PY tools/model_bench.py fleurs --langs ru,en --n 80
   ```
   Если раскладка файлов в датасете `google/fleurs` изменилась и скрипт падает, поправь функцию `fleurs_clip`.
2. **Мои записи — главный тест.** Покажи мне список расшифрованных записей (id, название, длительность, язык) из копии базы:
   ```
   PY -c "import sqlite3,os;c=sqlite3.connect(os.environ['VERBALIOX_DATA_DIR']+'/meetings.db');[print(r) for r in c.execute(\"select id,title,round(duration/60,1),language from meetings where status='completed' order by id desc limit 40\")]"
   ```
   Спроси, какие из них я исправлял вручную: только они годятся как эталон. Потом запусти:
   ```
   PY tools/model_bench.py library --meetings <id,id> --clip 600
   ```
   Желательно взять одну запись на русском, одну на английском и одну смешанную или шумную.
3. **Анализ** на 2–3 записях:
   ```
   PY tools/model_bench.py llm --meetings <id,id>
   ```
4. (По желанию, если лицензии подходят.) Временно добавь в `ONNX_MODELS` в `app/asr_onnx.py` записи для `gigaam-multilingual-large-ctc` и `t-tech/t-one`, по образцу GigaAM v3, и прогони их тем же скриптом. Эти изменения **не коммить**, только результаты.

Если какая-то модель падает, запиши ошибку и продолжай с остальными.

## 5. Отчёт

1. `PY tools/model_bench.py report`
2. Добавь в начало `bench-report/report.md` раздел «Выводы»:
   - лицензии из шага 1;
   - лучшая модель распознавания для русского и для английского — по точности и по соотношению точность/скорость;
   - что ставить по умолчанию для видеокарт на 4, 8, 12 и 24 ГБ и для компьютера без видеокарты;
   - какие модели стоит убрать из каталога;
   - модель анализа по умолчанию;
   - какие ошибки и странности ты заметил.
3. Закоммить `bench-report/report.md`, `bench-report/results.json` и исправления кода, если они были (каждое исправление отдельным коммитом, после зелёных тестов), и запушь в ветку `claude/sweet-maxwell-uaextf`. Папку `bench-report/data/` не коммить (она в `.gitignore`).

**Не делай:** не меняй модели по умолчанию и каталог в коде (это решим после отчёта), не выпускай релиз, не удаляй мои модели и не трогай настоящую папку данных.
