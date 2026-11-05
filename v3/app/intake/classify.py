from __future__ import annotations
# -*- coding: utf-8 -*-
"""Пакетная загрузка, шаг 2: тип документа по правилам config/intake/classify.yaml.

Без сети и без обученных моделей: у каждого признака (слово RU/EN/ZH, заголовок,
корневой тег XML, поле словаря в заголовке таблицы, имя файла, формат) есть вес,
баллы складываются, побеждает тип с наибольшей суммой. Причины выбора
возвращаются вместе с типом — специалист видит, почему документ стал «инвойсом».

Уверенность = «сила» × «отрыв»:
    сила  = min(1, баллы победителя / strong_score)
    отрыв = 0.5 + 0.5 × (баллы победителя − баллы второго) / баллы победителя
Меньше min_score баллов — тип «Прочее» с низкой уверенностью (нужна проверка).
"""
import functools
import os
import re
import unicodedata
from dataclasses import dataclass, field

import yaml

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config", "intake", "classify.yaml")
DEFAULT_SETTINGS = {"min_score": 4, "strong_score": 14, "review_below": 0.6, "title_lines": 6,
                    "title_weight": 5, "xml_weight": 15, "keyword_cap": 30}
_CJK_SPACE = re.compile(r"(?<=[⺀-鿿豈-￯])\s+(?=[⺀-鿿豈-￯])")
_WORD = "0-9a-zа-я"


def prepare(text: str) -> str:
    """Текст для поиска признаков: NFKC, нижний регистр, ё -> е, пробелы между иероглифами убраны."""
    s = unicodedata.normalize("NFKC", str(text or "")).lower().replace("ё", "е").replace("\xa0", " ")
    s = re.sub(r"[ \t]+", " ", s)
    return _CJK_SPACE.sub("", s)


def compile_phrase(phrase: str) -> re.Pattern:
    """«packing list» -> регулярное выражение с границами слов; «re:…» — как есть."""
    phrase = str(phrase)
    if phrase.startswith("re:"):
        return re.compile(phrase[3:], re.I | re.M)
    p = prepare(phrase).strip()
    words = [w for w in re.split(r"\s+", p) if w]
    body = r"\s*".join(re.escape(w) for w in words)
    if words and re.match(f"[{_WORD}]", words[0][0]):
        body = f"(?<![{_WORD}])" + body
    if words and re.match(f"[{_WORD}]", words[-1][-1]):
        body += f"(?![{_WORD}])"
    return re.compile(body, re.I | re.M)


@dataclass
class TypeRule:
    type: str
    label: str
    label_en: str
    label_zh: str
    keywords: list = field(default_factory=list)    # [(фраза, regex, вес)]
    nots: list = field(default_factory=list)
    titles: list = field(default_factory=list)      # [(фраза, regex)]
    headers: dict = field(default_factory=dict)
    xml_roots: set = field(default_factory=set)
    filename: list = field(default_factory=list)
    formats: dict = field(default_factory=dict)
    ids: list = field(default_factory=list)          # [regex]


def _pairs(d) -> list:
    out = []
    for k, w in (d or {}).items():
        try:
            out.append((str(k), compile_phrase(k), float(w)))
        except re.error as exc:
            raise ValueError(f"classify.yaml: ошибка в выражении «{k}»: {exc}")
    return out


@functools.lru_cache(maxsize=4)
def _load(path: str, mtime: float) -> tuple[dict, dict]:
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    settings = {**DEFAULT_SETTINGS, **(data.get("settings") or {})}
    rules: dict[str, TypeRule] = {}
    for tid, t in (data.get("types") or {}).items():
        t = t or {}
        rules[tid] = TypeRule(
            type=tid, label=t.get("label", tid), label_en=t.get("label_en", t.get("label", tid)),
            label_zh=t.get("label_zh", t.get("label", tid)),
            keywords=_pairs(t.get("keywords")), nots=_pairs(t.get("not")),
            titles=[(str(x), compile_phrase(x)) for x in t.get("titles") or []],
            headers={str(k): float(v) for k, v in (t.get("headers") or {}).items()},
            xml_roots={str(x).lower() for x in t.get("xml_roots") or []},
            filename=_pairs(t.get("filename")),
            formats={str(k): float(v) for k, v in (t.get("formats") or {}).items()},
            ids=[re.compile(x, re.I | re.M) for x in t.get("id") or []])
    if "other" not in rules:
        rules["other"] = TypeRule("other", "Прочее", "Other", "其他")
    return settings, rules


def config(path: str | None = None) -> tuple[dict, dict]:
    path = path or CONFIG_PATH
    return _load(path, os.path.getmtime(path) if os.path.exists(path) else 0.0)


def types_public() -> list[dict]:
    _, rules = config()
    return [{"type": r.type, "label": r.label, "label_en": r.label_en, "label_zh": r.label_zh}
            for r in rules.values()]


def type_label(t: str, lang: str = "ru") -> str:
    r = config()[1].get(t)
    if r is None:
        return t
    return {"en": r.label_en, "zh": r.label_zh}.get(lang, r.label)


@dataclass
class Features:
    text: str = ""
    top: str = ""                # первые строки (шапка) каждой страницы / листа + имена листов
    filename: str = ""
    fmt: str = ""
    header_fields: set = field(default_factory=set)
    xml_root: str = ""


@dataclass
class Classification:
    type: str
    score: float
    confidence: float
    scores: dict
    reasons: list                 # причины для победившего типа
    title_type: str = ""          # тип по заголовку в шапке (для деления PDF на документы)
    doc_id: str = ""              # номер документа по шаблону id

    def as_dict(self) -> dict:
        return {"type": self.type, "score": self.score, "confidence": self.confidence,
                "scores": {k: v for k, v in sorted(self.scores.items(), key=lambda kv: -kv[1]) if v},
                "reasons": self.reasons, "title_type": self.title_type, "doc_id": self.doc_id}


def _score_type(rule: TypeRule, f: Features, text: str, top: str, fname: str, st: dict) -> tuple[float, list, bool]:
    score, reasons, kw, titled = 0.0, [], 0.0, False
    for raw, rx, w in rule.keywords:
        if rx.search(text):
            kw += w
            reasons.append(f"слово «{raw[3:] if raw.startswith('re:') else raw}» +{w:g}")
    if kw > st["keyword_cap"]:
        reasons.append(f"(ключевые слова: не больше {st['keyword_cap']:g})")
        kw = float(st["keyword_cap"])
    score += kw
    for raw, rx in rule.titles:
        if top and rx.search(top):
            score += st["title_weight"]
            titled = True
            reasons.append(f"заголовок «{raw[3:] if raw.startswith('re:') else raw}» +{st['title_weight']:g}")
            break
    for raw, rx, w in rule.nots:
        if rx.search(text):
            score -= w
            reasons.append(f"против: «{raw[3:] if raw.startswith('re:') else raw}» −{w:g}")
    for fld, w in rule.headers.items():
        if fld in f.header_fields:
            score += w
            reasons.append(f"колонка таблицы «{fld}» +{w:g}")
    if f.xml_root and f.xml_root.lower() in rule.xml_roots:
        score += st["xml_weight"]
        reasons.append(f"корневой тег XML {f.xml_root} +{st['xml_weight']:g}")
    for raw, rx, w in rule.filename:
        if fname and rx.search(fname):
            score += w
            reasons.append(f"имя файла +{w:g}")
            break
    if f.fmt in rule.formats:
        score += rule.formats[f.fmt]
        reasons.append(f"формат {f.fmt} +{rule.formats[f.fmt]:g}")
    return round(score, 2), reasons, titled


def find_doc_id(rule: TypeRule | None, raw_text: str) -> str:
    if rule is None:
        return ""
    txt = unicodedata.normalize("NFKC", raw_text or "")
    for rx in rule.ids:
        pos = 0
        while True:                       # поиск с перекрытием: «INVOICE / Invoice No: INV-1»
            m = rx.search(txt, pos)
            if m is None:
                break
            v = (m.group(1) if m.groups() else m.group(0)).strip(" .,;:")
            if re.search(r"\d", v):
                return re.sub(r"\s+", "", v) if re.fullmatch(r"[\d\s/]+", v) else v
            pos = m.start() + 1
    return ""


def classify(f: Features) -> Classification:
    st, rules = config()
    text, top, fname = prepare(f.text), prepare(f.top), prepare(f.filename)
    scores, reasons, titled = {}, {}, {}
    for tid, rule in rules.items():
        scores[tid], reasons[tid], titled[tid] = _score_type(rule, f, text, top, fname, st)
    order = list(rules)
    ranked = sorted(order, key=lambda t: (-scores[t], order.index(t)))
    best = ranked[0]
    s1 = scores[best]
    s2 = max([scores[t] for t in ranked[1:]] or [0.0])
    title_types = [t for t in ranked if titled[t] and scores[t] > 0]
    title_type = title_types[0] if title_types else ""
    if s1 < st["min_score"]:
        conf = round(0.4 * max(0.0, s1) / max(1e-9, st["min_score"]), 2)
        why = reasons.get("other", []) if best == "other" else []
        return Classification("other", s1, min(conf, 0.4), scores,
                              why + [f"тип не определён: лучший результат {s1:g} баллов (нужно {st['min_score']:g})"],
                              title_type=title_type)
    strength = min(1.0, s1 / max(1e-9, st["strong_score"]))
    margin = (s1 - max(0.0, s2)) / s1 if s1 > 0 else 0.0
    conf = round(strength * (0.5 + 0.5 * margin), 2)
    why = list(reasons[best])
    if len(ranked) > 1 and s2 > 0:
        why.append(f"второй вариант: {type_label(ranked[1])} ({s2:g})")
    return Classification(best, s1, conf, scores, why, title_type=title_type,
                          doc_id=find_doc_id(rules.get(best), f.text))
