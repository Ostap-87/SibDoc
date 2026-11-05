from __future__ import annotations
# -*- coding: utf-8 -*-
"""Data-quality детекция по источникам, секциям, блокам и колонкам.

Запускается ДО проверок и отвечает на вопросы:
  - в каком источнике/секции не хватает данных;
  - какая колонка пустая или «мусорная» (текст там, где ждут число);
  - какие строки дублируются по ключу;
  - на какую проверку это влияет.

Каждая находка — Finding(severity, source, section, column, issue,
affected_rows, examples, impact).
"""
import numpy as np
import pandas as pd
from app.models import Finding, Source
from app.normalizers import (container_check_digit_ok, decl_is_lossy, has_mixed_scripts,
                             norm_container, norm_price, norm_text)

# Какие проверки зависят от каких колонок какого источника —
# используется для поля impact ("на какую проверку влияет дефект").
CHECK_COLUMN_DEPS: dict[str, dict[str, list[str]]] = {
    "wide_table": {
        "decl_num": ["completeness", "price", "hs_code", "incoterms", "contracts"],
        "container_num": ["price", "incoterms", "contracts"],
        "contract": ["price", "contracts"],
        "products_price": ["price"],
        "product_code": ["hs_code"],
        "product_name": ["hs_code"],
        "incoterms": ["incoterms"],
        "shipreport_incoterms": ["incoterms"],
        "extra_costs_value": ["incoterms"],
        "storage_location": ["incoterms"],
    },
    "purchase": {
        "Container number": ["price", "contracts"],
        "Price": ["price"],
        "Main contract": ["price", "contracts"],
        "Add contract": ["price", "contracts"],
    },
    "sap_vs_cd": {
        "decl_num": ["completeness"],
    },
    "ru_dt": {
        "decl_ru": ["cross_border"], "container": ["cross_border"], "contract": ["cross_border"],
        "net_weight": ["cross_border"], "total_value": ["cross_border"], "hs_code": ["cross_border"],
        "currency": ["cross_border"], "incoterms": ["cross_border"],
    },
    "cn_dec": {
        "decl_cn": ["cross_border"], "container": ["cross_border"], "contract": ["cross_border"],
        "net_weight": ["cross_border"], "unit_price": ["cross_border"], "hs_code": ["cross_border"],
        "currency": ["cross_border"], "incoterms": ["cross_border"],
    },
    "cd_drafts": {},
    "hs_reference": {
        "hs_code": ["hs_code"],
    },
    "cde_reference": {},
}

CHECK_TITLES = {
    "completeness": "0. Completeness",
    "price": "1. Price",
    "hs_code": "2. HS code",
    "incoterms": "3. Incoterms",
    "decl_elements": "4. Decl. elements",
    "contracts": "5. Contract",
    "cross_border": "6. ДТ ↔ 报关单 ↔ SAP",
}

# Ключи для поиска дублей
KEY_COLUMNS = {
    "wide_table": ["container_num", "decl_num"],
    "purchase": ["Container number"],
    "sap_vs_cd": ["decl_num"],
    "ru_dt": ["decl_ru"],
    "cn_dec": ["decl_cn"],
}

# Ключевые колонки, где проверяется смесь кириллицы/латиницы и контрольная цифра контейнера
CONTAINER_COLUMNS = {"wide_table": ["container_num"], "purchase": ["Container number"],
                     "ru_dt": ["container"], "cn_dec": ["container"]}
MIXED_COLUMNS = {"wide_table": ["contract", "container_num"], "purchase": ["Main contract", "Add contract",
                                                                          "Container number"],
                 "ru_dt": ["contract", "container"], "cn_dec": ["contract", "container"]}
DECL_COLUMNS = {"wide_table": ["decl_num"], "sap_vs_cd": ["decl_num"], "cn_dec": ["decl_cn"]}


def _impact_for(source_slot: str, column: str) -> str:
    checks = CHECK_COLUMN_DEPS.get(source_slot, {}).get(column, [])
    if not checks:
        return "ни одна проверка напрямую не пострадает"
    return "пострадают проверки: " + ", ".join(
        CHECK_TITLES.get(c, c) for c in checks)


def _examples_of(series: pd.Series, max_n: int) -> str:
    vals = [norm_text(v) for v in series.tolist()]
    vals = [v for v in vals if v][:max_n]
    return "; ".join(vals)


def analyze(sources: dict[str, Source], cfg: dict) -> list[Finding]:
    dq = cfg.get("data_quality", {})
    if not dq.get("enabled", True):
        return []

    max_ex = int(cfg.get("output", {}).get("max_examples_per_finding", 5))
    empty_thr = float(dq.get("empty_column_threshold", 0.95))
    sparse_thr = float(dq.get("sparse_column_threshold", 0.30))
    numeric_cols: dict = dq.get("numeric_columns", {})
    src_cfg = cfg.get("sources", {})

    findings: list[Finding] = []

    # --- 0. Обязательные источники отсутствуют вообще -----------------
    for slot, sc in src_cfg.items():
        if sc.get("required") and slot not in sources:
            findings.append(Finding(
                severity="ERROR", source=slot, section="(весь источник)",
                column="", issue=f"Обязательный источник «{sc.get('label', slot)}» не загружен",
                impact="проверки, требующие этот источник, будут пропущены (SKIPPED)"))

    for slot, source in sources.items():
        df = source.df
        sc = src_cfg.get(slot, {})
        sections: dict = sc.get("sections") or {}
        required = list(sc.get("required_columns") or [])

        # --- 1. Отсутствующие обязательные колонки (ERROR) ------------
        for col in required:
            if col not in df.columns:
                sec = next((s for s, cols in sections.items() if col in cols), "(прочее)")
                findings.append(Finding(
                    severity="ERROR", source=slot, section=sec, column=col,
                    issue=f"Обязательная колонка «{col}» отсутствует в файле «{source.name}»",
                    impact=_impact_for(slot, col)))

        # --- 2. Пустые и «редкие» колонки по секциям ------------------
        for sec, cols in sections.items():
            for col in cols:
                if col not in df.columns:
                    continue  # уже поймано выше (если required)
                fill = df[col].map(lambda v: norm_text(v) != "").mean() if len(df) else 0.0
                if fill <= (1 - empty_thr):
                    findings.append(Finding(
                        severity="ERROR" if col in required else "WARNING",
                        source=slot, section=sec, column=col,
                        issue=f"Колонка «{col}» пустая (заполнено {fill:.0%} значений из {len(df)})",
                        affected_rows=int(len(df)),
                        impact=_impact_for(slot, col)))
                elif fill < sparse_thr:
                    findings.append(Finding(
                        severity="WARNING", source=slot, section=sec, column=col,
                        issue=f"Колонка «{col}» заполнена редко ({fill:.0%} значений из {len(df)})",
                        affected_rows=int(len(df) - round(fill * len(df))),
                        impact=_impact_for(slot, col)))

        # --- 3. Числовой мусор: текст там, где ждут число -------------
        for col in numeric_cols.get(slot, []):
            if col not in df.columns:
                continue
            parsed = df[col].map(norm_price)
            present = df[col].map(lambda v: norm_text(v) != "")
            bad = present & parsed.isna()
            if bad.any():
                sec = next((s for s, cols in sections.items() if col in cols), "(прочее)")
                findings.append(Finding(
                    severity="WARNING", source=slot, section=sec, column=col,
                    issue=f"В числовой колонке «{col}» нечисловые значения (возможен мусор/текст)",
                    affected_rows=int(bad.sum()),
                    examples=_examples_of(df.loc[bad, col], max_ex),
                    impact=_impact_for(slot, col)))

        # --- 4. Дубли ключей -------------------------------------------
        if dq.get("duplicate_key_warning", True) and slot not in ("ru_dt", "cn_dec"):
            for key_col in KEY_COLUMNS.get(slot, []):
                if key_col not in df.columns:
                    continue
                keys = df[key_col].map(norm_container)
                keys = keys[keys != ""]
                dup = keys[keys.duplicated(keep=False)]
                if dup.empty:
                    continue
                sec = next((s for s, cols in sections.items() if key_col in cols), "(прочее)")
                n_dup = int(dup.nunique())
                sev = "INFO" if slot == "purchase" else "WARNING"
                note = ("допустимо: повторные строки обрабатываются правилом 2.3.1"
                        if slot == "purchase" else
                        "проверьте, не задвоены ли строки в выгрузке")
                findings.append(Finding(
                    severity=sev, source=slot, section=sec, column=key_col,
                    issue=f"Дубли ключа «{key_col}»: {n_dup} уникальных значений встречаются >1 раза ({note})",
                    affected_rows=int(len(dup)),
                    examples="; ".join(sorted(dup.unique())[:max_ex])))

        # --- 5. Контрольная цифра контейнера (ISO 6346) -------------------
        if dq.get("container_check_digit", True):
            for col in CONTAINER_COLUMNS.get(slot, []):
                if col not in df.columns:
                    continue
                vals = [c for v in df[col].dropna() for c in str(v).replace(",", ";").split(";")]
                vals = sorted({norm_container(v) for v in vals} - {""})
                bad = [v for v in vals if not container_check_digit_ok(v)]
                if bad:
                    findings.append(Finding(
                        severity="WARNING", source=slot, section="(ключи)", column=col,
                        issue=f"Номер контейнера не проходит проверку контрольной цифры ISO 6346 "
                              f"({len(bad)} из {len(vals)}) — возможна опечатка или ошибка распознавания",
                        affected_rows=len(bad), examples="; ".join(bad[:max_ex]),
                        impact=_impact_for(slot, col)))

        # --- 6. Кириллица и латиница вперемешку в ключах -------------------
        if dq.get("mixed_scripts_warning", True):
            for col in MIXED_COLUMNS.get(slot, []):
                if col not in df.columns:
                    continue
                mixed = df[col].dropna().map(str)
                mixed = mixed[mixed.map(has_mixed_scripts)]
                if len(mixed):
                    findings.append(Finding(
                        severity="INFO", source=slot, section="(ключи)", column=col,
                        issue="В значениях смешаны кириллица и латиница (например, «СХ» и «CX») — "
                              "при сверке они считаются одной буквой, но в источнике лучше исправить",
                        affected_rows=int(len(mixed)), examples=_examples_of(mixed, max_ex),
                        impact=_impact_for(slot, col)))

        # --- 7. Номер декларации хранится числом (теряются последние цифры) --
        for col in DECL_COLUMNS.get(slot, []):
            if col in df.columns:
                lossy = df[col].dropna()
                lossy = lossy[lossy.map(decl_is_lossy)]
                if len(lossy):
                    findings.append(Finding(
                        severity="WARNING", source=slot, section="(ключи)", column=col,
                        issue="Номер декларации записан в Excel числом: в 18-значном номере могли "
                              "потеряться последние цифры — храните номер как текст",
                        affected_rows=int(len(lossy)), examples=_examples_of(lossy, max_ex),
                        impact=_impact_for(slot, col)))

    order = {"ERROR": 0, "WARNING": 1, "INFO": 2}
    findings.sort(key=lambda f: (order.get(f.severity, 3), f.source, f.section, f.column))
    return findings
