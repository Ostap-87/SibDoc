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
| GET | `/api/health` | жив ли сервис |
| GET | `/api/config` | источники и проверки |
| GET | `/api/sources` | состояние слотов загрузки |
| POST | `/api/upload/{slot}` | загрузить файл (multipart, поле `file`) |
| DELETE | `/api/upload/{slot}` | очистить слот |
| GET | `/api/rules`, `/api/rules/{file}` | чтение правил |
| PUT | `/api/rules/{file}` | обновление правил (JSON-тело) |
| POST | `/api/run` | прогон: summary + DQ + строки проверок + ссылки на отчёты |
| GET | `/api/report/excel`, `/api/report/pdf` | скачать отчёты |
| GET | `/api/scans/info` | языки OCR и принимаемые форматы |
| POST | `/api/scans/upload` | массовая загрузка сканов (поле `files`, multi) → OCR + извлечение полей |
| GET | `/api/scans/preview?lang=` | строки предпросмотра + локализованные подписи полей |
| GET | `/api/scans/excel` | Excel предпросмотра (значения в ячейках, раскраска) |
| DELETE | `/api/scans` | очистить загруженные сканы |

### OCR-подсистема (app/services/ocr_reader.py, decl_parser.py, scan_mapper.py)

- `ocr_reader.read_scan(fileobj, filename)` — PDF рендерится PyMuPDF в 300 DPI,
  изображения предобрабатываются (grayscale+autocontrast), OCR через
  pytesseract с `lang=rus+eng+chi_sim`. Модели лежат в `tessdata/` пакета
  (офлайн); переопределяются через `TESSDATA_PREFIX`.
- `decl_parser.parse_declaration(text)` — извлечение 8 полей по паттернам из
  `config/scan_fields.yaml`; статусы found/review/missing; кириллица в
  контейнерах приводится к латинице. FIELD_LABELS — подписи полей на 3 языках.
- `scan_mapper` — маппинг поле→колонка Excel из `config/scan_mapping.yaml`;
  `write_preview_excel()` раскрашивает ячейки по статусу.

Расширение: новое поле = паттерны в scan_fields.yaml + подпись в FIELD_LABELS +
колонка в scan_mapping.yaml. Новый язык OCR = скачать `<lang>.traineddata`
в tessdata/ и добавить код в LANGS (ocr_reader) — требует пересборки образа.

Загруженные файлы хранятся в памяти процесса (одна сессия). Для multi-user
замените `_uploads` в `server.py` на хранилище по session-id.

**Пример автопрогона:**
```bash
curl -F "file=@wide.xlsx"  -X POST http://server:8000/api/upload/wide_table
curl -F "file=@pur.xlsx"   -X POST http://server:8000/api/upload/purchase
curl -X POST http://server:8000/api/run
curl -OJ http://server:8000/api/report/excel
```

---

## 6. Нормализация (app/normalizers.py) — почему это важно

| Функция | Правило |
|---|---|
| `norm_container` | верхний регистр, без пробелов |
| `norm_price` | `9 661` / `9661,00` / число → float |
| `scale_cd_price` | `< per_kg_threshold` → ×1000 (CD за кг vs SAP за тонну) |
| `norm_hs` | только цифры |
| `canon_contract` | без пробелов/дефисов, верхний регистр |

Все «расхождения форматов» чинятся здесь, один раз, для всех проверок.

---

## 7. Отчёты

- `report_excel.write_excel(result, cfg, path)` — SUMMARY/SOURCES/DATA_QUALITY
  + лист на проверку; раскраска статусов и severity.
- `report_pdf.write_pdf(result, cfg, path)` — reportlab, landscape A4; кириллица
  через TTF (в Docker-образе ставится `fonts-dejavu-core`); в PDF идут только
  ISSUE/REVIEW строки проверок (полные данные — в Excel).

---

## 8. Тестирование изменений

```bash
# эталонный прогон на примерных данных — числа должны совпасть:
python run_cli.py --wide data/wide_table.xlsx --purchase data/purchase.xlsx
# ожидается: price 32 OK / 5 ISSUE; hs_code 33 OK / 22 REVIEW;
#            incoterms 47 OK / 8 REVIEW; contracts 32 OK / 2 ISSUE / 3 REVIEW;
#            DQ: 1 ERROR (extra_costs_value пустая)
```

Изменили логику — сверьте с этим эталоном и объясните каждую разницу.

## 9. Структура каталогов

```
PostControl Suite/
├── Dockerfile  docker-compose.yml  requirements.txt  README.md  run_cli.py
├── app/
│   ├── server.py            FastAPI
│   ├── models.py            Source / CheckOutput / Finding / Status
│   ├── normalizers.py       единая очистка значений
│   ├── config_loader.py     system.yaml + rules/*.yaml (+ save_rules)
│   ├── checks/              плагины проверок + реестр (__init__.py)
│   └── services/            loader / data_quality / engine / report_excel / report_pdf
├── config/
│   ├── system.yaml          структура (разработчик)
│   └── rules/*.yaml         параметры (специалист, через UI/API)
├── references/              встроенные справочники (hs_codes.csv, declaration_elements.csv)
├── data/                    примеры данных для эталонного прогона
├── storage/reports/         отчёты
├── webui/                   статический интерфейс
└── docs/                    SPECIALIST_GUIDE / AGENT_GUIDE / DEPLOY
```
