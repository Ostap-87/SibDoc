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
| POST / GET / DELETE | `/api/scans/upload`, `/preview`, `/excel`, `/to-source` | сканы: OCR, предпросмотр, перенос в `ru_dt`/`cn_dec` |

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
  лист на проверку, «ДТ РФ», «报关单 КНР», «Курсы»; заголовки RU / ZH (`app/services/labels.py`);
  значения, похожие на формулы, пишутся текстом (`safe_cell`).
- `report_pdf.write_pdf(result, cfg, path)` — reportlab, A4 альбомная; латиница/кириллица —
  DejaVu, иероглифы — CJK-шрифт с контурами TrueType (WenQuanYi, YaHei, SimSun, Arial Unicode),
  текст делится на участки (`rich()`); колонки проверок для PDF — `PDF_COLUMNS`.

## 10. Тестирование изменений

```bash
pip install -r requirements-dev.txt
pytest                      # 164 теста: проверки (все случаи аудита), загрузка, XML, отчёты, API, курсы, коннекторы
./verify.sh                 # эталон data/ + демо-набор data/demo/
```

Эталон на `data/` (2.0): цена 46 строк — 41 OK / 5 ISSUE; HS 51 — 33 OK / 18 REVIEW;
условия 51 — 43 OK / 8 REVIEW; элементы 21 — 17 OK / 4 REVIEW; контракты 46 — 43 OK /
0 ISSUE / 3 REVIEW; DQ — 1 ERROR. Изменили логику — сверьте с эталоном и объясните
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
│   ├── checks/              плагины проверок + реестр + _common.py
│   ├── rates/               курсы валют
│   ├── connectors/          коннекторы (первая очередь + задел)
│   ├── notify/              уведомления WeCom / Telegram / почта
│   └── services/            loader / xml_decl / data_quality / engine / runs / labels / report_* / OCR
├── config/                  system.yaml, rules/, fields.yaml, xml_profiles.yaml, rates.yaml, scan_*.yaml
├── references/              встроенные справочники
├── data/                    эталонные данные + demo/ (вымышленные)
├── tools/make_demo.py       генератор демо-набора
├── tests/                   pytest
├── storage/                 рабочие данные (не в git): uploads/, runs/, reports/, rates/, journal.jsonl
├── webui/                   интерфейс (RU / EN / 中文)
└── docs/                    SPECIALIST_GUIDE / CONNECTORS / DEPLOY / AGENT_GUIDE / USERS / CHANGELOG
```
