# Промпт для локальной сессии: дубляж на VoxCPM2 — lock и проверка на видеокарте

Скопируйте всё, что ниже линии, в новую сессию Claude Code на компьютере с VerbaScribe.

---

Ты работаешь на моём Windows-ПК с установленным VerbaScribe (RTX 3090 24 ГБ). В ветке
`claude/sweet-maxwell-uaextf` дубляж переведён с Chatterbox на **VoxCPM2** (`bench-report/tts-candidates.md`,
раздел 5). Код написан в облачной сессии, где нет индекса PyTorch и видеокарты. Твоя задача: собрать lock окружения
дубляжа, проверить всё на настоящей видеокарте и исправить, что не работает. Отвечай по-русски. Правила проекта — в
`CLAUDE.md`.

**Условия:**
- Модели и пакеты — только бесплатные и для коммерции. Лицензия новой зависимости не MIT/BSD/Apache/LGPL или не та,
  что в README, — остановись и спроси.
- Мои данные и модели не трогаем: копия базы в `VERBASCRIBE_DATA_DIR`, пустая папка в `VERBASCRIBE_MODELS_DIR`, копия
  программы для проверки обновления.
- В `main` не пушь, релиз не выпускай. Одна тема — один коммит, тесты зелёные по коду возврата.

## Что уже сделано в ветке

- `app/dub_worker.py`:
  - VoxCPM2 загружается из `openbmb/VoxCPM2` по ревизии `32279effe8c19989596f05d353d1447f51d9e915`, с
    `allow_patterns` `*.json, *.safetensors, *.pth, *.model, *.txt`, через `optimize=False`, без денойзера;
  - голос образца кэшируется (`build_prompt_cache`);
  - фраза синтезируется через `_generate_with_prompt_cache`: `cfg_value` из настроек, 10 шагов, `retry_badcase`;
  - водяной знак Perth на каждую фразу;
  - числа словами — `num2words`, на языке перевода.
- `requirements-dub.in`: torch 2.11+cu126 (как в основном окружении), рабочие зависимости voxcpm, perth, num2words,
  demucs. `requirements-dub-voxcpm.txt`: `voxcpm==2.0.3` с хешами, ставится с `--no-deps`.
  **`requirements-dub.txt` ещё старый (Chatterbox)** — его нужно собрать.
- `app/dub_setup.py`, `setup_dub.bat`: окружение с Chatterbox при установке создаётся заново (иначе в нём остался бы
  pykakasi, GPL).
- `app/dub_models.py`: каталог весов — VoxCPM2 (размер 4,7 ГБ — оценка, уточни) и Demucs; старые веса Chatterbox
  показываются, пока лежат на диске, и только удаляются.
- Настройки: одна настройка `dub_voice_cfg` (1–3, по умолчанию 2,0) вместо `dub_cfg_weight` и `dub_exaggeration`;
  `TTS_WORKER_VRAM_GB = 7.5` (`app/dub_text.py`).
- README, `docs/dubbing.md`, CHANGELOG (раздел «В пятой бете», номер версии не поднят).

## 1. Lock окружения дубляжа

1. Собери lock командой из шапки `requirements-dub.txt`, но с `--python-version 3.11` и индексом **cu126**:
   ```
   uv pip compile requirements-dub.in -o requirements-dub.txt --generate-hashes --python-version 3.11 ^
     --python-platform x86_64-pc-windows-msvc --index-strategy unsafe-best-match ^
     --extra-index-url https://download.pytorch.org/whl/cu126
   ```
2. Сверь лицензии новых пакетов (особенно транзитивных от voxcpm, transformers, num2words) с условием выше.
3. `pip-audit -r requirements-dub.txt -r requirements-dub-voxcpm.txt --disable-pip --no-deps`: запиши, что осталось.
   Поправь абзац над шагом `pip-audit (dubbing environment)` в `.github/workflows/ci.yml`, если он больше не верен.

## 2. Установка и загрузка

1. На копии программы (её `.venv-dub` с Chatterbox) нажми в программе «Установить» окружение дубляжа: окружение
   создаётся заново, проверка импорта (`torch, demucs, voxcpm, perth, num2words`) проходит, `cuda=True`.
2. Пустая `VERBASCRIBE_MODELS_DIR`: «Скачать» VoxCPM2 и Demucs в настройках. Проверь, что `allow_patterns` скачали всё,
   что нужно модели (загрузка проходит без обращения к сети после скачивания: `HF_HUB_OFFLINE=1`), и не тянут лишнего.
   Запиши фактический размер и поправь `size_bytes` в `app/dub_models.py`.
3. Старые веса Chatterbox (если были) показываются как «Не используется» и удаляются.

## 3. Дубляж

1. Короткий дубляж записи ru→en и en→ru (если есть английская запись), затем ja и ещё 2 языка из списка
   `DUB_LANGUAGES`. Послушай вместе со мной: голос похож, ударения в русском верные, числа и даты прочитаны словами,
   нет обрывов и бормотания в конце фраз (`_cut_trailing_artifacts` в `app/dubbing.py` — оставь, если всё ещё нужен).
2. Водяной знак: `perth.PerthImplicitWatermarker().get_watermark()` на итоговой дорожке ≈ 1.0.
3. Время и память: запиши RTF и пик видеопамяти в `docs/dubbing.md` (раздел «VRAM / speed estimates», вместо
   оговорки о прототипе). Проверь «Авто» число процессов синтеза на 24 ГБ при `TTS_WORKER_VRAM_GB = 7.5`.
4. Если на твоём torch есть Triton, попробуй `optimize=True`: если быстрее и без ошибок — включи, иначе оставь `False`.
5. Длинная фраза (≈12 с, `UNIT_MAX_SEC`) не зависает: `retry_badcase` и `max_len` её ограничивают.
6. `num2words` на языках списка: где языка нет, цифры остаются как есть. Проверь, что для ja, ko, zh, ar, he чтение
   нормальное или лучше оставить цифры (тогда исключи эти языки в `_spell_numbers`).

## 4. Итог

- Тесты (`python -m unittest discover -s tests -t .` — в окружении дубляжа есть num2words, тест чисел не пропустится),
  `compileall`, браузерный прогон настроек.
- Коммиты в `claude/sweet-maxwell-uaextf`, затем PR в `main` (текст: что проверено, цифры).
- Сводка мне: что сделано, цифры, что не получилось.
