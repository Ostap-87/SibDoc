# SibDoc (PostControl Suite) 2.0

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

**Сканы деклараций (OCR):** PNG/JPG/TIFF/PDF-сканы ДТ и 报关单 на русском, английском и китайском;
демо — `data/demo/scans/` (`python3 tools/make_demo_scans.py`). Tesseract ищется в `SIBDOC_TESSERACT`, `.ocr/bin`,
PATH, Homebrew, MacPorts; языки — из папки `tessdata` программы (rus, eng, chi_sim).

**Запуск:** `pip install -r requirements.txt && uvicorn app.server:app --port 8000` → http://localhost:8000
(или `docker compose up -d`). Вход выключен (прототип): `config/system.yaml → auth.disabled`.

**Проверка:** `./verify.sh` (эталон + демо-набор), `pip install -r requirements-dev.txt && pytest`.

**Без сервера:** `python3 run_cli.py --wide … --purchase … --ru-dt "dt/*.xml" --cn-dec 报关单.xlsx`.

| Документ | Для кого |
|---|---|
| `docs/SPECIALIST_GUIDE.md` | специалист пост-контроля: источники, проверки, курсы, журнал, отчёты |
| `docs/CONNECTORS.md` | подключение почты, SFTP, 1С, ЭТРАН, Единого окна, уведомлений |
| `docs/DEPLOY.md` | установка на сервер |
| `docs/AGENT_GUIDE.md` | разработчик: архитектура, API, как добавить проверку |
| `docs/USERS.md` | учётные записи (когда вход включён) |
| `docs/CHANGELOG.md` | что изменилось и почему поменялись эталонные числа |

Настройки: `config/system.yaml` (источники, проверки, коннекторы), `config/rules/*.yaml`
(параметры проверок, без перезапуска), `config/fields.yaml` (словарь полей),
`config/xml_profiles.yaml` (теги XML деклараций), `config/rates.yaml` (курсы).
Демо-набор из вымышленных данных: `data/demo/` (`python3 tools/make_demo.py`).
Рабочие данные (`storage/`) в репозиторий не попадают.
