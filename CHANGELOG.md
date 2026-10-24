# Журнал изменений · Changelog

Описание каждой версии берётся отсюда в страницу релиза (раздел с её номером). Бета-версии
(`vX.Y.Z-beta.N`) показывают раздел своей будущей версии.

## 0.6.0

### Новый вид программы
- **Верхняя панель вместо вкладок:** «Главная», «Задачи», открытая запись («Расшифровка», «✦ Анализ»,
  «Участники», меню ⋯), «Студия», «✦ Ассистент». Внизу строка состояния: процессор, видеокарта,
  память и очередь. Масштаб интерфейса — в «Настройки → Общие» (75–115 %).
- **Красная кнопка «Запись»** пишет сразу, в любом окне, с последними настройками студии. Второе
  нажатие останавливает запись, она уходит на распознавание.
- **Главная:** недавние записи с итогами, встречи календаря на рабочий день (его начало и конец
  задаются, в том числе через полночь), автозапись всех встреч с исключениями и вопросом перед очной
  встречей, очередь обработки, «Мои задачи», диктовка.
- **Окно импорта:** файлы, ссылки и пути одним списком; модель, язык, число участников, обработка
  звука перед распознаванием, разделение по голосам и анализ; примерное время. Выбор можно запомнить
  или больше не спрашивать.
- **Плеер:** волна по настоящей громкости с цветами участников и главами; повтор реплики и отрезка
  A–B, отметки, скорость от 0,25×; «✦ Звук» — студийный голос, пропуск пауз без речи, дорожка
  с озвучкой. Двойной щелчок по волне показывает это место в тексте. Звук сохраняется в MP3 или WAV,
  а также без запинок и пауз.
- **«Без запинок»** только скрывает «ну», «как бы», «э-э» и повторы при показе: текст записи не
  меняется. Для старых записей, где текст уже чистили, есть «Вернуть исходный текст». В экспорте это
  отдельная галочка вместе с таймкодами и именами.
- **Сведения о записи** (Ctrl+I): файл, даты, формат, распознавание, обработка звука и сохранённые
  копии со «Слушать», «Показать в папке» и «Удалить».
- **Анализ:** разделы слева, настраиваемый обзор, свои шаблоны, письмо участникам, вопрос по записи.
- **Участники:** карточки с ролью и «Это я», подсказки по голосу и по приглашённым из календаря,
  объединение и разделение участников, статистика разговора. Справочник «Все люди» — люди и отделы
  из всех записей.
- **Задачи** из всех записей: по людям, срокам или записям; исполнитель может быть участником,
  человеком не со встречи, отделом или компанией; цитата из записи; срок датой; календарь
  с перетаскиванием; сводка за период; «+ Задача».
- **Студия:** готовые наборы настроек, уровни и проверка микрофона, встреча из календаря, отметки,
  свои заметки по ходу записи, текст на всё окно.
- **Ассистент:** разговоры с историей, где искать (вся медиатека, папка, период, выбранные записи),
  ответы с источниками, ведущими к месту в записи, сохранение ответа.
- **Настройки** — отдельная страница: поиск по всем настройкам, сброс раздела, изменения сохраняются
  сразу.

### Новое
- **Диктовки в медиатеке:** каждая диктовка сохраняется записью в системной папке «Диктовки» — звук,
  текст и перевод. Папка только для локального анализа. Отключается в настройках диктовки.
- **Запуск вместе с Windows** в трее, без окна, и закрытие окна, которое оставляет программу в трее
  («Настройки → Общие»). Скачанное обновление ставится само, пока окно скрыто и ничего не идёт.
- **Параметры распознавания у записи:** шумоподавление, выравнивание громкости, подъём тихих мест,
  пропуск тишины, разделение по голосам, язык и число участников. Запоминаются у записи.
- **Медиатека:** «Свернуть всё / Развернуть всё»; точка у свёрнутой папки или дня, где открытая запись.

### Изменено
- «Обещания» теперь везде называются «Задачи».
- Значки в медиатеке: всегда видно только важное; анализ, переводы и дубляж — при наведении и у
  выбранной записи («Вид → Значки → Все всегда» — как раньше).
- Убрана отметка «Проверено» над текстом. В «Сравнении моделей» эталоном служит текст записи.
- Ошибки записи и обработки называют причину и ведут к нужной настройке; технический текст — за «Подробнее».

### Безопасность
- **torch 2.11** (CUDA 12.6) вместо 2.6: закрыта уязвимость `torch.load` (PYSEC-2026-2286). Обновление
  один раз скачает около 2,5 ГБ. Скорость распознавания та же или выше, точность та же.
- Модели Hugging Face скачиваются с закреплённых коммитов, а не с `main`; окружение дубляжа ставится
  из lock-файла с хешами.

### Исправлено
- Диктовка с переводом (и анализ, перевод, дубляж) больше не берёт модель поиска по смыслу (bge-m3)
  вместо модели для текста — Ollama отвечала на неё «400 Bad Request».
- Волна звука на плеере снова видна у обработанных записей.
- Снова открывается меню вида и сортировки медиатеки, папка подсвечивается при перетаскивании записи.
- Копирование анализа, повторная обработка, удаление записи и ещё несколько действий больше не
  обрываются без сообщения после того, как сделаны.

## 0.6.0 — English

### A new look
- **A top bar instead of tabs:** Home, Tasks, the open recording (Transcript, ✦ Analysis, Speakers,
  ⋯ menu), Studio, ✦ Assistant; a status bar below (processor, video card, memory, queue). Interface
  scale in Settings → General (75–115 %).
- **The red Record button** records at once in any view with the last studio settings; a second press
  stops and queues the recording.
- **Home:** recent recordings with their summaries, today's calendar meetings over a working day you
  set (also past midnight), auto-recording of all meetings with exceptions and a question before
  in-person ones, the queue, My tasks, dictation.
- **Import window:** files, links and paths in one list; model, language, speakers, sound processing
  before recognition, speaker separation, analysis; an estimated time; remember the choice or stop asking.
- **Player:** a waveform from the real loudness with speaker colours and chapters; repeat a line or an
  A–B span, marks, speed from 0.25×; ✦ Sound — studio voice, skipping pauses without speech, the dub
  track; a double click on the wave shows the place in the text; save the sound as MP3 / WAV, also
  without fillers and pauses.
- **"Without fillers"** only hides "um", "like" and repeats in the view; the recording's text stays.
  Older recordings cleaned before get "Restore the original text". In exports it is an option next to
  timecodes and names.
- **Recording details** (Ctrl+I): file, dates, format, recognition, sound processing, saved copies.
- **Analysis:** sections on the left, a configurable overview, your templates, a letter to the
  participants, a question about the recording.
- **Speakers:** cards with role and "This is me", voice and calendar suggestions, merge and split,
  talk statistics; an "All people" directory of people and departments across recordings.
- **Tasks** from all recordings: by person, due date or recording; assignees can be participants,
  people outside the meeting, departments or companies; the quote they came from; a due date;
  a calendar with drag and drop; a summary for a period; "+ Task".
- **Studio:** ready-made sets of settings, levels and a microphone check, the calendar meeting, marks,
  your notes while recording, full-window text.
- **Assistant:** conversations with history, where to look (library, folder, period, chosen
  recordings), answers whose sources lead to the place in a recording, saving an answer.
- **Settings** are a page: search across all settings, reset a section, changes saved at once.

### New
- **Dictations in the library**, in a system folder "Dictations" (sound, text, translation; local analysis only).
- **Start with Windows** in the tray without a window; closing the window keeps the app in the tray;
  a downloaded update installs while the window is hidden and nothing runs.
- **Recognition options per recording:** noise reduction, loudness levelling, quiet parts, skip silence,
  speaker separation, language, number of speakers; remembered for the recording.
- **Library:** fold / unfold everything; a dot on a folded folder or day holding the open recording.

### Changed
- "Promises" are called "Tasks" everywhere.
- Library badges: only what needs attention always; analysis, translations, dubs on hover and on the
  selected row ("View → Badges → All, always" for the old list).
- The "Checked" mark above the text is gone; the model comparison takes the recording's text as the reference.
- Recording and processing errors name the cause and lead to the setting; technical text under "Details".

### Security
- **torch 2.11** (CUDA 12.6) instead of 2.6 (PYSEC-2026-2286); the update downloads about 2.5 GB once.
- Hugging Face models from fixed commits; the dubbing environment from a lock with hashes.

### Fixed
- Dictation with translation (and analysis, translation, dubbing) no longer picks the embedding model
  (bge-m3) as the text model, which Ollama refused with "400 Bad Request".
- The player waveform is back for processed recordings.
- The library view / sort menu opens again, a folder lights up when a recording is dragged over it.
- Copying an analysis, reprocessing, deleting a recording and a few other actions no longer end
  silently after they are done.

---

## 0.5.0

### Новое
- **Несколько моделей — один текст.** Расшифровка каждой модели хранится отдельно: над текстом
  строка «Распознавание» со списком моделей (✓ — уже расшифровано, ↓ — модель не скачана), кнопками
  «Расшифровать», «Свести версии», «Очистить» и статусом, какой моделью сделан текст ниже.
- **Сведение версий.** Один текст из расшифровок нескольких моделей: где модели согласны — их слово,
  спорные места выбирает модель анализа (только из вариантов). Они подчёркнуты, щелчок показывает
  вариант каждой модели.
- **Нескачанные модели в списках** импорта и повторной обработки: модель скачивается перед обработкой,
  прогресс виден в строке записи.
- **Диктовка с переводом:** своя горячая клавиша (по умолчанию Ctrl+Alt+T) вставляет продиктованный
  текст на другом языке.
- **Календарь из Outlook** на этом компьютере (Microsoft 365, Exchange, Outlook.com) без ссылок и входа.
- **Проверенные расшифровки:** отметка «Проверено» делает текст эталоном для сравнения моделей.
- **Резервная копия библиотеки** одной кнопкой (база, заметки, настройки) и восстановление из неё.
- **Окно «О программе»**, Ctrl+1 / 2 / 3 для вкладок, поиск и освобождение файлов, которые не
  использует ни одна запись.
- **Бета-версии:** «Настройки → Обновления → Получать бета-версии».

### Изменено
- GigaAM и Parakeet на видеокарте распознают пакетами (быстрее); громкость поднимается по частям
  записи, поэтому тихий собеседник рядом с громким не пропадает.
- Настройки разложены по карточкам с пояснениями; горячие клавиши названы по действию.
- Расшифровки хранятся построчно в отдельной таблице: библиотека открывается быстрее, правка строки
  пишет одну строку. Перенос — один раз при первом запуске.
- Вкладка «Редактор» со своей иконкой; панели «Распознавание» и «Текст» разделены. «Удалить
  результаты» — в меню ••• записи («Очистить результаты»).

### Безопасность
- **Обновления подписаны ключом автора:** программа ставит только релиз с верной подписью.
- Страница без встроенных скриптов (CSP `script-src 'self'`); запросы других страниц отклоняются.
- Пакеты, которые программа ставит сама (yt-dlp, onnx-asr, onnxruntime-gpu), — только с проверкой хеша.
- Обновление не пишет файлы вне папки программы; восстановление копии — только в свои папки.
- Обновлены certifi, requests, urllib3, idna, DOMPurify; токен сессии не попадает в журнал и адрес.

### Исправлено
- Итог полного аудита: правки и участники не теряются при одновременных изменениях, рабочие процессы
  завершаются вместе с программой даже после сбоя, оборванные скачивания не считаются установленными,
  календарь не падает на необычных событиях, порт другой программы пропускается, один экземпляр окна,
  горячие клавиши не мешают буквам AltGr, тексты интерфейса и сообщения сервера переведены на английский.

---

## 0.5.0 — English

### New
- **Several models, one text.** Each model's transcript is kept; the "Recognition" row above the text
  has the model list (✓ transcribed, ↓ not downloaded), Transcribe, Merge versions, Clear and the status.
- **Merge versions:** one text from several models — their word where they agree; the analysis model
  picks among the variants elsewhere, those places are underlined and a click shows every variant.
- Models not downloaded yet are listed in Import and Re-process and download before the processing.
- **Dictation with translation** on its own hotkey (Ctrl+Alt+T by default).
- Calendar from Outlook on this computer; checked transcripts as references; one-click library backup;
  About window; Ctrl+1/2/3 for the tabs; unused files found and freed; **Settings → Updates → Get beta versions**.

### Changed
- GigaAM / Parakeet batched on the GPU; loudness raised part by part before speech detection.
- Settings in cards; transcripts stored line by line (moved once on the first start).

### Security
- **Updates signed with the author's key**; no inline script (CSP); requests from other pages refused;
  self-installed packages checked by hash; updated certifi, requests, urllib3, idna, DOMPurify.

### Fixed
- Everything from the full audit: no lost edits, work processes end with the app, cut-off downloads are
  not "installed", calendar, ports, single instance, AltGr letters, English texts throughout.
