# Руководство для разработчика / агента

Архитектура, контракты данных и точки расширения PostControl Suite.
Прочтите его перед любыми доработками.

---

## 1. Архитектура

```
┌────────────────────────────────────────────────────────────────┐
│ webui/ (static HTML+JS)         run_cli.py (CLI)               │
│        │                              │                        │
│        ▼ HTTP                         ▼                        │
│ app/server.py (FastAPI) ──────► app/services/engine.py         │
│                                     │                          │
│              ┌──────────────────────┴───────────────────┐      │
│              ▼                                          ▼      │
│   services/data_quality.py              app/checks/* (плагины) │
│   (DQ-детекция: источник→секция→        реестр: register/run   │
│    колонка, Finding с impact)                                  │
│              │                          │                      │
│              ▼                          ▼                      │
│        services/loader.py ◄── config/system.yaml + rules/*.yaml│
│              │                                                 │
│              ▼                                                 │
│   report_excel.py / report_pdf.py ──► storage/reports/         │
└────────────────────────────────────────────────────────────────┘
```

**Поток прогона** (`engine.run`):
1. `data_quality.analyze(sources, cfg)` → `list[Finding]`;
2. по каждой включённой проверке из `cfg["checks"]`: `load_rules()` →
   `registry.run_check()` → `CheckOutput`; исключение внутри проверки
   не роняет прогон — строка ISSUE «Внутренняя ошибка проверки»;
3. `RunResult` → Excel + PDF.

---

## 2. Контракты данных

### Source (`app/models.py`)
```python
Source(slot: str, name: str, df: pd.DataFrame)
```
Один загруженный источник. Слоты перечислены в `config/system.yaml → sources`.

### CheckOutput
```python
CheckOutput(check_id: str, title: str, df: pd.DataFrame, meta: dict)
```
`df` обязан содержать колонки `status` (OK/ISSUE/REVIEW/SKIPPED) и `detail`;
остальные колонки — свободные (попадут в отчёт как есть).

### Finding (data-quality)
```python
Finding(severity,      # "ERROR" | "WARNING" | "INFO"
        source,        # слот источника
        section,       # секция из system.yaml → sources.<slot>.sections
        column,        # колонка или ""
        issue,         # что не так
        affected_rows, # сколько строк затронуто
        examples,      # примеры значений
        impact)        # какая проверка пострадает
```

### Конфигурация
- `config/system.yaml` — **структура**: источники (label, required, sections,
  required_columns, builtin), проверки (enabled, rules-файл), пороги DQ, вывод.
  Меняется разработчиком; требует перезапуска.
- `config/rules/*.yaml` — **параметры**: меняет специалист через API/UI
  (`PUT /api/rules/{file}` → `config_loader.save_rules()`), без перезапуска.

---

## 3. Как добавить новую проверку (плагин)

**Шаг 1.** Создайте `app/checks/my_check.py`:

```python
import pandas as pd
from app.models import CheckOutput, Status
from app.normalizers import norm_container          # используйте ТОЛЬКО их
from app.checks import register

def run(sources: dict, rules: dict, cfg: dict) -> CheckOutput:
    wide = sources.get("wide_table")
    if wide is None:
        return CheckOutput("my_check", "6. My check", pd.DataFrame([{
            "check": "6. My check", "status": Status.SKIPPED.value,
            "detail": "Нужна wide table."}]))
    threshold = rules.get("my_threshold", 10)        # параметр из rules
    rows = []
    for _, r in wide.df.iterrows():
        # ... логика ...
        rows.append({"check": "6. My check", "status": Status.OK.value,
                     "detail": "..."})
    return CheckOutput("my_check", "6. My check", pd.DataFrame(rows))

register("my_check", "6. My check", run)
```

**Шаг 2.** Зарегистрируйте импорт в `app/checks/__init__.py`:
```python
from app.checks import (..., my_check)
```

**Шаг 3.** Включите в `config/system.yaml`:
```yaml
checks:
  my_check: {enabled: true, rules: "my_check.yaml"}
```

**Шаг 4.** Создайте `config/rules/my_check.yaml` с параметрами.

Всё: проверка появится в CLI, API, UI, Excel и PDF автоматически.

**Правила для проверок:**
- только `app/normalizers.py` для очистки значений — нормализация не должна
  расходиться между проверками;
- отсутствие источника → одна строка SKIPPED, а не исключение;
- сортировка: ISSUE → REVIEW → SKIPPED → OK (см. приём `_o` в `price.py`);
- один контейнер/одна CD = одна строка результата, если проверка контейнерная.

---

## 4. Как расширить DQ-детекцию

`app/services/data_quality.py` → `analyze(sources, cfg)` возвращает `list[Finding]`.

**Готовые рычаги (без кода):**
- новая секция/колонка → `config/system.yaml → sources.<slot>.sections`
  (пустота/редкость начнут детектиться автоматически);
- новая обязательная колонка → `required_columns`;
- новая числовая колонка → `data_quality.numeric_columns.<slot>`;
- пороги → `empty_column_threshold`, `sparse_column_threshold`.

**Новое правило (код):** добавьте блок в `analyze()` по образцу существующих
(обязательные колонки → ERROR; пустые/редкие → ERROR/WARNING; числовой мусор;
дубли ключей). Каждая находка обязана указывать `impact`.

**Связь колонка→проверка:** словарь `CHECK_COLUMN_DEPS` — именно он заполняет
поле `impact` («пострадают проверки: …»). При добавлении проверки дополните его.

---

## 5. API (для интеграций и автотестов)

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/api/health`, `/api/config` | жив ли сервис; источники, проверки, режим входа, допустимые форматы |
| GET | `/api/fields`, `/api/labels` | словарь полей RU/EN/ZH; подписи проверок, статусов, колонок |
| GET | `/api/sources` | состояние слотов: файлы, как прочитаны (формат, лист, кодировка), распознанные колонки |
| POST | `/api/upload/{slot}?append=` | загрузить файл(ы): поле `file` или несколько `files`; xlsx/xls/csv/xml/zip |
| DELETE | `/api/upload/{slot}` | очистить слот |
| GET | `/api/sources/{slot}/preview?limit=&lang=` | таблица источника с подписями колонок |
| GET | `/api/declarations/{ru_dt\|cn_dec}` | декларации карточками: шапка + товары |
| GET | `/api/package-docs/{invoice\|packing\|transport\|conformity}` | документы пакета ВЭД карточками: шапка + позиции |
| GET / PUT | `/api/rules/{file}` | правила (+ `history`); PUT пишет журнал с diff, сохраняет прежнюю версию |
| GET / POST | `/api/rules/{file}/history/{ver}`, `/restore/{ver}` | просмотр и откат версии |
| POST | `/api/run` | прогон: номер, сводка, DQ, строки проверок с подписями колонок, курсы, ссылки на отчёты |
| GET | `/api/runs`, `/api/runs/{id}/excel\|pdf` | история прогонов и их отчёты |
| GET | `/api/report/excel`, `/api/report/pdf` | последний отчёт |
| GET | `/api/rates?date=&source=` | курсы на дату (официальные + ручные) |
| POST | `/api/rates/refresh` | скачать курс источника на дату |
| GET / POST / DELETE | `/api/rates/manual[/{id}]` | ручные курсы |
| GET / PUT | `/api/rates/settings` | режим `official` / `manual_override` и др. |
| GET | `/api/rates/convert?amount&from_ccy&to_ccy&date&side` | пересчёт |
| GET | `/api/connectors`, `/api/connectors/catalog` | настроенные коннекторы; все типы |
| POST | `/api/connectors/{id}/test`, `/pull` | проверить доступ; забрать данные в слоты |
| POST | `/api/reload` | опросить включённые коннекторы и перечитать сохранённые файлы |
| GET / POST | `/api/notifications`, `/{id}/test` | каналы уведомлений, тестовое сообщение |
| GET | `/api/journal?day&q&kind&date_from&date_to`, `/kinds`, `/days`, `/export` | журнал, виды событий, календарь, CSV |
| POST / GET / DELETE | `/api/scans/upload?kind=`, `/preview`, `/excel`, `/to-source` | сканы: OCR, предпросмотр, перенос в `ru_dt`/`cn_dec` и слоты пакета ВЭД (`kind`: auto, declaration, invoice, packing, transport, conformity) |
| GET | `/api/offline` | SibDoc3: закрытый контур — включён ли, разрешённые адреса (`app/netguard.py`) |
| POST / GET | `/api/rates/import`, `/api/rates/template` | курсы из файлов (ЦБ РФ XML, CFETS, шаблон); шаблон Excel |
| POST / GET | `/api/offline/update?apply=`, `/api/offline/updates` | обновление с носителя (admin): проверка / установка, история |

Исходящие сетевые обращения (SibDoc3: закрытый контур): любой новый код, который ходит в сеть,
вызывает `app.netguard.check(url_или_хост, "что это")` до соединения или берёт `requests` через
`netguard.guarded_requests(...)` (у коннекторов и каналов уведомлений — `self._requests()`); список
обращений — `docs/OFFLINE.md`, раздел 2.

## 6. Данные: словарь полей, загрузка, XML

- **`config/fields.yaml` + `app/fields.py`** — единый словарь (поле → подписи RU/EN/ZH,
  тип, синонимы колонок и тегов XML). `map_columns(df, slot_fields)` переименовывает
  распознанные колонки в имена, которые ждёт слот (`sources.<slot>.fields`);
  `canonicalize(df)` — для слотов с `canonical: true` (`ru_dt`, `cn_dec`).
- **`app/services/loader.py`** — `read_table(bytes, name, slot, cfg)`: кодировки, разделители,
  выбор листа и строки заголовков, `.xls`, XML, ZIP; `read_many(files, slot, cfg)` — несколько
  файлов в слот. `Source.info` хранит, как прочитан файл.
- **`config/xml_profiles.yaml` + `app/services/xml_decl.py`** — разбор XML деклараций по
  профилям (теги на поле, перекодировки валют / условий / единиц, контейнеры, контракт из
  документов 03011). Пространства имён игнорируются, windows-1251 поддерживается, XML с
  DOCTYPE/ENTITY отклоняется. Новый формат = новый профиль, без кода.
- **ВЭД-пакет (`app/services/package_docs.py`)** — слоты с `package: <вид>` в `system.yaml`
  (`invoice`, `packing`, `transport`, `conformity`) читаются не `read_table`, а
  `package_docs.read_document`: Excel/CSV как сетка ячеек — строка заголовков таблицы (оценка:
  распознанные поля минус значения), блок шапки над / под таблицей («Подпись: значение» или
  подпись и значение в соседних ячейках; «Дата», «Номер», «Итого» — по виду документа),
  строка «Итого» → `*_total`, `aliases` слота (например, `sap_price → unit_price`), тонны → кг;
  PDF/сканы — `ocr_reader.read_scan` + `parse_text_fields(text, kind)` по
  `config/scan_fields.yaml → package` (`detect`, `doc_types`, `fields`, `multi`). Номер документа —
  `NUMBER_FIELD` (`invoice_no`, `packing_no`, `bill_of_lading`, `cert_no`); вид накладной и
  сертификата — `norm_doc_type` (SMGS, CMR, AWB, B/L, ST-1, FORM-A, FORM-E, CO, EAEU-DECL, EAEU-CERT).
  `loader.accepted_for(slot_cfg)` — какие расширения принимает слот. Новый вид документа пакета =
  слот с `package:`, ключи в `NUMBER_FIELD` / `DATE_FIELD` / `KIND_HINTS` и паттерны в
  `scan_fields.yaml → package`.
- **`app/checks/docs_package.py`** — проверка «7. Пакет документов»: документы → словари
  (`_package_docs`, `_ru_docs`, `_cn_docs`), отгрузки — `build_shipments(docs, rules)` (инвойс —
  union-find по номерам, затем контейнеры с ближайшей датой), сертификаты — `_link_certs`; уровни —
  `_inv_pack`, `_inv_decl`, `_pack_tr`, `_tr_decl`, `_cert`; каждая подпроверка — `Subs.add(имя RU/ZH,
  статус, пояснение RU, пояснение ZH)`, строка — `Subs.fill` (`checks`, `status`, `detail`, `detail_zh`).
  `meta.shipments` — комплект по отгрузкам для PDF. Номера документов сравниваются через
  `norm_doc_no`, страны — `norm_country` (`app/normalizers.py`). ДТ ссылается на документы через
  `presented_docs` (все документы гр. 44 из XML, `xml_profiles.yaml → presented_docs.all_field`).
- **`app/checks/_common.py`** — общие помощники проверок: `Cols` (имя колонки поля в слоте),
  `sap_lines` (строки SAP в едином виде), `contract_match` (склейка, основной контракт),
  `fx_convert` (пересчёт через `app.rates`), `sort_df`, `worst`.

## 7. Нормализация (app/normalizers.py)

| Функция | Правило |
|---|---|
| `latinize` | кириллица-«двойники» → латиница (СХ → CX), полноширинные символы → обычные |
| `norm_container` / `container_check_digit_ok` | без пробелов/дефисов, латиница; контрольная цифра ISO 6346 |
| `norm_number` | `9 661`, `9661,00`, `1 234,56`, `1,234.56`, `0,00` → float |
| `price_per_ton` | цена за т по единице (`kg`/`t`/`g`/`lb`), иначе запасной порог |
| `norm_unit` | кг / 千克 / 035 → `kg`; т / 吨 / 070 → `t` |
| `norm_hs`, `norm_decl`, `decl_is_lossy`, `ru_dt_date` | коды и номера деклараций (ДТ РФ `8/6/7`, 报关单 18 цифр, номер-«число» из Excel) |
| `canon_contract`, `split_contracts` | контракт для сравнения (`ignore_chars`), деление склеенных |
| `norm_incoterms` | → (условие, место): синонимы, китайские названия, коды 成交方式 |
| `canon_model`, `norm_date`, `is_zero_or_blank`, `nearly_equal`, `has_mixed_scripts` | марки, даты, «0,00», допуски, смесь алфавитов |
| `norm_doc_no`, `norm_country` | номер документа пакета (без «№», знаков, латиница); страна → ISO alpha-2 (RU / RUS / Россия / 俄罗斯 → RU) |

## 8. Курсы, журнал, прогоны, коннекторы

- **`app/rates/`** — `get_rate`, `convert`, `refresh`, `list_rates`, `set_manual`, `delete_manual`,
  `load_settings` / `save_settings`, `norm_currency`. Источники: `cbr` (ЦБ РФ), `cfets`
  (центральный курс НБК), `china_customs` (месячная ручная таблица), `manual`. Кэш и ручные
  курсы — `storage/rates/`, настройки — `config/rates.yaml`. Тесты — `tests/test_rates.py`.
- **`app/journal.py`** — `log(kind, user, detail, meta)`; виды событий и подписи — `KINDS`;
  `query(...)`, `export_csv(...)`. Новое событие = новый ключ в `KINDS`.
- **`app/services/runs.py`** — номер прогона, отчёты в `storage/runs/<id>/`, копия последнего
  отчёта в `storage/reports/`, история (`keep_runs`).
- **`app/connectors/`** — `Connector.test(user)` / `pull(dest_dir, user)` (наследники реализуют
  `_test` / `_pull`), `stage` = `ready` | `needs_access`, `params` для интерфейса, секреты через
  `secret()` (`*_env`, `${VAR}`), `slot_rules`; `catalog()` — все типы. **`app/notify/`** —
  `send(cfg, event, text, files, user)`, `test_channel`. Подробно — `docs/CONNECTORS.md`.

## 9. Отчёты

- `report_excel.write_excel(result, cfg, path)` — «Сводка», «Источники», «Качество данных»,
  лист на проверку, «ДТ РФ», «报关单 КНР», листы пакета ВЭД (`labels.PACKAGE_SHEETS`), «Курсы»; заголовки RU / ZH (`app/services/labels.py`);
  значения, похожие на формулы, пишутся текстом (`safe_cell`).
- `report_pdf.write_pdf(result, cfg, path)` — reportlab, A4 альбомная; латиница/кириллица —
  DejaVu, иероглифы — CJK-шрифт с контурами TrueType (WenQuanYi, YaHei, SimSun, Arial Unicode),
  текст делится на участки (`rich()`); колонки проверок для PDF — `PDF_COLUMNS`; раздел пакета ВЭД —
  `_package_section` (комплект из `meta.shipments` + поля документов `PKG_COLUMNS`).

## 10. Тестирование изменений

```bash
pip install -r requirements-dev.txt
pytest                      # 206 тестов: проверки (все случаи аудита), загрузка, XML, отчёты, API, курсы, коннекторы, пакет ВЭД
./verify.sh                 # эталон data/ + демо-набор data/demo/
```

Эталон на `data/` (2.0): цена 46 строк — 41 OK / 5 ISSUE; HS 51 — 33 OK / 18 REVIEW;
условия 51 — 43 OK / 8 REVIEW; элементы 21 — 17 OK / 4 REVIEW; контракты 46 — 43 OK /
0 ISSUE / 3 REVIEW; DQ — 1 ERROR; «Пакет документов» на `data/` — SKIPPED (документов пакета нет).
Демо-набор: «Пакет документов» — 37 строк, 28 OK / 7 ISSUE / 2 REVIEW. Изменили логику — сверьте с эталоном и объясните
каждую разницу (пример — `docs/CHANGELOG.md`). Демо-набор пересоздаётся
`python3 tools/make_demo.py` (только вымышленные данные).

## 11. Структура каталогов

```
SibDoc/
├── Dockerfile  docker-compose.yml  requirements*.txt  README.md  run_cli.py  verify.sh
├── app/
│   ├── server.py            FastAPI
│   ├── models.py            Source / CheckOutput / Finding / Status
│   ├── fields.py            единый словарь полей
│   ├── normalizers.py       единая очистка значений
│   ├── config_loader.py     system.yaml + rules/*.yaml (история версий, diff)
│   ├── journal.py           журнал событий
│   ├── security.py          пользователи и сессии (когда вход включён)
│   ├── checks/              плагины проверок + реестр + _common.py (docs_package — пакет ВЭД)
│   ├── rates/               курсы валют
│   ├── connectors/          коннекторы (первая очередь + задел)
│   ├── notify/              уведомления WeCom / Telegram / почта
│   └── services/            loader / xml_decl / package_docs / data_quality / engine / runs / labels / report_* / OCR
├── config/                  system.yaml, rules/, fields.yaml, xml_profiles.yaml, rates.yaml, scan_*.yaml
├── references/              встроенные справочники
├── data/                    эталонные данные + demo/ (вымышленные; demo/package — пакет ВЭД)
├── tools/make_demo.py       генератор демо-набора
├── tests/                   pytest
├── storage/                 рабочие данные (не в git): uploads/, runs/, reports/, rates/, journal.jsonl
├── webui/                   интерфейс (RU / EN / 中文)
└── docs/                    SPECIALIST_GUIDE / CONNECTORS / DEPLOY / AGENT_GUIDE / USERS / CHANGELOG
```
