# -*- coding: utf-8 -*-
"""Нормализация кодов валют: ISO-4217, цифровые коды ДТ, коды и названия из 报关单.

Все внешние значения («156», 840.0, «美元», «руб.», «usd ») приводятся к буквенному
коду ISO-4217 в верхнем регистре. Неизвестное значение возвращается как есть
(в верхнем регистре, без пробелов) — проверка курса потом честно скажет,
что такой валюты нет у источника.
"""
from __future__ import annotations

import math
import re
import unicodedata

# Цифровые коды ISO-4217 (графа 22 ДТ и справочники ЕАЭС). Ключ — без ведущих нулей.
ISO_NUMERIC = {
    "8": "ALL", "12": "DZD", "32": "ARS", "36": "AUD", "48": "BHD", "50": "BDT",
    "51": "AMD", "68": "BOB", "104": "MMK", "116": "KHR", "124": "CAD", "144": "LKR",
    "152": "CLP", "156": "CNY", "170": "COP", "192": "CUP", "203": "CZK", "208": "DKK",
    "230": "ETB", "344": "HKD", "348": "HUF", "352": "ISK", "356": "INR", "360": "IDR",
    "364": "IRR", "368": "IQD", "376": "ILS", "392": "JPY", "398": "KZT", "400": "JOD",
    "404": "KES", "410": "KRW", "414": "KWD", "417": "KGS", "418": "LAK", "446": "MOP",
    "458": "MYR", "484": "MXN", "496": "MNT", "498": "MDL", "504": "MAD", "512": "OMR",
    "524": "NPR", "554": "NZD", "566": "NGN", "578": "NOK", "586": "PKR", "604": "PEN",
    "608": "PHP", "634": "QAR", "643": "RUB", "682": "SAR", "702": "SGD", "704": "VND",
    "710": "ZAR", "752": "SEK", "756": "CHF", "764": "THB", "784": "AED", "788": "TND",
    "810": "RUB",  # устаревший код RUR — в старых ДТ и выгрузках SAP
    "818": "EGP", "826": "GBP", "840": "USD", "860": "UZS", "901": "TWD", "933": "BYN",
    "934": "TMT", "941": "RSD", "944": "AZN", "946": "RON", "949": "TRY", "960": "XDR",
    "972": "TJS", "975": "BGN", "978": "EUR", "980": "UAH", "981": "GEL", "985": "PLN",
    "986": "BRL",
}

# Старые цифровые коды валют таможни КНР (币制代码) — только те, что НЕ совпадают
# с цифровыми кодами ISO. Коды 116, 132, 136, 344 у таможни КНР и в ISO означают
# разное, поэтому они читаются как ISO (344 = HKD, а не рубль).
CN_CUSTOMS_NUMERIC = {
    "110": "HKD", "121": "MOP", "133": "KRW", "142": "CNY", "300": "EUR", "302": "DKK",
    "303": "GBP", "326": "NOK", "330": "SEK", "331": "CHF", "501": "CAD", "502": "USD",
    "601": "AUD", "609": "NZD",
}

# Названия валют: китайские (报关单, CFETS), русские (ДТ, SAP), символы.
# Сравнение — по точному совпадению после очистки, не по подстроке
# («白俄罗斯卢布» — белорусский рубль, а не российский).
NAMES = {
    # китайские
    "人民币": "CNY", "人民币元": "CNY", "元": "CNY", "RMB": "CNY",
    "美元": "USD", "美金": "USD", "欧元": "EUR",
    "卢布": "RUB", "俄罗斯卢布": "RUB", "俄卢布": "RUB", "白俄罗斯卢布": "BYN",
    "日元": "JPY", "日本元": "JPY", "港币": "HKD", "港元": "HKD", "英镑": "GBP",
    "澳元": "AUD", "澳大利亚元": "AUD", "新西兰元": "NZD", "新加坡元": "SGD",
    "瑞士法郎": "CHF", "加元": "CAD", "加拿大元": "CAD", "澳门元": "MOP",
    "林吉特": "MYR", "马来西亚林吉特": "MYR", "兰特": "ZAR", "南非兰特": "ZAR",
    "韩元": "KRW", "韩国元": "KRW", "迪拉姆": "AED", "阿联酋迪拉姆": "AED",
    "沙特里亚尔": "SAR", "匈牙利福林": "HUF", "福林": "HUF", "波兰兹罗提": "PLN",
    "兹罗提": "PLN", "丹麦克朗": "DKK", "瑞典克朗": "SEK", "挪威克朗": "NOK",
    "土耳其里拉": "TRY", "墨西哥比索": "MXN", "泰铢": "THB", "泰国铢": "THB",
    "坚戈": "KZT", "哈萨克斯坦坚戈": "KZT", "吉尔吉斯斯坦索姆": "KGS",
    "乌兹别克斯坦苏姆": "UZS", "印度卢比": "INR", "蒙古图格里克": "MNT",
    "越南盾": "VND", "印尼盾": "IDR", "印度尼西亚卢比": "IDR", "新台币": "TWD",
    # русские
    "РУБ": "RUB", "РУБЛЬ": "RUB", "РУБЛИ": "RUB", "РОССИЙСКИЙ РУБЛЬ": "RUB", "RUR": "RUB",
    "ДОЛЛАР": "USD", "ДОЛЛАР США": "USD", "ДОЛЛ США": "USD", "ЕВРО": "EUR",
    "ЮАНЬ": "CNY", "КИТАЙСКИЙ ЮАНЬ": "CNY", "ЮАНЬ ЖЭНЬМИНЬБИ": "CNY", "ТЕНГЕ": "KZT",
    "БЕЛОРУССКИЙ РУБЛЬ": "BYN", "ИЕНА": "JPY", "ЙЕНА": "JPY", "ФУНТ СТЕРЛИНГОВ": "GBP",
    "ГОНКОНГСКИЙ ДОЛЛАР": "HKD",
    # символы
    "$": "USD", "US$": "USD", "€": "EUR", "₽": "RUB", "£": "GBP",
}


def norm_currency(v) -> str:
    """Любое написание валюты -> код ISO-4217 («USD»). Пусто -> «».

    156 / «156» / 156.0 -> CNY; «840» -> USD; «643»/«810»/«RUR» -> RUB;
    «人民币» -> CNY; «美元» -> USD; «俄罗斯卢布» -> RUB; «руб.» -> RUB;
    « usd » -> USD; старые коды таможни КНР 142/502/300 -> CNY/USD/EUR.
    """
    if v is None:
        return ""
    if isinstance(v, bool):
        return ""
    if isinstance(v, float):
        if math.isnan(v):
            return ""
        if v.is_integer():
            v = int(v)
    s = unicodedata.normalize("NFKC", str(v)).strip()
    if not s or s.lower() in ("nan", "none", "null", "-", "—"):
        return ""
    s = re.sub(r"\s+", " ", s).upper()
    # «156.0» из Excel, «036» с ведущими нулями
    m = re.fullmatch(r"0*(\d{1,3})(?:\.0+)?", s)
    if m:
        num = m.group(1)
        if num in ISO_NUMERIC:
            return ISO_NUMERIC[num]
        if num in CN_CUSTOMS_NUMERIC:
            return CN_CUSTOMS_NUMERIC[num]
        return s
    if s in NAMES:
        return NAMES[s]
    key = s.replace(".", "").strip()          # «руб.», «долл. США»
    if key in NAMES:
        return NAMES[key]
    # «USD (доллар США)», «CNY-人民币» -> берём буквенный код в начале
    m = re.match(r"([A-Z]{3})\b", s)
    if m and (len(s) == 3 or not s[3:4].isalpha()):
        return NAMES.get(m.group(1), m.group(1))
    # «人民币(CNY)», «美元（USD）» -> код в скобках, иначе название без скобок
    m = re.search(r"[（(]\s*([A-Z]{3})\s*[)）]", s)
    if m:
        return NAMES.get(m.group(1), m.group(1))
    bare = re.sub(r"[（(].*?[)）]", "", s).strip()
    if bare and bare != s:
        return norm_currency(bare)
    return s


def is_iso(code: str) -> bool:
    """Похоже на буквенный код ISO-4217: ровно три латинские буквы."""
    return bool(re.fullmatch(r"[A-Z]{3}", code or ""))
