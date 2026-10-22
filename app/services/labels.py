from __future__ import annotations
# -*- coding: utf-8 -*-
"""Подписи для отчётов и интерфейса: проверки, статусы, колонки результатов (RU / ZH / EN).

Колонки, которые есть в едином словаре полей (config/fields.yaml), подписываются
оттуда; здесь — только колонки результатов проверок.
"""
from app import fields as fdict

CHECKS = {
    "completeness": ("0. Полнота: CD есть в обеих таблицах", "0. 完整性", "0. Completeness"),
    "price": ("1. Цена CD vs SAP", "1. 价格", "1. Price"),
    "hs_code": ("2. HS-код по марке", "2. 商品编号", "2. HS code"),
    "incoterms": ("3. Условия поставки", "3. 成交方式", "3. Incoterms"),
    "decl_elements": ("4. Марка и элементы декларации", "4. 申报要素", "4. Decl. elements"),
    "contracts": ("5. Контракт CD vs SAP", "5. 合同号", "5. Contract"),
    "cross_border": ("6. ДТ РФ ↔ 报关单 ↔ SAP", "6. 俄报关单 ↔ 中国报关单 ↔ SAP", "6. RU DT ↔ CN dec ↔ SAP"),
}
SHEETS = {"completeness": "0 Полнота", "price": "1 Цена", "hs_code": "2 HS-код", "incoterms": "3 Условия",
          "decl_elements": "4 Элементы", "contracts": "5 Контракт", "cross_border": "6 ДТ-报关单-SAP"}

STATUS = {
    "OK": ("совпадает", "一致"), "ISSUE": ("расхождение", "不一致"),
    "REVIEW": ("проверить", "需复核"), "SKIPPED": ("пропущено", "跳过"),
    "ERROR": ("ошибка", "错误"), "WARNING": ("предупреждение", "警告"), "INFO": ("информация", "信息"),
}

COLUMNS = {
    "check": ("Проверка", "检查"), "status": ("Статус", "状态"), "detail": ("Пояснение", "说明"),
    "container": ("Контейнер", "集装箱号"), "containers": ("Контейнеры", "集装箱号"),
    "decl_num": ("Декларация КНР", "报关单号"), "decl_cn": ("Декларация КНР", "报关单号"),
    "decl_ru": ("ДТ РФ", "俄罗斯报关单"), "item_no": ("№ товара", "项号"), "level": ("Что сверяется", "核对层级"),
    "contract": ("Контракт CD", "合同号"), "cd_contract": ("Контракт CD", "报关单合同号"),
    "cd_contract_parts": ("Части склеенного контракта", "合同号拆分"),
    "sap_contract": ("Контракт SAP", "SAP合同号"), "contract_ru": ("Контракт ДТ", "俄报关单合同号"),
    "contract_sap": ("Контракт SAP", "SAP合同号"), "contract_cn": ("Контракт 报关单", "报关单合同号"),
    "cd_price": ("Цена CD (как в декларации)", "报关单单价"), "cd_unit": ("Ед. цены CD", "单价单位"),
    "cd_price_per_t": ("Цена CD за т", "报关单单价(每吨)"), "cd_currency": ("Валюта CD", "报关单币制"),
    "sap_price": ("Цена SAP за т", "SAP价格(每吨)"), "sap_currency": ("Валюта SAP", "SAP币制"),
    "sap_lines": ("Строки SAP по контейнеру", "SAP行"), "fx_rate": ("Курс пересчёта", "汇率"),
    "fx_rate_ru": ("Курс пересчёта ДТ", "汇率(俄)"),
    "cd_hs_code": ("Код CD", "报关单商品编号"), "expected_hs": ("Утверждённый код", "核定编号"),
    "basis": ("Как определена марка", "依据"), "material": ("Материал SAP", "SAP物料号"),
    "product_name": ("Наименование", "商品名称"), "storage_location": ("Склад", "存放地点"),
    "cd_incoterms": ("Условия CD", "报关单成交方式"), "sap_incoterms": ("Условия SAP", "SAP贸易术语"),
    "cd_term": ("Условие CD", "报关单术语"), "sap_term": ("Условие SAP", "SAP术语"),
    "cd_place": ("Место CD", "报关单地点"), "sap_place": ("Место SAP", "SAP地点"),
    "source": ("Источник", "来源"), "declared_model": ("Заявленная марка", "申报型号"),
    "declared_elements": ("Заявленные элементы", "申报要素"), "reference_model": ("Марка в мастер-данных", "主数据型号"),
    "reference_elements": ("Элементы в мастер-данных", "主数据申报要素"),
    "in_wide": ("Есть в wide table", "宽表中"), "in_manual": ("Есть в ручной таблице", "手工表中"),
    "hs_ru": ("Код ДТ", "俄报关单编号"), "hs_cn": ("Код 报关单", "中国报关单编号"),
    "net_ru_kg": ("Нетто ДТ, кг", "俄净重(千克)"), "net_sap_kg": ("Нетто SAP, кг", "SAP净重(千克)"),
    "net_cn_kg": ("Нетто 报关单, кг", "中国净重(千克)"), "price_ru_t": ("Цена ДТ за т", "俄单价(每吨)"),
    "price_sap_t": ("Цена SAP за т", "SAP单价(每吨)"), "price_cn_t": ("Цена 报关单 за т", "中国单价(每吨)"),
    "currency_ru": ("Валюта ДТ", "俄币制"), "currency_sap": ("Валюта SAP", "SAP币制"),
    "currency_cn": ("Валюта 报关单", "中国币制"), "incoterms_ru": ("Условия ДТ", "俄成交方式"),
    "incoterms_cn": ("Условия 报关单", "中国成交方式"), "checks": ("Что сверено", "核对项"),
    "approx_matched": ("Совпали приблизительно", "近似匹配"), "direction": ("Направление", "方向"),
    "source_file": ("Файл", "文件"), "incoterms_code": ("Код условий", "成交方式代码"),
    "first_qty": ("Количество в осн. ед.", "法定数量"), "first_unit": ("Осн. единица", "法定单位"),
    "net_weight_total": ("Нетто всего", "总净重"), "gross_weight_total": ("Брутто всего", "总毛重"),
    "currency_rate": ("Курс валюты контракта", "合同汇率"), "procedure": ("Таможенная процедура", "监管方式"),
    "trade_country": ("Страна торговли", "贸易国"), "manufacturer": ("Производитель", "生产厂家"),
    # data quality
    "severity": ("Важность", "严重程度"), "section": ("Секция", "部分"), "column": ("Колонка", "列"),
    "issue": ("Что не так", "问题"), "affected_rows": ("Строк затронуто", "受影响行数"),
    "examples": ("Примеры", "示例"), "impact": ("На какую проверку влияет", "影响"),
    # sources / summary
    "slot": ("Слот", "数据源"), "label": ("Источник", "数据源名称"), "file": ("Файл", "文件"),
    "rows": ("Строк", "行数"), "columns": ("Колонок", "列数"), "read_as": ("Как прочитан", "读取方式"),
    "mapped": ("Колонок по словарю", "按字典识别的列"), "check_id": ("Код проверки", "检查代码"),
    "total": ("Всего", "合计"), "OK": ("OK", "一致"), "ISSUE": ("ISSUE", "不一致"),
    "REVIEW": ("REVIEW", "需复核"), "SKIPPED": ("SKIPPED", "跳过"),
}


def check_title(cid: str, lang: str = "ru") -> str:
    t = CHECKS.get(cid)
    if not t:
        return cid
    return {"ru": t[0], "zh": t[1], "en": t[2]}.get(lang, t[0])


def column(col: str, lang: str = "ru", slot_fields: dict | None = None) -> str:
    if col in COLUMNS:
        ru, zh = COLUMNS[col]
        return zh if lang == "zh" else ru
    return fdict.column_label(col, slot_fields, lang)


def bilingual(col: str, slot_fields: dict | None = None) -> str:
    """«Контейнер / 集装箱号» — для листов, которые читают обе стороны."""
    if col in COLUMNS:
        ru, zh = COLUMNS[col]
        return ru if ru == zh else f"{ru} / {zh}"
    return fdict.bilingual_label(col, slot_fields)


def public() -> dict:
    return {"checks": {k: {"ru": v[0], "zh": v[1], "en": v[2]} for k, v in CHECKS.items()},
            "status": {k: {"ru": v[0], "zh": v[1]} for k, v in STATUS.items()},
            "columns": {k: {"ru": v[0], "zh": v[1]} for k, v in COLUMNS.items()}}
