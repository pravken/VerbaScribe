# Журнал изменений · Changelog

Описание каждой версии берётся отсюда в страницу релиза (раздел с её номером). Бета-версии
(`vX.Y.Z-beta.N`) показывают раздел своей будущей версии.

## 0.5.1

### Новое
- **Диктовки в медиатеке:** каждая диктовка сохраняется записью в системной папке «Диктовки» — звук,
  текст и перевод. Папка только для локального анализа. Отключается в настройках диктовки.
- **Переименование участников из текста:** правый щелчок по кружку участника («Переименовать…»,
  «Прослушать», «Показать только его реплики») и пункт «Переименовать…» в списке имени у реплики.
- **Запуск вместе с Windows** в трее, без окна, и закрытие окна, которое оставляет программу в трее
  («Настройки → Общие → Запуск и трей»). Скачанное обновление ставится само, пока окно скрыто и
  ничего не идёт, и программа возвращается в трей.
- **Медиатека:** кнопка «Свернуть всё / Развернуть всё»; точка у свёрнутой папки или дня, где открытая запись.

### Изменено
- Значки в медиатеке: всегда видно только важное (обработка, ошибка, своя пометка «локально»);
  анализ, переводы и дубляж — при наведении и у выбранной записи. «Вид → Значки → Все всегда» — как раньше.
- Боковая панель: CPU, RAM и GPU с иконками подряд, модели — в отдельной рамке, разделённой на
  «Модели на видеокарте» и «Модели на процессоре», с числом загруженных.

### Исправлено
- Диктовка с переводом (и анализ, перевод, дубляж) больше не берёт модель поиска по смыслу (bge-m3)
  вместо модели для текста — Ollama отвечала на неё «400 Bad Request». Ошибки Ollama показываются её словами.

## 0.5.1 — English

### New
- **Dictations in the library**, in a system folder "Dictations" (sound, text, translation; local analysis only).
- **Rename a speaker from the text:** right click on their circle, or "Rename…" in a line's speaker list.
- **Start with Windows** in the tray without a window, and closing the window keeps the app in the tray;
  a downloaded update installs while the window is hidden and nothing runs.
- **Library:** fold / unfold everything; a dot on a folded folder or day holding the open recording.

### Changed
- Library badges: only what needs attention always; analysis, translations, dubs on hover and on the
  selected row ("View → Badges → All, always" for the old list).
- Sidebar: CPU, RAM, GPU with icons, the models framed apart, on the video card and on the processor.

### Fixed
- Dictation with translation (and analysis, translation, dubbing) no longer picks the embedding model
  (bge-m3) as the text model, which Ollama refused with "400 Bad Request"; Ollama errors keep its words.

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
