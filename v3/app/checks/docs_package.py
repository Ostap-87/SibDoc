from __future__ import annotations
# -*- coding: utf-8 -*-
"""Проверка 7: «Пакет документов ВЭД» — сверка всех документов отгрузки.

Документы собираются в отгрузки (shipment) по номеру инвойса и/или номерам контейнеров
(правило group_by): упаковочный лист и сертификат происхождения ссылаются на инвойс,
ДТ РФ — через документы графы 44, транспортный документ и 报关单 — через контейнеры
(при повторном контейнере — отгрузка с ближайшей датой).

По каждой отгрузке (строка = отгрузка × уровень сверки):
  1. комплект: какие из инвойса / упаковочного листа / транспортного документа / ДТ /
     报关单 / сертификата есть (обязательные и ожидаемые — в правилах);
  2. инвойс ↔ упаковочный лист: позиции, количество, нетто, брутто, места;
  3. инвойс ↔ ДТ и инвойс ↔ 报关单: номер инвойса в декларации, сумма и валюта
     (курс — только если валюты разные), условия поставки;
  4. упаковочный лист ↔ транспортный документ: места, брутто (допуск %), контейнеры, пломбы;
  5. транспортный документ ↔ ДТ / 报关单: номер накладной в декларации (если есть в данных);
  6. сертификаты: действует на дату ДТ, код ТН ВЭД (по началу кода) и товар, страна
     в сертификате происхождения против ДТ / 报关单.
Пояснения — по-русски (detail) и по-китайски (detail_zh).
"""
import re

import numpy as np
import pandas as pd

from app.checks import register
from app.checks._common import Cols, fx_convert, norm_currency_safe, skipped, sort_df, split_containers, worst
from app.models import CheckOutput, Status
from app.normalizers import (canon_model, is_blank, nearly_equal, norm_country, norm_date, norm_decl, norm_doc_no,
                             norm_hs, norm_incoterms, norm_number, norm_text, norm_unit, ru_dt_date)
from app.services.package_docs import (DATE_FIELD, KIND_LABELS, NUMBER_FIELD, doc_type_label, norm_doc_type)

TITLE = "7. Пакет документов"
OK, ISSUE, REVIEW, SKIP = Status.OK.value, Status.ISSUE.value, Status.REVIEW.value, Status.SKIPPED.value
PACKAGE_KINDS = ("invoice", "packing", "transport", "conformity")
ORDER = ("invoice", "packing", "transport", "ru_dt", "cn_dec", "conformity")
LEVELS = {
    "set": ("Комплект документов", "单证齐全性"),
    "inv_pack": ("Инвойс ↔ упаковочный лист", "发票 ↔ 装箱单"),
    "inv_ru": ("Инвойс ↔ ДТ", "发票 ↔ 俄罗斯报关单"),
    "inv_cn": ("Инвойс ↔ 报关单", "发票 ↔ 中国报关单"),
    "pack_tr": ("Упаковочный лист ↔ транспортный документ", "装箱单 ↔ 运输单据"),
    "tr_decl": ("Транспортный документ ↔ ДТ / 报关单", "运输单据 ↔ 报关单"),
    "cert": ("Сертификат", "证书"),
}


# ------------------------------------------------------------------ мелочи
def _fmt(v, nd=2) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "—"
    s = f"{v:,.{nd}f}".replace(",", " ")
    return s.rstrip("0").rstrip(".") if "." in s else s


def _dmy(d: str) -> str:
    return f"{d[8:10]}.{d[5:7]}.{d[0:4]}" if d and len(d) == 10 else (d or "—")


def _sum(vals) -> float:
    nums = [v for v in vals if v is not None and not (isinstance(v, float) and np.isnan(v))]
    return float(sum(nums)) if nums else np.nan


def _nan(v) -> bool:
    return v is None or (isinstance(v, float) and np.isnan(v))


def _split(v) -> list[str]:
    if is_blank(v):
        return []
    return [x.strip() for x in re.split(r"[;,，、\n]+", str(v)) if x.strip()]


def _refs(values) -> set[str]:
    """Номера документов из списка «04021 INV-1; 02013 SMGS-7; …» (код вида документа отбрасывается)."""
    out = set()
    for v in values:
        for seg in _split(v):
            for x in (seg, re.sub(r"^\d{5}\s+", "", seg)):
                k = norm_doc_no(x)
                if k:
                    out.add(k)
    return out


class Subs:
    """Подпроверки одной строки: (имя RU, имя ZH, статус, пояснение RU, пояснение ZH)."""

    def __init__(self):
        self.items: list[tuple[str, str, str, str, str]] = []

    def add(self, name: tuple[str, str], status: str, ru: str, zh: str):
        self.items.append((name[0], name[1], status, ru, zh))

    def __bool__(self):
        return bool(self.items)

    def fill(self, rec: dict, level: str, ok_ru: str = "всё совпадает", ok_zh: str = "全部一致") -> dict:
        items = [x for x in self.items if x[2] != SKIP]
        rec["checks"] = "; ".join(f"{n}: {'✓' if st == OK else ('✗' if st == ISSUE else '?')}"
                                  for n, _, st, _, _ in items)
        rec["status"] = worst([x[2] for x in items]) if items else SKIP
        bad = [x for x in items if x[2] in (ISSUE, REVIEW)]
        lv_zh = LEVELS[level][1]
        if bad:
            rec["detail"] = "; ".join(f"{n}: {ru}" for n, _, _, ru, _ in bad)
            rec["detail_zh"] = f"【{lv_zh}】" + "；".join(f"{nz}：{zh}" for _, nz, _, _, zh in bad)
        else:
            rec["detail"] = f"{ok_ru}: " + ", ".join(n for n, *_ in items)
            rec["detail_zh"] = f"【{lv_zh}】{ok_zh}：" + "、".join(nz for _, nz, *_ in items)
        return rec


N_POS, N_QTY, N_NET, N_GROSS, N_PKG = ("позиции", "品项"), ("количество", "数量"), ("нетто", "净重"), \
    ("брутто", "毛重"), ("места", "件数")
N_SUM, N_INC, N_CTN, N_SEAL = ("сумма", "金额"), ("условия поставки", "成交方式"), ("контейнеры", "集装箱"), \
    ("пломбы", "封号")
N_VALID, N_HS, N_GOODS, N_ORIGIN = ("срок действия", "有效期"), ("код ТН ВЭД", "商品编码"), ("товар", "商品"), \
    ("страна происхождения", "原产国")


# ------------------------------------------------------------------ документы пакета
def _first(grp: pd.DataFrame, col: str):
    if col not in grp.columns:
        return None
    for v in grp[col]:
        if not is_blank(v):
            return v
    return None


def _line_amount(r) -> float:
    amt = norm_number(r.get("total_value"))
    if np.isnan(amt):
        p, q = norm_number(r.get("unit_price")), norm_number(r.get("qty"))
        amt = p * q if not (np.isnan(p) or np.isnan(q)) else np.nan
    return amt


LINE_FIELDS = ("item_no", "product_name", "model", "hs_code", "qty", "total_value", "unit_price", "net_weight",
               "gross_weight", "packages")


def _lines(grp: pd.DataFrame) -> list[dict]:
    out = []
    for _, r in grp.iterrows():
        if not any(not is_blank(r.get(f)) for f in LINE_FIELDS if f in grp.columns):
            continue
        out.append({"item": norm_text(r.get("item_no") or "").split(".")[0] if not is_blank(r.get("item_no")) else "",
                    "name": norm_text(r.get("product_name") or ""), "model": norm_text(r.get("model") or ""),
                    "hs": norm_hs(r.get("hs_code") or ""), "qty": norm_number(r.get("qty")),
                    "unit": norm_unit(r.get("unit") or ""), "amount": _line_amount(r),
                    "net": norm_number(r.get("net_weight")), "gross": norm_number(r.get("gross_weight")),
                    "packages": norm_number(r.get("packages")), "containers": split_containers(r.get("container"))})
    return out


def _seals(grp: pd.DataFrame) -> dict[str, set]:
    out: dict[str, set] = {}
    if "seal_no" not in grp.columns:
        return out
    for _, r in grp.iterrows():
        seals = [norm_doc_no(s) for s in _split(r.get("seal_no")) if norm_doc_no(s)]
        if not seals:
            continue
        conts = split_containers(r.get("container"))
        if len(conts) == len(seals) and len(conts) > 1:
            for c, s in zip(conts, seals):
                out.setdefault(c, set()).add(s)
        else:
            for c in conts or [""]:
                out.setdefault(c, set()).update(seals)
    return out


def _package_docs(src, kind: str) -> list[dict]:
    if src is None or src.df.empty:
        return []
    df = src.df.reset_index(drop=True)
    numf = NUMBER_FIELD[kind]
    keys = []
    for i, r in df.iterrows():
        num = norm_doc_no(r.get(numf)) if numf in df.columns else ""
        inv = norm_doc_no(r.get("invoice_no")) if "invoice_no" in df.columns else ""
        if num:
            keys.append(f"n:{num}")
        elif kind == "packing" and inv:
            keys.append(f"i:{inv}")
        elif kind == "conformity":
            keys.append(f"r:{i}")
        else:
            keys.append(f"f:{norm_text(r.get('source_file') or '')}")
    df["_k"] = keys
    docs = []
    for _, grp in df.groupby("_k", sort=False):
        number = norm_text(_first(grp, numf) or "")
        lines = _lines(grp)
        conts = []
        for v in grp["container"] if "container" in grp.columns else []:
            for c in split_containers(v):
                if c not in conts:
                    conts.append(c)
        for ln in lines:
            for c in ln["containers"]:
                if c not in conts:
                    conts.append(c)

        def total(line_key, total_field):
            s = _sum(ln[line_key] for ln in lines)
            return s if not np.isnan(s) else norm_number(_first(grp, total_field))

        inv_numbers = [norm_text(v) for v in grp["invoice_no"]] if "invoice_no" in grp.columns else []
        term, place = norm_incoterms(_first(grp, "incoterms") or "")
        units = {ln["unit"] for ln in lines if ln["unit"]}
        d = {"kind": kind, "number": number, "key": norm_doc_no(number),
             "date": norm_date(_first(grp, DATE_FIELD[kind]) or ""),
             "doc_type": norm_doc_type(kind, _first(grp, "doc_type") or ""),
             "invoices": {norm_doc_no(x) for v in inv_numbers for x in _split(v) if norm_doc_no(x)},
             "invoice_numbers": sorted({x for v in inv_numbers for x in _split(v)}),
             "containers": conts, "seals": _seals(grp), "lines": lines,
             "currency": norm_currency_safe(_first(grp, "currency") or ""), "term": term, "place": place,
             "contract": norm_text(_first(grp, "contract") or ""),
             "net": total("net", "net_weight_total"), "gross": total("gross", "gross_weight_total"),
             "packages": total("packages", "packages_total"),
             "qty": _sum(ln["qty"] for ln in lines) if len(units) <= 1 else np.nan,
             "unit": next(iter(units)) if len(units) == 1 else "",
             "file": norm_text(_first(grp, "source_file") or ""), "refs": set()}
        if kind == "invoice":
            d["invoices"] = {d["key"]} if d["key"] else d["invoices"]
            d["invoice_numbers"] = [number] if number else d["invoice_numbers"]
            hdr = norm_number(_first(grp, "invoice_total"))
            d["total"] = hdr if not np.isnan(hdr) else _sum(ln["amount"] for ln in lines)
        if kind == "conformity":
            d.update({"issue": d["date"], "expiry": norm_date(_first(grp, "expiry_date") or ""),
                      "hs": {norm_hs(x) for v in (grp["hs_code"] if "hs_code" in grp.columns else [])
                             for x in _split(v) if norm_hs(x)},
                      "origin": norm_country(_first(grp, "origin_country") or ""),
                      "model": norm_text(_first(grp, "model") or ""),
                      "product": norm_text(_first(grp, "product_name") or ""),
                      "issuer": norm_text(_first(grp, "issuer") or "")})
        docs.append(d)
    return docs


# ------------------------------------------------------------------ декларации
def _ru_docs(sources: dict) -> list[dict]:
    src = sources.get("ru_dt")
    if src is None or src.df.empty:
        return []
    df = src.df.copy()
    df["_k"] = [norm_decl(v) if not is_blank(v) else f"f:{norm_text(f)}"
                for v, f in zip(df["decl_ru"] if "decl_ru" in df.columns else [None] * len(df),
                                df["source_file"] if "source_file" in df.columns else [""] * len(df))]
    docs = []
    for key, grp in df.groupby("_k", sort=False):
        conts = []
        for v in grp["container"] if "container" in grp.columns else []:
            for c in split_containers(v):
                if c not in conts:
                    conts.append(c)
        ref_cols = [c for c in ("presented_docs", "invoice_no", "bill_of_lading", "cert_no") if c in grp.columns]
        refs = _refs(v for c in ref_cols for v in grp[c] if not is_blank(v))
        term, place = norm_incoterms(_first(grp, "incoterms") or "")
        items = [{"hs": norm_hs(r.get("hs_code") or ""), "name": norm_text(r.get("product_name") or ""),
                  "model": norm_text(r.get("model") or ""), "origin": norm_country(r.get("origin_country") or "")}
                 for _, r in grp.iterrows()]
        docs.append({"kind": "ru_dt", "number": key, "key": norm_doc_no(key),
                     "date": norm_date(_first(grp, "decl_date") or "") or ru_dt_date(key),
                     "containers": conts, "refs": refs, "has_refs": bool(refs), "invoices": set(),
                     "currency": norm_currency_safe(_first(grp, "currency") or ""), "term": term, "place": place,
                     "total": _sum(norm_number(v) for v in grp["total_value"]) if "total_value" in grp.columns
                     else np.nan,
                     "net": _sum(norm_number(v) for v in grp["net_weight"]) if "net_weight" in grp.columns else np.nan,
                     "items": items, "file": norm_text(_first(grp, "source_file") or "")})
    return docs


def _cn_docs(sources: dict, cfg: dict) -> list[dict]:
    src = sources.get("cn_dec")
    if src is not None and not src.df.empty:
        df, C = src.df, Cols({}, "cn_dec", src.df)
    else:
        w = sources.get("wide_table")
        if w is None or w.df.empty:
            return []
        df, C = w.df, Cols(cfg, "wide_table", w.df)
    if not C("decl_cn"):
        return []
    docs = []
    for num, grp in df.groupby(df[C("decl_cn")].map(norm_decl), sort=False):
        if not num:
            continue
        lines = []
        item_col = C("item_no")
        for _, ig in (grp.groupby(item_col, sort=False, dropna=False) if item_col else [(None, grp)]):
            r = ig.iloc[0]
            conts = []
            for v in ig[C("container")] if C("container") else []:
                for c in split_containers(v):
                    if c not in conts:
                        conts.append(c)
            amt = norm_number(C.get(r, "total_value"))
            if np.isnan(amt):
                p, q = norm_number(C.get(r, "unit_price")), norm_number(C.get(r, "qty"))
                amt = p * q if not (np.isnan(p) or np.isnan(q)) else np.nan
            lines.append({"hs": norm_hs(C.get(r, "hs_code", "")), "name": norm_text(C.get(r, "product_name", "")),
                          "model": norm_text(C.get(r, "model", "")), "amount": amt, "containers": conts,
                          "origin": norm_country(C.get(r, "origin_country", "")),
                          "currency": norm_currency_safe(C.get(r, "currency", ""))})
        conts = []
        for ln in lines:
            for c in ln["containers"]:
                if c not in conts:
                    conts.append(c)
        bl_cols = [c for c in ("bill_of_lading",) if C(c)]
        inv_cols = [C(c) for c in ("invoice_no", "presented_docs") if C(c)]
        r0 = grp.iloc[0]
        term, place = norm_incoterms(C.get(r0, "incoterms", ""))
        docs.append({"kind": "cn_dec", "number": num, "key": norm_doc_no(num),
                     "date": norm_date(C.get(r0, "decl_date", "")), "containers": conts,
                     "refs": _refs(v for c in inv_cols for v in grp[c] if not is_blank(v)),
                     "has_refs": any(not is_blank(v) for c in inv_cols for v in grp[c]),
                     "bl_refs": _refs(v for c in bl_cols for v in grp[C(c)] if not is_blank(v)),
                     "invoices": set(), "currency": next((ln["currency"] for ln in lines if ln["currency"]), ""),
                     "term": term, "place": place, "total": _sum(ln["amount"] for ln in lines),
                     "items": lines, "file": ""})
    return docs


# ------------------------------------------------------------------ отгрузки
class _UF:
    def __init__(self):
        self.p: dict = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def _days(a: str, b: str) -> float:
    try:
        return abs((pd.Timestamp(a) - pd.Timestamp(b)).days)
    except (ValueError, TypeError):
        return np.nan


def _group_containers(g: list[dict]) -> set:
    return {c for d in g for c in d["containers"]}


def _nearest(groups: list[list[dict]], d: dict, window: float):
    if len(groups) == 1 and not d.get("date"):
        return groups[0]
    best, best_dist = None, None
    for g in groups:
        dates = [x["date"] for x in g if x.get("date")]
        dist = min((_days(d["date"], x) for x in dates), default=np.nan) if d.get("date") else np.nan
        if np.isnan(dist):
            dist = window if window else 0          # дат нет — допустимо, но хуже любой близкой даты
        if window and dist > window:
            continue
        if best is None or dist < best_dist:
            best, best_dist = g, dist
    return best


def build_shipments(docs: list[dict], rules: dict) -> list[list[dict]]:
    """Документы (кроме сертификатов) -> отгрузки (списки документов)."""
    group_by = [str(x) for x in (rules.get("group_by") or ["invoice_no", "container"])]
    use_inv, use_ctn = "invoice_no" in group_by, "container" in group_by
    window = float(rules.get("container_match_days", 120) or 0)
    known = {k for d in docs if d["kind"] in PACKAGE_KINDS for k in d["invoices"]}
    for d in docs:
        if d["kind"] in ("ru_dt", "cn_dec"):
            d["invoices"] = d["refs"] & known
    main = [d for d in docs if d["kind"] != "conformity"]
    groups: list[list[dict]] = []
    if use_inv:
        uf = _UF()
        for d in main:
            inv = sorted(d["invoices"])
            for a in inv:
                uf.union(inv[0], a)
        by_root: dict = {}
        for d in main:
            if d["invoices"]:
                by_root.setdefault(uf.find(sorted(d["invoices"])[0]), []).append(d)
        groups = list(by_root.values())
    rest = [d for d in main if not (use_inv and d["invoices"])]
    if use_ctn:
        pending = []
        for d in rest:
            cands = [g for g in groups if _group_containers(g) & set(d["containers"])]
            g = _nearest(cands, d, window) if cands else None
            if g is not None:
                g.append(d)
            else:
                pending.append(d)
        uf = _UF()
        for i, d in enumerate(pending):
            uf.find(i)
            for c in d["containers"]:
                uf.union(i, f"c:{c}")
        comps: dict = {}
        for i, d in enumerate(pending):
            comps.setdefault(uf.find(i), []).append(d)
        groups.extend(comps.values())
    else:
        groups.extend([d] for d in rest)
    return groups


def _link_certs(certs: list[dict], groups: list[list[dict]], rules: dict) -> dict[int, list[int]]:
    """-> {№ сертификата в списке: [№ отгрузок]}: по номеру инвойса, контейнеру, ссылке в ДТ / 报关单,
    при link_by_hs — по коду ТН ВЭД товаров отгрузки."""
    by_hs = bool(rules.get("link_by_hs", False))
    plen = int(rules.get("hs_prefix_len", 6))
    out: dict[int, list[int]] = {}
    for ci, c in enumerate(certs):
        hits = []
        for gi, g in enumerate(groups):
            invs = {k for d in g for k in d["invoices"]}
            refs = {k for d in g if d["kind"] in ("ru_dt", "cn_dec") for k in d["refs"]}
            if (c["invoices"] & invs) or (set(c["containers"]) & _group_containers(g)) or (c["key"] and c["key"] in refs):
                hits.append(gi)
            elif by_hs and c["hs"] and _hs_match(c["hs"], _ship_hs(g), plen):
                hits.append(gi)
        out[ci] = hits
    return out


def _ship_hs(g: list[dict]) -> set:
    hs = {it["hs"] for d in g if d["kind"] == "ru_dt" for it in d["items"] if it["hs"]}
    hs |= {ln["hs"] for d in g if d["kind"] == "invoice" for ln in d["lines"] if ln["hs"]}
    if not hs:
        hs = {it["hs"] for d in g if d["kind"] == "cn_dec" for it in d["items"] if it["hs"]}
    return hs


def _hs_match(cert_hs: set, goods_hs: set, plen: int) -> bool:
    for c in cert_hs:
        for h in goods_hs:
            if h.startswith(c) or (len(c) >= plen and len(h) >= plen and c[:plen] == h[:plen]):
                return True
    return False


# ------------------------------------------------------------------ уровни сверки
def _by(g, kind):
    return [d for d in g if d["kind"] == kind]


def _cmp_num(subs: Subs, name, a, b, la, lb, tol_abs, tol_pct, unit_ru, unit_zh, labs_zh, extra_ru="", extra_zh=""):
    if _nan(a) or _nan(b):
        return
    if nearly_equal(a, b, tol_abs, tol_pct):
        subs.add(name, OK, f"совпадает ({_fmt(a)} {unit_ru})".strip(), f"一致（{_fmt(a)} {unit_zh}）")
    else:
        subs.add(name, ISSUE, f"{la} {_fmt(a)} {unit_ru} vs {lb} {_fmt(b)} {unit_ru}{extra_ru}",
                 f"{labs_zh[0]} {_fmt(a)} {unit_zh}，{labs_zh[1]} {_fmt(b)} {unit_zh}{extra_zh}")


def _line_key_mode(a: list[dict], b: list[dict]) -> str:
    both = a + b
    if both and all(x["model"] for x in both):
        return "model"
    if both and all(x["item"] for x in both):
        return "item"
    if both and all(x["name"] for x in both):
        return "name"
    return ""


def _agg_lines(lines: list[dict], mode: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for ln in lines:
        k = canon_model(ln[mode]) if mode in ("model", "name") else ln[mode]
        rec = out.setdefault(k, {"label": ln[mode], "qty": [], "net": [], "unit": set()})
        rec["qty"].append(ln["qty"])
        rec["net"].append(ln["net"])
        if ln["unit"]:
            rec["unit"].add(ln["unit"])
    return {k: {"label": v["label"], "qty": _sum(v["qty"]), "net": _sum(v["net"]), "unit": v["unit"]}
            for k, v in out.items()}


def _inv_pack(g, rules, maxn) -> dict | None:
    inv, pk = _by(g, "invoice"), _by(g, "packing")
    if not inv or not pk:
        return None
    subs = Subs()
    il = [ln for d in inv for ln in d["lines"]]
    pl = [ln for d in pk for ln in d["lines"]]
    mode = _line_key_mode(il, pl)
    line_notes_ru, line_notes_zh = [], []
    if mode:
        a, b = _agg_lines(il, mode), _agg_lines(pl, mode)
        only_a = [a[k]["label"] for k in a if k not in b]
        only_b = [b[k]["label"] for k in b if k not in a]
        if only_a or only_b:
            ru = "; ".join(x for x in (
                f"нет в упаковочном листе: {', '.join(only_a[:maxn])}" if only_a else "",
                f"нет в инвойсе: {', '.join(only_b[:maxn])}" if only_b else "") if x)
            zh = "；".join(x for x in (
                f"装箱单中没有：{', '.join(only_a[:maxn])}" if only_a else "",
                f"发票中没有：{', '.join(only_b[:maxn])}" if only_b else "") if x)
            subs.add(N_POS, ISSUE, ru, zh)
        else:
            subs.add(N_POS, OK, f"совпадают ({len(a)})", f"一致（{len(a)}）")
        for k in a:
            if k in b:
                for f, ru_n, zh_n in (("qty", "кол-во", "数量"), ("net", "нетто", "净重")):
                    x, y = a[k][f], b[k][f]
                    if not (_nan(x) or _nan(y)) and not nearly_equal(x, y, 0.001, float(
                            rules.get("qty_tolerance_pct", 0)) if f == "qty" else float(rules.get("weight_tolerance_pct", 0.5))):
                        line_notes_ru.append(f"«{a[k]['label']}» {ru_n} {_fmt(x, 3)} vs {_fmt(y, 3)}")
                        line_notes_zh.append(f"「{a[k]['label']}」{zh_n} {_fmt(x, 3)} / {_fmt(y, 3)}")
    notes_ru = f" (позиции: {'; '.join(line_notes_ru[:maxn])})" if line_notes_ru else ""
    notes_zh = f"（品项：{'；'.join(line_notes_zh[:maxn])}）" if line_notes_zh else ""
    iq, pq = _sum(d["qty"] for d in inv), _sum(d["qty"] for d in pk)
    iu = {d["unit"] for d in inv if d["unit"]}
    pu = {d["unit"] for d in pk if d["unit"]}
    if not (iu and pu and iu != pu):
        _cmp_num(subs, N_QTY, iq, pq, "инвойс", "уп. лист", 0.001, float(rules.get("qty_tolerance_pct", 0)),
                 next(iter(iu or pu), ""), next(iter(iu or pu), ""), ("发票", "装箱单"), notes_ru, notes_zh)
    _cmp_num(subs, N_NET, _sum(d["net"] for d in inv), _sum(d["net"] for d in pk), "инвойс", "уп. лист",
             float(rules.get("weight_tolerance_kg", 1)), float(rules.get("weight_tolerance_pct", 0.5)), "кг", "千克",
             ("发票", "装箱单"), notes_ru if _nan(iq) else "", notes_zh if _nan(iq) else "")
    _cmp_num(subs, N_GROSS, _sum(d["gross"] for d in inv), _sum(d["gross"] for d in pk), "инвойс", "уп. лист",
             float(rules.get("gross_tolerance_kg", 1)), float(rules.get("gross_tolerance_pct", 0.5)), "кг", "千克",
             ("发票", "装箱单"))
    _cmp_num(subs, N_PKG, _sum(d["packages"] for d in inv), _sum(d["packages"] for d in pk), "инвойс", "уп. лист",
             float(rules.get("packages_tolerance", 0)), 0, "", "", ("发票", "装箱单"))
    return {"subs": subs, "level": "inv_pack"} if subs else None


def _inv_decl(g, decl_kind, rules) -> dict | None:
    inv, dec = _by(g, "invoice"), _by(g, decl_kind)
    if not inv or not dec:
        return None
    subs = Subs()
    extra: dict = {}
    is_ru = decl_kind == "ru_dt"
    d_ru, d_zh = ("ДТ", "俄罗斯报关单") if is_ru else ("报关单", "中国报关单")
    # 1. номер инвойса среди документов декларации
    mode = str(rules.get("reference_missing", "review"))
    if mode != "off" and any(d.get("has_refs") for d in dec):
        keys = {k for d in inv for k in d["invoices"]}
        nums = ", ".join(n for d in inv for n in d["invoice_numbers"])
        if keys & {k for d in dec for k in d["refs"]}:
            subs.add((f"№ инвойса в {d_ru}", f"{d_zh}中的发票号"), OK, "указан", "已列明")
        else:
            subs.add((f"№ инвойса в {d_ru}", f"{d_zh}中的发票号"), ISSUE if mode == "issue" else REVIEW,
                     f"инвойс {nums} не найден среди документов {d_ru}" + (" (гр. 44)" if is_ru else ""),
                     f"在{d_zh}的随附单证中未找到发票 {nums}")
    # 2. сумма и валюта
    curs = {d["currency"] for d in inv if d["currency"]}
    dcurs = {d["currency"] for d in dec if d["currency"]}
    it, dt = _sum(d["total"] for d in inv), _sum(d["total"] for d in dec)
    if not _nan(it) and not _nan(dt) and len(curs) <= 1 and len(dcurs) <= 1:
        ic, dc = next(iter(curs), ""), next(iter(dcurs), "")
        if not ic or not dc or ic == dc:
            ok = nearly_equal(it, dt, float(rules.get("amount_tolerance_abs", 0.01)),
                              float(rules.get("amount_tolerance_pct", 0)))
            cur = ic or dc
            subs.add(N_SUM, OK if ok else ISSUE,
                     f"совпадает ({_fmt(it)} {cur})" if ok else f"инвойс {_fmt(it)} {cur} vs {d_ru} {_fmt(dt)} {cur} "
                                                                f"(разница {_fmt(it - dt)})",
                     f"一致（{_fmt(it)} {cur}）" if ok else f"发票 {_fmt(it)} {cur}，{d_zh} {_fmt(dt)} {cur}"
                                                          f"（差额 {_fmt(it - dt)}）")
        else:
            on = min((d["date"] for d in dec if d.get("date")), default="")
            val, rate = fx_convert(it, ic, dc, on, side="ru" if is_ru else "cn")
            if not _nan(val):
                extra["fx_rate"] = rate
            if _nan(val):
                subs.add(N_SUM, REVIEW, f"валюты разные ({ic} / {dc}): {rate}", f"币种不同（{ic} / {dc}）：{rate}")
            elif nearly_equal(val, dt, 0.01, float(rules.get("fx_tolerance_pct", 0.5))):
                subs.add(N_SUM, REVIEW, f"валюты разные: инвойс {_fmt(it)} {ic} = {_fmt(val)} {dc} ≈ {d_ru} "
                                        f"{_fmt(dt)} {dc} ({rate})",
                         f"币种不同：发票 {_fmt(it)} {ic} 折合 {_fmt(val)} {dc}，与{d_zh} {_fmt(dt)} {dc} 一致（{rate}）")
            else:
                subs.add(N_SUM, ISSUE, f"инвойс {_fmt(it)} {ic} = {_fmt(val)} {dc} vs {d_ru} {_fmt(dt)} {dc} ({rate})",
                         f"发票 {_fmt(it)} {ic} 折合 {_fmt(val)} {dc}，{d_zh} {_fmt(dt)} {dc}（{rate}）")
    elif not _nan(it) and not _nan(dt):
        subs.add(N_SUM, REVIEW, "в документах несколько валют — сумма не сверялась", "单据中有多种币种，未核对金额")
    # 3. условия поставки
    it_terms = {d["term"] for d in inv if d["term"]}
    dt_terms = {d["term"] for d in dec if d["term"]}
    if it_terms and dt_terms:
        place_ok = True
        if bool(rules.get("compare_place", False)):
            ip = {norm_text(d["place"]).upper() for d in inv if d["place"]}
            dp = {norm_text(d["place"]).upper() for d in dec if d["place"]}
            place_ok = not ip or not dp or bool(ip & dp)
        if it_terms & dt_terms and place_ok:
            subs.add(N_INC, OK, f"совпадают ({', '.join(sorted(it_terms))})", f"一致（{', '.join(sorted(it_terms))}）")
        else:
            st = ISSUE if str(rules.get("incoterms_mismatch", "issue")) == "issue" else REVIEW
            subs.add(N_INC, st, f"инвойс {', '.join(sorted(it_terms))} vs {d_ru} {', '.join(sorted(dt_terms))}"
                     + ("" if place_ok else " (место поставки)"),
                     f"发票 {', '.join(sorted(it_terms))}，{d_zh} {', '.join(sorted(dt_terms))}")
    return {"subs": subs, "level": "inv_ru" if is_ru else "inv_cn", "extra": extra} if subs else None


def _pack_tr(g, rules, maxn) -> dict | None:
    pk, tr = _by(g, "packing"), _by(g, "transport")
    if not pk or not tr:
        return None
    subs = Subs()
    _cmp_num(subs, N_PKG, _sum(d["packages"] for d in pk), _sum(d["packages"] for d in tr), "уп. лист",
             "трансп. документ", float(rules.get("packages_tolerance", 0)), 0, "", "", ("装箱单", "运输单据"))
    _cmp_num(subs, N_GROSS, _sum(d["gross"] for d in pk), _sum(d["gross"] for d in tr), "уп. лист",
             "трансп. документ", float(rules.get("gross_tolerance_kg", 1)), float(rules.get("gross_tolerance_pct", 0.5)),
             "кг", "千克", ("装箱单", "运输单据"))
    pc = {c for d in pk for c in d["containers"]}
    tc = {c for d in tr for c in d["containers"]}
    if pc and tc:
        if pc == tc:
            subs.add(N_CTN, OK, f"совпадают ({len(pc)})", f"一致（{len(pc)}）")
        else:
            a, b = sorted(pc - tc), sorted(tc - pc)
            subs.add(N_CTN, ISSUE, "; ".join(x for x in (
                f"нет в трансп. документе: {', '.join(a[:maxn])}" if a else "",
                f"нет в уп. листе: {', '.join(b[:maxn])}" if b else "") if x),
                "；".join(x for x in (f"运输单据中没有：{', '.join(a[:maxn])}" if a else "",
                                     f"装箱单中没有：{', '.join(b[:maxn])}" if b else "") if x))
    ps: dict[str, set] = {}
    ts: dict[str, set] = {}
    for d in pk:
        for c, s in d["seals"].items():
            ps.setdefault(c, set()).update(s)
    for d in tr:
        for c, s in d["seals"].items():
            ts.setdefault(c, set()).update(s)
    common = [c for c in ps if c in ts and c]
    if common:
        bad = [c for c in common if ps[c] != ts[c]]
        if bad:
            subs.add(N_SEAL, ISSUE, "; ".join(f"{c}: уп. лист {', '.join(sorted(ps[c]))} vs трансп. документ "
                                              f"{', '.join(sorted(ts[c]))}" for c in bad[:maxn]),
                     "；".join(f"{c}：装箱单 {', '.join(sorted(ps[c]))}，运输单据 {', '.join(sorted(ts[c]))}"
                              for c in bad[:maxn]))
        else:
            subs.add(N_SEAL, OK, f"совпадают ({len(common)} конт.)", f"一致（{len(common)} 个集装箱）")
    return {"subs": subs, "level": "pack_tr"} if subs else None


def _tr_decl(g, rules) -> dict | None:
    tr = _by(g, "transport")
    ru, cn = _by(g, "ru_dt"), _by(g, "cn_dec")
    mode = str(rules.get("reference_missing", "review"))
    if not tr or mode == "off" or not (ru or cn):
        return None
    subs = Subs()
    keys = {d["key"] for d in tr if d["key"]}
    nums = ", ".join(f"{doc_type_label(d['doc_type'])} {d['number']}".strip() for d in tr)
    nums_zh = ", ".join(f"{doc_type_label(d['doc_type'], 'zh')} {d['number']}".strip() for d in tr)
    bad = ISSUE if mode == "issue" else REVIEW
    if keys and any(d.get("has_refs") for d in ru):
        if keys & {k for d in ru for k in d["refs"]}:
            subs.add(("№ накладной в ДТ", "俄罗斯报关单中的运单号"), OK, "указан", "已列明")
        else:
            subs.add(("№ накладной в ДТ", "俄罗斯报关单中的运单号"), bad, f"{nums} не найден среди документов ДТ (гр. 44)",
                     f"在俄罗斯报关单随附单证中未找到 {nums_zh}")
    bl = {k for d in cn for k in d.get("bl_refs", set())}
    if keys and bl:
        if keys & bl:
            subs.add(("№ накладной в 报关单", "中国报关单提运单号"), OK, "совпадает с 提运单号", "与提运单号一致")
        else:
            subs.add(("№ накладной в 报关单", "中国报关单提运单号"), bad, f"{nums} ≠ 提运单号 报关单 "
                     f"{', '.join(sorted({n for d in cn for n in d.get('bl_refs', set())}))}",
                     f"{nums_zh} 与报关单提运单号不一致")
    return {"subs": subs, "level": "tr_decl"} if subs else None


def _cert(c, g, rules) -> Subs:
    subs = Subs()
    ru, cn, inv = _by(g, "ru_dt"), _by(g, "cn_dec"), _by(g, "invoice")
    ref, ref_ru, ref_zh = "", "", ""
    for docs, lr, lz in ((ru, "дата ДТ", "俄罗斯报关单日期"), (cn, "дата 报关单", "中国报关单日期"),
                         (inv, "дата инвойса", "发票日期")):
        dates = sorted(d["date"] for d in docs if d.get("date"))
        if dates:
            ref, ref_ru, ref_zh = dates[0], lr, lz
            break
    # срок действия
    if not c["issue"] and not c["expiry"]:
        if str(rules.get("missing_dates", "review")) != "ok":
            subs.add(N_VALID, REVIEW, "в сертификате нет дат выдачи и окончания действия", "证书没有签发日期和有效期")
    elif not ref:
        subs.add(N_VALID, REVIEW, "нет даты ДТ / 报关单 / инвойса для сверки срока", "没有可用于核对有效期的单据日期")
    elif c["expiry"] and c["expiry"] < ref:
        subs.add(N_VALID, ISSUE, f"срок действия истёк {_dmy(c['expiry'])}, {ref_ru} {_dmy(ref)}",
                 f"有效期已于 {_dmy(c['expiry'])} 届满，{ref_zh} {_dmy(ref)}")
    elif c["issue"] and c["issue"] > ref:
        mode = str(rules.get("issued_after_decl", "review"))
        st = {"issue": ISSUE, "ok": OK}.get(mode, REVIEW)
        subs.add(N_VALID, st, f"выдан {_dmy(c['issue'])} — позже, чем {ref_ru} {_dmy(ref)}",
                 f"签发日期 {_dmy(c['issue'])} 晚于{ref_zh} {_dmy(ref)}")
    else:
        till = f" до {_dmy(c['expiry'])}" if c["expiry"] else ""
        subs.add(N_VALID, OK, f"действует на {ref_ru} {_dmy(ref)}{till}",
                 f"在{ref_zh} {_dmy(ref)} 有效" + (f"（至 {_dmy(c['expiry'])}）" if c["expiry"] else ""))
    # код ТН ВЭД
    goods = _ship_hs(g)
    plen = int(rules.get("hs_prefix_len", 6))
    if c["hs"] and goods:
        if _hs_match(c["hs"], goods, plen):
            subs.add(N_HS, OK, f"код {', '.join(sorted(c['hs']))} подходит к товару", "商品编码相符")
        else:
            st = ISSUE if str(rules.get("hs_mismatch", "issue")) == "issue" else REVIEW
            subs.add(N_HS, st, f"код сертификата {', '.join(sorted(c['hs']))} не подходит к товару "
                               f"{', '.join(sorted(goods))}",
                     f"证书商品编码 {', '.join(sorted(c['hs']))} 与货物 {', '.join(sorted(goods))} 不符")
    # товар (марка)
    if c["model"]:
        names = [it.get("model") or "" for d in ru + cn for it in d["items"]] + \
                [it.get("name") or "" for d in ru + cn for it in d["items"]] + \
                [ln["model"] or ln["name"] for d in inv for ln in d["lines"]]
        cm = canon_model(c["model"])
        if any(cm and cm in canon_model(n) for n in names if n):
            subs.add(N_GOODS, OK, f"марка {c['model']} есть в документах отгрузки", f"牌号 {c['model']} 相符")
        elif names:
            subs.add(N_GOODS, REVIEW, f"марка {c['model']} не найдена в ДТ / 报关单 / инвойсе",
                     f"在报关单 / 发票中未找到牌号 {c['model']}")
    # страна происхождения
    origin_types = {str(x).upper() for x in (rules.get("origin_doc_types") or ["CO", "ST-1", "FORM-A", "FORM-E"])}
    if c["doc_type"] in origin_types and c["origin"]:
        ru_o = {it["origin"] for d in ru for it in d["items"] if it["origin"]}
        cn_o = {it["origin"] for d in cn for it in d["items"] if it["origin"]}
        if ru_o or cn_o:
            bad_ru = ru_o and c["origin"] not in ru_o
            bad_cn = cn_o and c["origin"] not in cn_o
            if bad_ru or bad_cn:
                st = ISSUE if str(rules.get("origin_mismatch", "issue")) == "issue" else REVIEW
                parts_ru = [f"ДТ {', '.join(sorted(ru_o))}" if ru_o else "", f"报关单 {', '.join(sorted(cn_o))}" if cn_o else ""]
                parts_zh = [f"俄罗斯报关单 {', '.join(sorted(ru_o))}" if ru_o else "",
                            f"中国报关单 {', '.join(sorted(cn_o))}" if cn_o else ""]
                subs.add(N_ORIGIN, st, f"в сертификате {c['origin']}, в " + ", ".join(p for p in parts_ru if p),
                         f"证书 {c['origin']}，" + "，".join(p for p in parts_zh if p))
            else:
                subs.add(N_ORIGIN, OK, f"{c['origin']} совпадает с декларациями", f"{c['origin']} 与报关单一致")
    return subs


# ------------------------------------------------------------------ проверка
def _label(g: list[dict]) -> str:
    invs = [n for d in g if d["kind"] == "invoice" for n in d["invoice_numbers"]]
    if not invs:
        invs = sorted({n for d in g for n in d.get("invoice_numbers", [])})
    if invs:
        return ", ".join(dict.fromkeys(invs))
    conts = sorted(_group_containers(g))
    return ("конт. " + ", ".join(conts[:2]) + (" …" if len(conts) > 2 else "")) if conts else "без номера"


def _ids(g: list[dict], certs: list[dict]) -> dict:
    def nums(kind):
        return ", ".join(dict.fromkeys(d["number"] for d in g if d["kind"] == kind and d["number"]))
    def tr_label(d):
        code = d["doc_type"] or ""
        return d["number"] if not code or norm_doc_no(d["number"]).startswith(norm_doc_no(code)) \
            else f"{code} {d['number']}"
    tr = ", ".join(dict.fromkeys(tr_label(d) for d in g if d["kind"] == "transport"))
    return {"shipment": _label(g),
            "invoice_no": nums("invoice") or ", ".join(sorted({n for d in g for n in d.get("invoice_numbers", [])})),
            "packing_no": nums("packing"), "transport_doc": tr, "decl_ru": nums("ru_dt"), "decl_cn": nums("cn_dec"),
            "cert_no": ", ".join(dict.fromkeys(c["number"] for c in certs if c["number"])),
            "containers": "; ".join(sorted(_group_containers(g)))}


def run(sources: dict, rules: dict, cfg: dict) -> CheckOutput:
    pkg = {k: _package_docs(sources.get(k), k) for k in PACKAGE_KINDS}
    if not any(pkg.values()):
        return skipped("docs_package", TITLE, "Нужен хотя бы один документ пакета: инвойс, упаковочный лист, "
                                              "транспортный документ или сертификат.")
    ru, cn = _ru_docs(sources), _cn_docs(sources, cfg)
    docs = pkg["invoice"] + pkg["packing"] + pkg["transport"] + ru + cn
    certs = pkg["conformity"]
    groups = build_shipments(docs, rules)
    links = _link_certs(certs, groups, rules)
    certs_of = {gi: [certs[ci] for ci, gs in links.items() if gi in gs] for gi in range(len(groups))}
    maxn = int(rules.get("max_listed", 5))
    required = [str(k) for k in (rules.get("required_docs") or ["invoice", "packing", "transport", "ru_dt"])]
    expected = [str(k) for k in (rules.get("expected_docs") or []) if str(k) not in required]
    not_loaded_mode = str(rules.get("not_loaded", "review"))
    loaded = {k: bool(v) for k, v in pkg.items()}
    loaded.update(ru_dt=bool(ru), cn_dec=bool(cn))

    rows, shipments = [], []
    for gi, g in enumerate(groups):
        gc = certs_of[gi]
        base = {"check": TITLE, **_ids(g, gc)}
        have = {k: bool(_by(g, k)) for k in ORDER if k != "conformity"}
        have["conformity"] = bool(gc)
        # 1. комплект
        subs = Subs()
        for k in ORDER:
            lr, lz = KIND_LABELS[k]
            if have[k]:
                subs.add((lr, lz), OK, "есть", "有")
            elif k in required or k in expected:
                if not loaded[k]:
                    st = ISSUE if not_loaded_mode == "issue" and k in required else REVIEW
                    subs.add((lr, lz), st, "не загружены — комплект не проверен", "未上传，未核对")
                else:
                    st = ISSUE if k in required else REVIEW
                    subs.add((lr, lz), st, "нет в комплекте" + ("" if k in required else " (ожидается)"),
                             "缺少" + ("" if k in required else "（应有）"))
        rec = subs.fill({**base, "level": LEVELS["set"][0],
                         "docs": " · ".join(f"{KIND_LABELS[k][0]} {'✓' if have[k] else '✗'}" for k in ORDER)}, "set",
                        "комплект полный", "单证齐全")
        rows.append(rec)
        shipments.append({"shipment": base["shipment"], "status": rec["status"],
                          "docs": {k: [d["number"] or d["file"] for d in g if d["kind"] == k] for k in ORDER
                                   if k != "conformity"} | {"conformity": [c["number"] for c in gc]}})
        # 2–5. сверки пар документов
        for res in (_inv_pack(g, rules, maxn), _inv_decl(g, "ru_dt", rules), _inv_decl(g, "cn_dec", rules),
                    _pack_tr(g, rules, maxn), _tr_decl(g, rules)):
            if res:
                rows.append(res["subs"].fill({**base, "level": LEVELS[res["level"]][0], **res.get("extra", {})},
                                             res["level"]))
        # 6. сертификаты
        for c in gc:
            s = _cert(c, g, rules)
            if s:
                rows.append(s.fill({**base, "level": f"{LEVELS['cert'][0]}: {doc_type_label(c['doc_type'])}",
                                    "cert_no": c["number"]}, "cert"))
    for ci, c in enumerate(certs):
        if not links.get(ci):
            s = Subs()
            s.add(("связь с отгрузкой", "与货物的关联"), REVIEW,
                  "не связан ни с одной отгрузкой: нет ссылки на инвойс, контейнер или номер в ДТ / 报关单",
                  "未关联任何货物：无发票号、集装箱号或报关单中的证书号")
            rows.append(s.fill({"check": TITLE, "shipment": "—", "cert_no": c["number"],
                                "level": f"{LEVELS['cert'][0]}: {doc_type_label(c['doc_type'])}"}, "cert"))

    cols = ["check", "shipment", "level", "status", "detail", "detail_zh", "checks", "docs", "invoice_no", "packing_no",
            "transport_doc", "decl_ru", "decl_cn", "cert_no", "containers", "fx_rate"]
    df = sort_df(pd.DataFrame(rows))
    df = df[[c for c in cols if c in df.columns]]
    full = sum(1 for s in shipments if s["status"] == OK)
    meta = {"shipments": shipments, "documents": {k: len(v) for k, v in pkg.items()} | {"ru_dt": len(ru),
                                                                                         "cn_dec": len(cn)},
            "summary": f"Отгрузок: {len(groups)}, полный комплект: {full}; документов: "
                       + ", ".join(f"{KIND_LABELS[k][0]} {n}" for k, n in
                                   ({k: len(v) for k, v in pkg.items()} | {"ru_dt": len(ru), "cn_dec": len(cn)}).items()
                                   if n)}
    return CheckOutput("docs_package", TITLE, df, meta=meta)


register("docs_package", TITLE, run)
