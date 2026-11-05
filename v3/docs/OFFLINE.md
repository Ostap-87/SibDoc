# SibDoc 3 в закрытом контуре (без интернета)

SibDoc 3 по умолчанию работает в **закрытом контуре**: программа не обращается в интернет. Распознавание
сканов — на этом компьютере (Tesseract, языки rus / eng / chi_sim в комплекте), интерфейс и PDF-отчёты
не используют внешних ресурсов, курсы валют загружаются файлами с флешки или вводятся вручную, справочники
и правила обновляются архивом с носителя, установка — из локального комплекта библиотек. Телеметрии нет.

В шапке интерфейса видно состояние: **«Закрытый контур: интернет не используется»** (зелёная плашка;
при наведении — список разрешённых адресов) или **«Есть доступ в интернет»** (контур выключен).

## 1. Включение и разрешённые адреса

`config/system.yaml`:

```yaml
offline:
  enabled: true                 # закрытый контур (по умолчанию в SibDoc 3)
  allow_hosts:                  # куда можно обращаться внутри контура
    - localhost
    - 127.0.0.0/8
    - ::1
    - 10.0.0.0/8                # подсети организации (CIDR)
    - 172.16.0.0/12
    - 192.168.0.0/16
    - "*.local"
    - 1c-server                 # сервер 1С по имени
    - "*.corp.local"            # все серверы домена организации
  update:
    require_signature: false    # «Обновление с носителя»: только подписанные архивы
    keys_dir: config/update_keys
```

Переменные окружения важнее файла: `SIBDOC_OFFLINE=1` / `SIBDOC_OFFLINE=0` — включить / выключить контур,
`SIBDOC_OFFLINE_ALLOW="sap01,10.20.0.0/16"` — дополнить список (удобно для Docker: `-e SIBDOC_OFFLINE_ALLOW=…`).

Как работает проверка. Перед каждым обращением к другому компьютеру программа вызывает одну функцию
`app/netguard.py → check()`. Адрес берётся из настроек (URL, «сервер:порт», строка подключения к БД)
и сравнивается со списком **как есть, без DNS**: точное имя, маска имени (`*.corp.local`) или подсеть
(`10.0.0.0/8`) для IP-адресов. Если адреса в списке нет, обращение отклоняется сразу — без попытки
соединения и без ожидания таймаута — с понятной ошибкой:

> Закрытый контур: обращение к api.telegram.org (уведомления «Telegram — дежурные») заблокировано — адреса
> нет в списке разрешённых (config/system.yaml → offline.allow_hosts). Интернет в закрытом контуре не используется.

Сервер внутри контура, записанный в настройках **по имени** (`https://1c-server/trade`), нужно добавить
в `allow_hosts` этим же именем (или указывать в настройках IP-адрес из разрешённой подсети). Проверяются
и переходы по редиректу, и «следующая страница» из ответа API: если сервер в контуре перенаправит
наружу, запрос не уйдёт. Пароли из строки подключения в тексте ошибки не показываются.

## 2. Все исходящие обращения программы и что с ними в контуре

| Где | Куда | Когда | В закрытом контуре |
|---|---|---|---|
| Курсы ЦБ РФ (`app/rates/providers.py`) | `www.cbr.ru/scripts/XML_daily.asp` | курса на дату нет в кэше; кнопка «Скачать курс ЦБ РФ» | не скачивается: курсы **из файла** (раздел 5) или ручные; кнопка выключена |
| Курсы CFETS (`app/rates/providers.py`) | `www.chinamoney.com.cn` (CcprHisNew, ccpr.json) | то же | то же |
| Курс таможни КНР | — (ручная таблица по месяцу) | — | работает как раньше; можно загрузить шаблоном |
| Коннектор REST (`rest_api.py`) | `url`, ссылки на следующие страницы | «Проверить», «Обновить данные» | только к адресам из `allow_hosts` |
| SAP OData (`sap_odata.py`) | `base_url`, `token_url` (OAuth2) | то же | SAP внутри контура — работает; SAP BTP в облаке — заблокирован |
| 1С OData (`onec_odata.py`) | `base_url` | то же | публикация 1С в контуре — работает |
| Kingdee, Yonyou (`kingdee.py`, `yonyou.py`) | `server_url`; `auth_url`, `gateway_url` | то же | облачные Yonyou BIP / Kingdee Cloud — заблокированы; локальные — по списку |
| Почта IMAP (`imap_mail.py`) | `host:993/143` | то же | почтовый сервер организации — по списку; Яндекс, Mail.ru и т. п. — заблокированы |
| SFTP (`sftp.py`) | `host:22` | то же | по списку |
| База данных (`database.py`) | сервер из строки SQLAlchemy (`url_env`) | то же | по списку; SQLite (файл) — всегда |
| ЭТРАН, Единое окно КНР, банк (задел) | `service_url`, `api_url` | «Проверить» | режим «папка выгрузок» (`export_dir`) работает без сети |
| Папка (`folder.py`) | локальная или сетевая папка ОС | то же | не проверяется: это диск компьютера / сетевой диск в контуре |
| WeCom (`notify/wecom.py`) | `qyapi.weixin.qq.com` | прогон, ошибка коннектора, «Тест» | заблокировано (ошибка в журнале, прогон не прерывается) |
| Telegram (`notify/telegram.py`) | `api.telegram.org` или свой `api_url` | то же | заблокировано; свой сервер Bot API в контуре — по списку |
| Почта SMTP (`notify/smtp_email.py`) | `host:465/587` | то же | почтовый сервер организации — по списку |
| Корпоративный вход SSO (`security.py`) | `issuer_url` (Entra ID / OIDC) | — | в 3.0.0-dev не реализован (только описание); вход — локальный |
| Окно приложения (`sibdoc_gui.py`) | `127.0.0.1` | запуск | только этот компьютер |
| Веб-интерфейс (`webui/`) | — | — | внешних ссылок, CDN и шрифтов из интернета нет (тест `test_webui_has_no_external_resources`); значок встроен |
| PDF-отчёт (`report_pdf.py`) | — | — | шрифт DejaVu лежит в `assets/fonts/`; китайский — `assets/fonts/cjk.ttc` из комплекта, системный или встроенный STSong-Light |
| OCR (`ocr_reader.py`) | — | — | локально: tesseract + `tessdata/` (rus, eng, chi_sim) |
| `install.sh`, `install.bat` | Docker Hub, apt, PyPI (`docker build`, `pip install`) | установка | `--offline wheelhouse`: библиотеки из комплекта, образ — `docker load` |
| `install_mac.command` | `pypi.org`, зеркала Цинхуа / Alibaba, `python.org` | установка | `--offline wheelhouse`; Python — из `offline/python/*.pkg` |
| `install_ocr_mac.command` | GitHub (micromamba), conda-forge и зеркала | установка | `--offline offline/ocr`: пакеты из комплекта |
| `Dockerfile` | apt-get, PyPI | сборка образа | собирать на компьютере с интернетом и переносить `docker save` |
| README: обновление через `codeload.github.com` | GitHub | обновление программы | в контуре — новый комплект на флешке |

Других обращений наружу в программе нет: проверки, отчёты, журнал, загрузка файлов и сканов работают
только с этим компьютером.

## 3. Подготовка комплекта на компьютере с интернетом

```bash
bash tools/make_offline_bundle.sh                                   # для этого компьютера (надёжнее всего)
bash tools/make_offline_bundle.sh --platforms windows,linux-x86_64 --python 3.11,3.12
bash tools/make_offline_bundle.sh --platforms all --with-ocr --with-python 3.12.10 --with-docker
bash tools/make_offline_bundle.sh --dry-run --platforms all         # только показать план, ничего не качать
```

Windows: `tools\make_offline_bundle.bat`, Mac: двойной клик по `tools/make_offline_bundle.command`
(без параметров — библиотеки для этого компьютера, Tesseract и установщик Python).

Результат — папка `dist/sibdoc3-offline-ГГГГММДД/` и архив `.zip` рядом:

| Что | Где в комплекте |
|---|---|
| программа (как в репозитории, без `storage/`, `.venv`, `.git`) | корень |
| библиотеки Python `*.whl` по платформам | `wheelhouse/<платформа>-py<версия>/` (`windows-py3.12`, `linux-x86_64-py3.12`, `mac-arm64-py3.12` …) |
| библиотеки без готовых wheel (`proxy_tools`, чистый Python) | `wheelhouse/any/` |
| языки распознавания rus / eng / chi_sim | `tessdata/` (уже в программе) |
| шрифты PDF: DejaVu (в программе) и китайский WenQuanYi Zen Hei (если есть на этом компьютере) | `assets/fonts/` |
| Tesseract: установщик Windows (UB Mannheim), пакеты conda-forge для Mac / Linux | `offline/ocr/<платформа>/` (`--with-ocr`) |
| установщики Python для Windows (.exe) и Mac (.pkg) | `offline/python/` (`--with-python`) |
| образ Docker | `offline/docker/postcontrol-suite.tar.gz` (`--with-docker`) |
| что внутри (версии, платформы, предупреждения) | `offline/MANIFEST.json` |
| контрольные суммы всех файлов | `SHA256SUMS` |
| памятка для контура | `ЗАКРЫТЫЙ-КОНТУР.txt` |

Платформы: `native` (этот компьютер, по умолчанию — `pip wheel`, собирает и пакеты без готовых wheel),
`windows`, `linux-x86_64`, `linux-aarch64`, `mac-arm64`, `mac-x86_64`, `all`. Для чужой платформы pip
скачивает только готовые wheel (`pip download --platform … --python-version … --only-binary=:all:`).
pip при этом проверяет условия «только для Windows / Mac» по сборочному компьютеру, поэтому такие
зависимости (`colorama`, `pythonnet` — Windows; `pyobjc-*` — Mac) докачиваются отдельным списком.
Надёжнее всего собрать комплект на компьютере с той же ОС и версией Python, что в контуре, и там же
проверить установку. Версию Python в контуре и в комплекте выбирайте одинаковую (`--python 3.12` и
установщик `--with-python 3.12.10`). Объём: библиотеки ~150–250 МБ на платформу и версию Python,
Tesseract ~200 МБ на платформу, образ Docker ~0,6–1 ГБ.

Проверить комплект после переноса: `sha256sum -c SHA256SUMS` (Linux), `shasum -a 256 -c SHA256SUMS` (Mac)
или `python3 tools/make_offline_bundle.py --check <папка>` (любая ОС). `install.sh` проверяет суммы сам.

## 4. Установка в контуре

1. Скопировать папку комплекта (или распаковать `.zip`) на компьютер в контуре.
2. Python 3.10+ (Mac — 3.11+), если его нет: Windows — `offline/python/python-*-amd64.exe` (отметить «Add
   python.exe to PATH»), Mac — `offline/python/python-*.pkg`, Linux — `python3` и `python3-venv` из
   дистрибутива (локальный репозиторий или DVD; Astra Linux, РЕД ОС, Debian, Ubuntu).
3. Установка:

| ОС | Команда |
|---|---|
| Linux | `bash install.sh --offline wheelhouse` |
| Windows | `install.bat --offline wheelhouse` |
| Mac | `bash install_mac.command --offline wheelhouse` |

Папку `./wheelhouse` установщики находят и сами; путь можно задать и переменной `SIBDOC_OFFLINE_DIR`.
В этом режиме pip не обращается к PyPI (`--no-index`, `PIP_NO_INDEX=1`, без проверки новой версии pip).

### Docker в контуре

Образ собирается на компьютере с интернетом (`--with-docker` или вручную) и переносится файлом:

```bash
# компьютер с интернетом
docker build -t postcontrol-suite:latest .
docker save postcontrol-suite:latest | gzip > postcontrol-suite.tar.gz
# контур
docker load -i postcontrol-suite.tar.gz
docker run -d -p 8000:8000 --name postcontrol --restart unless-stopped \
  -v "$PWD/config:/srv/postcontrol/config" -v "$PWD/references:/srv/postcontrol/references" \
  -v "$PWD/storage:/srv/postcontrol/storage" postcontrol-suite
```

`install.sh --offline` делает это сам: найдёт `offline/docker/*.tar.gz`, выполнит `docker load` и запустит
контейнер; `docker build` в контуре не запускается (ему нужен интернет). В образе уже есть Tesseract и
шрифты (DejaVu, WenQuanYi). `docker compose up -d` тоже работает, если образ загружен: в
`docker-compose.yml` замените `build: .` на `image: postcontrol-suite:latest`.

## 5. Курсы валют без интернета

Раздел «Курсы валют» → **«Курсы из файла (флешка)»** (или `POST /api/rates/import`, поле `files`, можно
несколько файлов и zip). Формат определяется по содержимому:

| Формат | Где взять на компьютере с интернетом |
|---|---|
| ЦБ РФ, курсы на дату (все валюты) | `https://www.cbr.ru/scripts/XML_daily.asp?date_req=15/10/2026` → «Сохранить как» |
| ЦБ РФ, одна валюта за период | `https://www.cbr.ru/scripts/XML_dynamic.asp?date_req1=01/10/2026&date_req2=31/10/2026&VAL_NM_RQ=R01235` (R01235 — USD, R01239 — EUR, R01375 — CNY, R01820 — JPY, R01335 — KZT, …); валюта определяется по коду ЦБ или задаётся полем «валюта» |
| CFETS 中间价 (Excel / CSV) | chinamoney.com.cn → 人民币汇率中间价 → 历史数据 → выгрузка (колонки 日期, USD/CNY, 100JPY/CNY, CNY/RUB …) |
| Шаблон SibDoc «дата, валюта, курс, источник» | кнопка «Шаблон Excel» (`GET /api/rates/template`); источник: `cbr` (рублей за номинал), `cfets` (юаней за номинал), `china_customs` (месячный курс таможни КНР), `manual` (ручной, нужна колонка «к валюте») |

Курсы ЦБ РФ и CFETS из файла попадают в тот же кэш, что и скачанные (`storage/rates/cache.json`, пометка
«из файла …» в таблице курсов), и используются проверками как официальные. Дни внутри периода файла без
курса (выходные, праздники) берут последний курс до них — как отвечает сам ЦБ РФ. Если запрошена дата
позже последнего файла (в пределах `max_back_days`), берётся ближайший курс из файла с пометкой «курс из
файла от …: на … в загруженных файлах курса нет — проверьте, что он не менялся». Нет курса вовсе — понятная
ошибка «закрытый контур: курсов … нет — загрузите файл курсов …». Курсы таможни КНР и ручные из шаблона
пишутся в таблицу ручных курсов. Ручной ввод курсов работает как раньше. Каждая загрузка — запись
«Курсы: загрузка из файла» в журнале (файл, SHA-256, валюты, период, пропущенные строки).
Файлы курсов можно положить и в архив «Обновления с носителя» (папка `rates/`).

## 6. Сканы (Tesseract) без интернета

Языки rus, eng, chi_sim лежат в `tessdata/` программы. Нужна только сама программа tesseract:

- **Windows:** `offline/ocr/windows/tesseract-ocr-w64-setup-*.exe` (комплект `--with-ocr`) — установить в папку
  по умолчанию `C:\Program Files\Tesseract-OCR`, SibDoc найдёт её сам. Свою ссылку на установщик —
  `--tesseract-win-url` (актуальные версии: github.com/UB-Mannheim/tesseract/wiki).
- **Mac и Linux:** `bash install_ocr_mac.command --offline offline/ocr` — ставит пакеты conda-forge из
  комплекта (micromamba, без сети) в папку `.ocr` программы; `install_mac.command --offline` делает это сам.
- **Linux, другие способы:** образ Docker (Tesseract внутри); пакеты дистрибутива `tesseract-ocr` из
  локального репозитория; или скачать пакеты на компьютере с интернетом той же версии ОС
  (`apt-get download tesseract-ocr libtesseract5 liblept5 …`) и поставить `dpkg -i *.deb`.
- Путь к tesseract можно задать явно: `SIBDOC_TESSERACT=/путь/tesseract`.
- Проверка: `GET /api/scans/info` → `tesseract: true` и языки `chi_sim, eng, rus`. PDF с текстовым слоем
  читаются и без Tesseract.

## 7. Обновление справочников и правил с носителя

Справочники (HS-коды, элементы декларации CDE, контракты — `references/`), правила проверок
(`config/rules/*.yaml`), словари (`config/fields.yaml`, `scan_fields.yaml`, `scan_mapping.yaml`,
`xml_profiles.yaml`) и курсы (`rates/`) обновляются архивом `.zip`:

```bash
# компьютер, где готовят обновление
python3 tools/make_update_package.py --out update-2026-11.zip references/hs_codes.csv \
        config/rules/price.yaml --rates XML_daily.xml --text "HS-коды и правила на ноябрь"
# подпись (по желанию, нужен пакет cryptography)
python3 tools/make_update_package.py --gen-key ключи/          # sibdoc-update.key (хранить у себя) + .pub
python3 tools/make_update_package.py --sign ключи/sibdoc-update.key --out update.zip …
```

В контуре: «Обновление с носителя» → выбрать архив → **«Проверить»** (ничего не меняет) → **«Установить»**
(только admin; `POST /api/offline/update?apply=false|true`). Проверка:

- в архиве обязателен `SHA256SUMS` («<sha256>  <путь>» на каждый файл, формат `sha256sum`); каждый файл
  архива должен быть в списке и совпадать по SHA-256, каждый файл списка — быть в архиве;
- пути — только из перечня выше; `config/system.yaml` архивом не меняется (в нём настройки контура и
  доступа), `..`, абсолютные пути и зашифрованные записи отклоняются;
- YAML и JSON должны читаться;
- подпись `SHA256SUMS.sig` (Ed25519, если есть) проверяется ключами `config/update_keys/*.pub`; при
  `offline.update.require_signature: true` архив без подписи не принимается.

Установка: прежние версии файлов — в `storage/updates/<время>/backup/`, затем замена; если после замены
программа не читает настройки, правила или справочники — все файлы возвращаются назад. Файлы `rates/`
загружаются в курсы. В журнал пишется «Обновление с носителя» (архив, SHA-256, файлы, подпись, копия)
и каждый отказ. Откат вручную: скопировать файлы из `storage/updates/<время>/backup/` на место.

## 8. Что работает и что отключено

| Работает в контуре | Отключено (нужен интернет) |
|---|---|
| загрузка файлов, проверки, отчёты Excel / PDF, журнал | скачивание курсов с сайтов ЦБ РФ и CFETS |
| сканы: текстовый слой PDF и OCR Tesseract (rus / eng / chi_sim) | уведомления WeCom и Telegram (кроме своего Bot API в контуре) |
| курсы из файлов, ручные курсы, таблица таможни КНР | облачные коннекторы (SAP BTP, Yonyou BIP, Kingdee Cloud, внешний REST брокера, внешняя почта) |
| коннекторы к серверам в контуре (1С, SAP, БД, SFTP, почта, REST) по `allow_hosts` | установка и обновление программы из интернета (вместо них — комплект и «Обновление с носителя») |
| уведомления по почте организации (SMTP в контуре) | |
| обновление справочников, правил и курсов с носителя | |

Выключить контур (сервер с доступом в интернет): `offline.enabled: false` или `SIBDOC_OFFLINE=0` —
в шапке будет «Есть доступ в интернет», всё работает как в SibDoc 2.0.1.

## 9. Проверка

`python3 -m pytest -q tests/test_offline.py` — проверка адресов (блокировка внешних, доступ к серверам
локальной сети, без сети и без ожидания), курсы из файлов ЦБ РФ / CFETS / шаблона, комплект (план
`--dry-run`, сборка из тестовых wheel, `--check`, установка `install.sh --offline`), обновление с носителя
(контрольные суммы, запрещённые пути, подпись, откат), отсутствие внешних ресурсов в интерфейсе, шрифты PDF.
