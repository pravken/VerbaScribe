# Changelog · Журнал изменений

Each release page takes its text from here, the section with its number: English first, then Russian.
Betas (`vX.Y.Z-beta.N`) show the sections of their coming version.

Описание каждой версии берётся отсюда в страницу релиза (раздел с её номером): сначала английский, затем
русский. Бета-версии (`vX.Y.Z-beta.N`) показывают раздел своей будущей версии.

## 0.7.0 — English

### In the first beta (0.7.0-beta.1)
Everything of the 0.6.0 betas (Python 3.11, the audit fixes, the new look), and:
- **Dubbing in the VoxCPM2 voice instead of Chatterbox.** In a blind listening test the new model won every pair:
  correct Russian stress, 8 times fewer broken phrases, numbers and dates read as words in the target language.
  About 1.7 times slower, and it needs about 7 GB of VRAM. The dubbing environment is installed again once, the
  weights (about 5 GB) are downloaded on the first dub; the old Chatterbox weights can be deleted in Settings →
  Speech synthesis. The one GPL package (pykakasi) has left the environment.
- The synthesis settings have one «Reference adherence» (1–3) instead of Chatterbox's expressiveness and CFG.
- Dubbing environment: transformers 5.22 closes four vulnerability advisories.

## 0.7.0

### В первой бете (0.7.0-beta.1)
Всё из бет 0.6.0 (Python 3.11, исправления по аудиту, новый вид программы) и:
- **Дубляж голосом VoxCPM2 вместо Chatterbox.** В слепом сравнении на слух новая модель выиграла все пары:
  правильные ударения в русском, в 8 раз меньше срывов, числа и даты читаются словами на языке перевода.
  Медленнее примерно в 1,7 раза и требует около 7 ГБ видеопамяти. Окружение дубляжа ставится заново один раз,
  веса (около 5 ГБ) скачиваются при первом дубляже; старые веса Chatterbox можно удалить в «Настройки → Синтез
  речи». Из окружения ушёл единственный пакет с лицензией GPL (pykakasi).
- В настройках синтеза вместо «Выразительности» и CFG Chatterbox — одна настройка «Следование образцу» (1–3).
- Окружение дубляжа: transformers 5.22 закрывает четыре уведомления об уязвимостях.

## 0.6.0 — English

### In the fourth beta (0.6.0-beta.4)
- **Python 3.11.** The app and the dubbing environment move to Python 3.11, which closes the filelock
  and Pillow vulnerabilities that cannot be fixed on 3.10. The environment is made again once, by
  itself, at the first start after the update: about 7 GB of libraries are downloaded (about 6 GB more
  for dubbing when it is installed again), usually 5–15 minutes. Until the new environment is ready the
  old one works; if the install fails, the old one stays and the next try is a day later.
- Demucs weights (background separation in dubbing) come from a pinned version, like the other models.

### In the third beta (0.6.0-beta.3)
Fixes after an independent audit.
- **Restoring a backup** from someone else does nothing on this computer any more: the copy's own
  database commands, its queue of files to delete and paths leading to your files are removed; sound
  goes back only into the recording folders. Your sound retention setting stays.
- **Private recordings** (local analysis only) stay out of a cloud model in «Since last time», and a
  question about the whole library no longer sends all other transcripts in full.
- **Export without fillers** no longer moves translations onto neighbouring lines or loses a dictation's
  translation.
- **Installing Ollama** from the app works again.
- **Dictation hotkeys:** «Turn off» is saved; the dictation hotkey shows whether it works. A press while
  the last dictation is recognized no longer upsets it.
- **An export waiting for a transcript or translation** keeps its options, goes into the date folder and
  is listed with the recording. Changing the language in the Dub window keeps it.
- **Dubbing** no longer stops at 88 % when the model of the settings is not in Ollama.
- **Video subtitles:** the source picked as the output by mistake is not listed under Copies.
- **Import** takes sound and video only; .opus, .m4v, .mka and more are added.
- Translated SRT and VTT follow the «Names» option.
- «Open the last recording» from the tray right after start, a failed cancel of a pending export is
  reported, `update.bat` leaves no file in the temp folder.

### In the second beta (0.6.0-beta.2)
- **Menus and popovers** are one real glass; they grow from where they were opened and leave the same
  way. With Windows transparency effects off the fill is solid.
- **Undo instead of questions:** a task, a folder, a translation, an analysis section, a record preset,
  an own template and the dictionary go at once and can be brought back for 7 s. Questions stay for
  what cannot be undone, and their button names the action.
- **Four kinds of notices:** status, done, warning, error. An error stays until closed; none leaves
  while the pointer is on it.
- **Names:** Home is «Today», the system folders are «From the Studio» and «Imported», «Transcribe
  again» everywhere, the «Speech models» tab.
- **Settings:** Interface is part of General, model comparison and dubbing are under «Advanced»;
  Recognition starts with «Choose automatically», precision in words under «More»; scale up to 150 %,
  on the first start following the Windows text size.
- **Above the text** the recognition row folds into the model mark «GigaAM v3 ▾»; the **import window**
  shows the model and language, the rest under «Change».
- **Models:** a tile under the hardware opens every model beside the library — running, installed and
  downloadable; the CPU and GPU marks light while a model works there. The analysis model choice says
  what suits this computer and how much video memory it needs. Qwen 7B Standard, Qwen 14B Pro and
  Mistral 7B are no longer offered.
- **Recording:** the microphone and the computer sound each show their own level.
- **Player and text:** drag the time bubble over the wave; the current chapter stays in the middle;
  video buttons on the black panel beside the picture; a double click on a line no longer opens the
  editor. The recording's name is shown once, in full, above the text.
- **Library:** filters by type, source, state, readiness and language; the ⇅ menu stays open; dragging
  opens folders and moves the recording at once.
- **Type and accessibility:** seven text sizes instead of 25; captions and blue buttons up to the
  contrast norm; Windows «show less motion» and «more contrast»; soft edges of scrolled lists; nothing
  under 11 px.
- **update.bat** updates a git-cloned folder next to VerbaScribe.exe.
- Fixed: a dialog no longer closes when a text selection is released over its backdrop; a menu at the
  bottom of the window opens upward; following the playback waits while you scroll; Record in the open
  Studio uses the source it shows; clocks and badges are monospace again, letters like ą, č, ł from Inter.

### A new look
- **A top bar instead of tabs:** Today, Tasks, the open recording (Transcript, ✦ Analysis, Speakers,
  ⋯ menu), Studio, ✦ Assistant; a status bar below (processor, video card, memory, queue). Interface
  scale in Settings → General (75–150 %).
- **The red Record button** records at once in any view with the last studio settings; a second press
  stops and queues the recording.
- **Today:** recent recordings with their summaries, today's calendar meetings over a working day you
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

## 0.6.0

### В четвёртой бете (0.6.0-beta.4)
- **Python 3.11.** Программа и окружение дубляжа переходят на Python 3.11: это закрывает уязвимости
  filelock и Pillow, которые на 3.10 не исправить. Окружение пересоздаётся один раз, само, при первом
  запуске после обновления: скачивается около 7 ГБ библиотек (у дубляжа — ещё около 6 ГБ при его
  переустановке), обычно 5–15 минут. Пока новое окружение не готово, работает прежнее; если установка
  не удалась, прежнее остаётся на месте, а новая попытка будет через день.
- Веса Demucs (отделение фона в дубляже) скачиваются с закреплённой версии, как и остальные модели.

### В третьей бете (0.6.0-beta.3)
Исправления по независимому аудиту.
- **Восстановление резервной копии** чужого архива больше ничего не делает на этом компьютере: из базы
  копии убираются её собственные команды, очередь удаления файлов и пути, ведущие к вашим файлам;
  звук возвращается только в папки записей. Срок хранения звука остаётся вашим.
- **Приватные записи** («только локальный анализ») не попадают в облачную модель через «С прошлого раза»,
  а вопрос ко всей медиатеке не отправляет в облако все остальные расшифровки целиком.
- **Экспорт «Без запинок»** больше не сдвигает переводы на соседние реплики и не теряет перевод диктовки.
- **Установка Ollama** из программы снова работает.
- **Горячие клавиши диктовки:** «Отключить» сохраняет настройку; у диктовки видно, работает ли клавиша.
  Нажатие во время распознавания прошлой диктовки ничего не сбивает.
- **Экспорт, который ждал расшифровку или перевод,** сохраняется с выбранными опциями, в папку по дате и
  появляется в копиях записи. Смена языка в окне дубляжа не теряет такой экспорт.
- **Дубляж** не останавливается на 88 %, если модели из настроек нет в Ollama.
- **Субтитры к видео:** исходный файл, выбранный по ошибке как место сохранения, не попадает в «Копии».
- **Импорт:** принимаются только звук и видео; добавлены .opus, .m4v, .mka и другие.
- В переведённых SRT и VTT работает галочка «Имена».
- «Открыть последнюю запись» из трея сразу после запуска, отмена отложенного экспорта с сообщением об
  ошибке, `update.bat` не оставляет файл во временной папке.

### Во второй бете (0.6.0-beta.2)
- **Меню и всплывающие панели** — одно настоящее стекло; растут из места, где их открыли, и так же
  плавно закрываются. При выключенных эффектах прозрачности Windows — сплошной фон.
- **«Отменить» вместо вопросов:** задача, папка, перевод, раздел анализа, набор записи, свой шаблон
  и словарь удаляются сразу, 7 секунд можно вернуть. Вопрос остался только для необратимого, и его
  кнопка называет действие («Удалить папку», а не «OK»).
- **Уведомления четырёх видов:** состояние, готово, предупреждение, ошибка. Ошибка держится до закрытия,
  ни одно уведомление не исчезает под курсором.
- **Названия:** «Главная» → «Сегодня», системные папки «Из студии» и «Импортированные»,
  «Расшифровать заново» везде одинаково, вкладка «Модели речи».
- **Настройки:** «Интерфейс» вошёл в «Общие», сравнение моделей и дубляж — в группе «Для опытных»;
  в «Распознавании» — «Подбирать автоматически», точность словами в «Дополнительно»; масштаб до 150 %,
  при первом запуске — по размеру текста Windows.
- **Над текстом** строка распознавания свёрнута в метку модели «GigaAM v3 ▾»; **окно импорта** — модель
  и язык, остальное по «Изменить».
- **Модели:** плитка под оборудованием открывает все модели рядом с медиатекой — работающие,
  установленные и те, что можно скачать; отметки CPU и GPU горят, пока на устройстве работает модель.
  Выбор модели анализа подсказывает, что подойдёт этому компьютеру и сколько видеопамяти нужно.
  Из каталога убраны Qwen 7B Standard, Qwen 14B Pro и Mistral 7B.
- **Запись:** у микрофона и звука компьютера свои уровни — в красной кнопке и в студии.
- **Плеер и текст:** пузырёк времени над волной можно тянуть; текущая глава всегда посередине ленты;
  кнопки видео — на чёрной панели рядом с картинкой; двойной щелчок по строке больше не открывает
  редактор. Название записи — один раз, полностью, над текстом.
- **Медиатека:** фильтры по типу, источнику, состоянию, готовности и языку; меню ⇅ не закрывается
  после выбора; перетаскивание раскрывает папку и переносит запись сразу.
- **Шрифты и доступность:** семь размеров текста вместо 25; контраст подписей и синих кнопок до нормы;
  режимы Windows «меньше движения» и «больше контраста»; мягкие края у прокрутки; ничего мельче 11 px.
- **update.bat** — обновление папки, склонированной через git, рядом с VerbaScribe.exe.
- Исправлено: окно не закрывается, если выделение текста отпустить на фоне; меню у нижнего края окна
  открывается вверх; автопрокрутка не тянет текст, пока вы его листаете; «Запись» в открытой студии
  пишет с показанным источником; часы и бейджи снова моноширинным шрифтом, буквы ą, č, ł из Inter.

### Новый вид программы
- **Верхняя панель вместо вкладок:** «Сегодня», «Задачи», открытая запись («Расшифровка», «✦ Анализ»,
  «Участники», меню ⋯), «Студия», «✦ Ассистент». Внизу строка состояния: процессор, видеокарта,
  память и очередь. Масштаб интерфейса — в «Настройки → Общие» (75–150 %).
- **Красная кнопка «Запись»** пишет сразу, в любом окне, с последними настройками студии. Второе
  нажатие останавливает запись, она уходит на распознавание.
- **Сегодня:** недавние записи с итогами, встречи календаря на рабочий день (его начало и конец
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
