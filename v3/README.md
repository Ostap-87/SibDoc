# SibDoc 3 (на основе SibDoc 2.0.1 · PostControl Suite)

> **SibDoc 3 — новый проект.** Два режима: пост-контроль прошедших отгрузок (ретро) и проверка ближайших отгрузок
> в реальном времени; пакетная загрузка документов; работа без интернета. План — [`docs/ROADMAP_v3.md`](docs/ROADMAP_v3.md).
> Код SibDoc 3 — папка `v3/` репозитория `Ostap-87/SibDoc` (все команды — из неё); версия 2.0.1 без изменений —
> корень репозитория и ветка `v2`, резерв — `Ostap-87/sibdoc-versions`.

Пост-контроль таможенных деклараций на маршруте Россия → Китай: сверка китайских деклараций
(报关单 / CD), российских ДТ и данных SAP. Семь проверок (цена, HS-код, условия поставки,
элементы декларации, контракт, полнота, сквозная «ДТ ↔ 报关单 ↔ SAP»), проверка качества
данных, курсы ЦБ РФ и НБК (CFETS), журнал событий, коннекторы, отчёты Excel и PDF (RU + ZH).

**На Mac (окно приложения):** скачайте репозиторий (Code → Download ZIP), распакуйте и в Терминале
выполните `bash ~/Downloads/sibdoc-main/install_mac.command` — появится приложение «SibDoc» в
~/Applications (Launchpad, Dock). Нужен Python 3.11+ с python.org; установщик подскажет.
Если pypi.org отвечает медленно (Китай, часть сетей в России), установщик сам берёт библиотеки с зеркала
(Цинхуа или Alibaba); своё зеркало: `PIP_INDEX_URL=<адрес> bash install_mac.command`.
В браузере вместо окна: `start_mac.command`.
Установщик кладёт значок «SibDoc» на рабочий стол и ставит распознавание сканов (Tesseract) готовой сборкой
conda-forge в папку `.ocr` (~200 МБ, без Homebrew; отдельно — `bash install_ocr_mac.command`).
Обновить уже установленный SibDoc (папка на рабочем столе, `.venv` и `.ocr` сохраняются):

```bash
cd ~/Desktop/sibdoc-main && curl -fsSL https://codeload.github.com/Ostap-87/SibDoc/tar.gz/refs/heads/main \
  | tar -xzf - --strip-components=1 && bash install_mac.command
```

**SibDoc3: закрытый контур (без интернета).** По умолчанию программа не обращается в интернет
(`config/system.yaml → offline`, `SIBDOC_OFFLINE=0|1`); разрешены только адреса внутри контура
(`offline.allow_hosts`: локальная сеть, сервер 1С, база данных). В шапке — «Закрытый контур: интернет не
используется». Курсы — файлами ЦБ РФ / CFETS / шаблоном («Курсы валют» → «Курсы из файла»), справочники
и правила — «Обновление с носителя». Комплект для установки без интернета собирается на компьютере с
интернетом: `bash tools/make_offline_bundle.sh --platforms windows,linux-x86_64 --with-ocr --with-python`,
в контуре — `bash install.sh --offline wheelhouse` (`install.bat`, `install_mac.command` — так же).
Подробно — [`docs/OFFLINE.md`](docs/OFFLINE.md).

**Сканы деклараций (OCR):** PNG/JPG/TIFF/PDF-сканы ДТ и 报关单 на русском, английском и китайском;
демо — `data/demo/scans/` (`python3 tools/make_demo_scans.py`). Tesseract ищется в `SIBDOC_TESSERACT`, `.ocr/bin`,
PATH, Homebrew, MacPorts; языки — из папки `tessdata` программы (rus, eng, chi_sim).

**Реальное время и пакетная загрузка (SibDoc 3):** пакет из 20–30 документов одним действием — ZIP (с
вложенными папками и архивами), PDF (несколько документов в одном файле делятся по страницам), Excel, Word
(.docx; .doc — через LibreOffice), картинки (OCR), XML, TXT, письма EML. Тип каждого документа определяется
по правилам `config/intake/classify.yaml` (с объяснением, правится вручную), поля — в единую таблицу
«Пакет.xlsx» с источником и уверенностью каждого значения. Проверка ближайшей отгрузки: декларация со скана ↔
черновик ↔ SAP / 1С ↔ справочники ↔ БД → «Проверка отгрузки.xlsx» (OK / REVIEW / ISSUE, причины RU + 中文).
Без интернета. API `/api/intake/*`, `/api/realtime/*`; демо — `python3 tools/make_demo_package.py`;
подробно — [`docs/REALTIME.md`](docs/REALTIME.md).

**Запуск:** `pip install -r requirements.txt && uvicorn app.server:app --port 8000` → http://localhost:8000
(или `docker compose up -d`). Вход выключен (прототип): `config/system.yaml → auth.disabled`.

**Проверка:** `./verify.sh` (эталон + демо-набор), `pip install -r requirements-dev.txt && pytest`.

**Без сервера:** `python3 run_cli.py --wide … --purchase … --ru-dt "dt/*.xml" --cn-dec 报关单.xlsx`.

| Документ | Для кого |
|---|---|
| `docs/SPECIALIST_GUIDE.md` | специалист пост-контроля: источники, проверки, курсы, журнал, отчёты |
| `docs/REALTIME.md` | реальное время и пакетная загрузка: форматы, классификация, поля, проверка отгрузки, API |
| `docs/CONNECTORS.md` | подключение почты, SFTP, 1С, ЭТРАН, Единого окна, уведомлений |
| `docs/DEPLOY.md` | установка на сервер |
| `docs/OFFLINE.md` | закрытый контур: установка без интернета, курсы и обновления с флешки |
| `docs/AGENT_GUIDE.md` | разработчик: архитектура, API, как добавить проверку |
| `docs/USERS.md` | учётные записи (когда вход включён) |
| `docs/CHANGELOG.md` | что изменилось и почему поменялись эталонные числа |

Настройки: `config/system.yaml` (источники, проверки, коннекторы), `config/rules/*.yaml`
(параметры проверок, без перезапуска), `config/fields.yaml` (словарь полей),
`config/xml_profiles.yaml` (теги XML деклараций), `config/rates.yaml` (курсы).
Демо-набор из вымышленных данных: `data/demo/` (`python3 tools/make_demo.py`).
Рабочие данные (`storage/`) в репозиторий не попадают.
