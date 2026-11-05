from __future__ import annotations
# -*- coding: utf-8 -*-
"""Проверка 8 (Коннекторы/банк): оплаты по выпискам банка ↔ контракты.

Строки двух уровней (колонка level):
  * «платёж» — одна операция выписки: к какому контракту относится (номер контракта в
    назначении платежа — кириллица и латиница сравниваются одинаково; номер инвойса; УНК;
    только основной контракт; по контрагенту), совпадает ли плательщик/получатель со
    стороной контракта (нечёткое сравнение названий, транслит), валюта (пересчёт по курсу
    на дату платежа — app.rates), сумма инвойса, если он найден;
  * «контракт» — сумма оплат по контракту против ожидаемой суммы (инвойсы, реестр договоров,
    ДТ РФ, 报关单, SAP — rules.expected_amount_from): полностью / частично / переплата / оплат нет.

Пояснения — по-русски (detail) и по-китайски (detail_zh). Статусы: OK / REVIEW / ISSUE.
"""
import functools
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

import numpy as np
import pandas as pd

from app.checks import _common, register
from app.checks._common import Cols, norm_currency_safe, skipped, sort_df, worst
from app.models import CheckOutput, Status
from app.normalizers import (canon_contract, is_blank, latinize, nearly_equal, nfkc, norm_date, norm_decl,
                             norm_number, norm_text, split_contracts)

TITLE = "8. Оплаты"
OK, ISSUE, REVIEW, SKIP = Status.OK.value, Status.ISSUE.value, Status.REVIEW.value, Status.SKIPPED.value
LVL_PAY, LVL_CONTRACT = "платёж", "контракт"

# ------------------------------------------------------------------ транслитерация
_PHON = {"А": "A", "Б": "B", "В": "V", "Г": "G", "Д": "D", "Е": "E", "Ё": "E", "Ж": "ZH", "З": "Z", "И": "I",
         "Й": "Y", "К": "K", "Л": "L", "М": "M", "Н": "N", "О": "O", "П": "P", "Р": "R", "С": "S", "Т": "T",
         "У": "U", "Ф": "F", "Х": "KH", "Ц": "TS", "Ч": "CH", "Ш": "SH", "Щ": "SHCH", "Ъ": "", "Ы": "Y",
         "Ь": "", "Э": "E", "Ю": "YU", "Я": "YA"}
_REST = {k: v for k, v in _PHON.items() if k in "БГДЖЗИЙЛПФЦЧШЩЪЫЬЭЮЯ"}


def translit(v) -> str:
    """Кириллица -> латиница по звучанию (Пример -> PRIMER)."""
    return "".join(_PHON.get(ch, ch) for ch in nfkc(v).upper())


def _latin_forms(v) -> list[str]:
    """Варианты записи номера: «двойники» латиницей (СХ -> CX) + остальная кириллица транслитом
    (ДЕМО -> DEMO) и полный транслит."""
    lat = latinize(v)
    rest = "".join(_REST.get(ch, ch) for ch in lat)
    return list(dict.fromkeys([lat, rest, translit(v)]))


_SEP = r"[\s\-–—_]*"
_EDGE = "0-9A-ZА-ЯЁ"


@functools.lru_cache(maxsize=20000)
def _rx(canon: str):
    body = _SEP.join(re.escape(ch) for ch in canon)
    return re.compile(rf"(?<![{_EDGE}]){body}(?![{_EDGE}])")


def _canon_forms(v) -> list[str]:
    return list(dict.fromkeys(canon_contract(f) for f in _latin_forms(v) if canon_contract(f)))


# ------------------------------------------------------------------ названия контрагентов
@functools.lru_cache(maxsize=20000)
def name_key(v, legal_forms: tuple) -> str:
    s = translit(v)
    s = re.sub(r"[(（\[][^)）\]]*[)）\]]", " ", s)           # «(вымышленная)», «(Китай)»
    for lf in sorted(legal_forms, key=len, reverse=True):
        lf = translit(lf)
        if re.search(r"[一-鿿]", lf):
            s = s.replace(lf, " ")
        else:
            s = re.sub(rf"(?<![A-Z0-9]){re.escape(lf)}(?![A-Z0-9])", " ", s)
    s = re.sub(r"[^\w一-鿿]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


@functools.lru_cache(maxsize=50000)
def name_score(a, b, legal_forms: tuple) -> float:
    ka, kb = name_key(a, legal_forms), name_key(b, legal_forms)
    if not ka or not kb:
        return 0.0
    if ka == kb:
        return 1.0
    ratio = SequenceMatcher(None, ka, kb).ratio()
    ta, tb = set(ka.split()), set(kb.split())
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    if short and short <= long_ and max(len(t) for t in short) >= 3:
        ratio = max(ratio, 0.9)
    return ratio


# ------------------------------------------------------------------ контракты
@dataclass
class Contract:
    number: str
    key: str
    forms: list = field(default_factory=list)
    main_forms: list = field(default_factory=list)
    parties: set = field(default_factory=set)
    inns: set = field(default_factory=set)
    unk: set = field(default_factory=set)
    expected: dict = field(default_factory=dict)       # источник -> {валюта: сумма}
    sources: set = field(default_factory=set)
    glued: bool = False


def _val(row, df, col):
    if col is None or col not in df.columns:
        return None
    v = row.get(col)
    return None if is_blank(v) else v


class Universe:
    def __init__(self, min_len: int):
        self.by_key: dict[str, Contract] = {}
        self.min_len = min_len

    def add(self, number, source: str, parties=(), inn=None, unk=None, amount=None, currency="", split=True):
        """split=False — номер из реестра договоров / инвойса: не делить «склейку» («ИМП-ДЕМО.01» — один номер)."""
        number = norm_text(number)
        if not number:
            return
        parts = (split_contracts(number) or [number]) if split else [number]
        for part in parts:
            key = canon_contract(part)
            if not key:
                continue
            c = self.by_key.get(key)
            if c is None:
                c = Contract(number=part, key=key)
                c.forms = [f for f in _canon_forms(part) if len(f) >= self.min_len]
                main = part.split("-")[0] if "-" in part else ""
                c.main_forms = [f for f in _canon_forms(main) if len(f) >= self.min_len] if main else []
                self.by_key[key] = c
            c.sources.add(source)
            c.parties |= {norm_text(p) for p in parties if not is_blank(p)}
            if not is_blank(inn):
                c.inns.add(re.sub(r"\D", "", str(inn)))
            if not is_blank(unk):
                c.unk.add(re.sub(r"\s", "", str(unk)))
            if len(parts) > 1:
                c.glued = True
                continue                      # сумма «склеенной» строки на части не делится
            a = norm_number(amount)
            if not np.isnan(a) and a != 0:
                cur = norm_currency_safe(currency) or "?"
                bucket = c.expected.setdefault(source, {})
                bucket[cur] = bucket.get(cur, 0.0) + a

    def find(self, text_forms: list[str]) -> tuple[list[Contract], list[Contract]]:
        """-> (контракты, найденные целиком; контракты, у которых найден только основной номер)."""
        full = [c for c in self.by_key.values()
                if any(_rx(f).search(t) for f in c.forms for t in text_forms)]
        keys = {c.key for c in full}
        full = [c for c in full if not any(c.key != o.key and c.key in o.key for o in full)]   # «1001-01» внутри «1001-01А»
        main = [] if full else [c for c in self.by_key.values() if c.key not in keys and
                                any(_rx(f).search(t) for f in c.main_forms for t in text_forms)]
        return full, main


def build_universe(sources: dict, cfg: dict, rules: dict) -> Universe:
    u = Universe(int(rules.get("min_contract_len", 4)))
    # реестр договоров (1С EnterpriseData, Excel)
    reg = sources.get("contract_register")
    if reg is not None and not reg.df.empty:
        C = Cols(cfg, "contract_register", reg.df)
        for _, r in reg.df.iterrows():
            u.add(C.get(r, "contract"), "contract_register", [C.get(r, "counterparty")],
                  inn=C.get(r, "counterparty_inn"), unk=C.get(r, "unk"), amount=C.get(r, "contract_amount"),
                  currency=C.get(r, "currency", ""), split=False)
    # ДТ РФ: стоимость товаров по инвойсу (графа 42), стороны — отправитель и получатель
    for slot in ("ru_dt", "cn_dec"):
        src = sources.get(slot)
        if src is None or src.df.empty:
            continue
        df = src.df
        key_col = "decl_ru" if slot == "ru_dt" else "decl_cn"
        seen = set()
        for _, r in df.iterrows():
            k = (norm_decl(_val(r, df, key_col) or ""), norm_text(_val(r, df, "item_no") or ""), _val(r, df, "source_file"))
            amount = _val(r, df, "total_value")
            if k in seen:
                amount = None
            seen.add(k)
            u.add(_val(r, df, "contract"), slot, [_val(r, df, "consignor"), _val(r, df, "consignee")],
                  amount=amount, currency=_val(r, df, "currency") or "")
    # SAP / Purchase (и 1С «Поступление товаров»): цена за тонну × вес или сумма строки
    p = sources.get("purchase")
    if p is not None and not p.df.empty:
        C = Cols(cfg, "purchase", p.df)
        sum_col = next((c for c in ("Сумма", "amount", "Amount") if c in p.df.columns), None)
        cp_col = next((c for c in ("Контрагент", "counterparty", "Vendor", "Supplier") if c in p.df.columns), None)
        for _, r in p.df.iterrows():
            main, add = norm_text(C.get(r, "main_contract", "")), norm_text(C.get(r, "add_contract", ""))
            number = "-".join(x for x in (main, add) if x)
            amount = norm_number(r.get(sum_col)) if sum_col else np.nan
            if np.isnan(amount):
                amount = norm_number(C.get(r, "sap_price")) * norm_number(C.get(r, "net_weight"))
            u.add(number, "purchase", [r.get(cp_col)] if cp_col else [], amount=amount,
                  currency=C.get(r, "currency", ""))
    return u


# ------------------------------------------------------------------ инвойсы
def invoice_refs(text: str, rules: dict) -> list[str]:
    rx = rules.get("invoice_regex")
    found = []
    try:
        found += re.findall(str(rx), text) if rx else []
    except re.error:
        pass
    found += re.findall(r"(?<![A-Za-z0-9])(INV[-/_]?[A-Z0-9][A-Z0-9\-/_.]*)", text.upper())
    out = []
    for f in found:
        f = f.strip(".,;:/-")
        if not re.search(r"\d", f) or len(canon_contract(f)) < 3:
            continue
        out.append(f)
    canon = {f: canon_contract(f) for f in out}
    out = [f for f in dict.fromkeys(out) if not any(canon[f] != canon[o] and canon[f] in canon[o] for o in out)]
    return out


def build_invoices(sources: dict, rules: dict) -> dict[str, dict]:
    """{канон. номер инвойса: {number, contract, amount, currency}} из слота инвойсов (если он есть)."""
    src = sources.get(str(rules.get("invoice_slot", "invoice")))
    if src is None or src.df.empty:
        return {}
    df = src.df

    def pick(names):
        return next((c for c in names if c in df.columns), None)
    ncol = pick(rules.get("invoice_number_columns") or [])
    if ncol is None:
        return {}
    ccol, acol = pick(rules.get("invoice_contract_columns") or []), pick(rules.get("invoice_amount_columns") or [])
    kcol = pick(rules.get("invoice_currency_columns") or [])
    out: dict[str, dict] = {}
    for _, r in df.iterrows():
        num = norm_text(r.get(ncol))
        key = canon_contract(num)
        if not key:
            continue
        rec = out.setdefault(key, {"number": num, "contract": "", "amount": 0.0, "currency": "", "forms": _canon_forms(num)})
        if ccol and not is_blank(r.get(ccol)):
            rec["contract"] = rec["contract"] or norm_text(r.get(ccol))
        a = norm_number(r.get(acol)) if acol else np.nan
        if not np.isnan(a):
            rec["amount"] += a
        if kcol and not is_blank(r.get(kcol)):
            rec["currency"] = rec["currency"] or norm_currency_safe(r.get(kcol))
    return out


# ------------------------------------------------------------------ помощники
def _fmt(v, nd=2) -> str:
    return "—" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v:,.{nd}f}".replace(",", " ")


def _n(v):
    return None if v is None or (isinstance(v, float) and np.isnan(v)) else round(float(v), 2)


def _conv(amount, frm, to, date, side):
    """-> (сумма в валюте to, описание курса); при той же валюте курс не нужен."""
    if not frm or not to or frm == to or to == "?":
        return amount, ""
    return _common.fx_convert(amount, frm, to, date, side=side)


def _expected(c: Contract, order: list[str]) -> tuple[float, str, str, list[str]]:
    """-> (сумма, валюта, источник, заметки)."""
    for src in order:
        b = c.expected.get(src)
        if not b:
            continue
        notes = []
        if len(b) > 1:
            notes.append("в документах несколько валют: " + ", ".join(f"{_fmt(v)} {k}" for k, v in b.items()))
        cur, amt = max(b.items(), key=lambda kv: kv[1])
        return amt, ("" if cur == "?" else cur), src, notes
    return np.nan, "", "", []


SRC_TITLES = {"invoice": ("инвойсы", "发票"), "contract_register": ("реестр договоров", "合同登记"),
              "ru_dt": ("ДТ РФ", "俄罗斯报关单"), "cn_dec": ("报关单", "中国报关单"), "purchase": ("SAP / 1С", "SAP / 1C")}


# ------------------------------------------------------------------ проверка
def run(sources: dict, rules: dict, cfg: dict) -> CheckOutput:
    bank = sources.get("bank")
    if bank is None or bank.df.empty or "amount" not in bank.df.columns:
        return skipped("payments", TITLE, "Нужны выписки банка (слот «Банк: выписки»).")
    directions = [str(d).lower() for d in (rules.get("directions") or ["in", "out"])]
    side = str(rules.get("fx_side", "ru"))
    tol_pct, tol_abs = float(rules.get("amount_tolerance_pct", 0.5)), float(rules.get("amount_tolerance_abs", 50))
    min_ratio = float(rules.get("counterparty_min_ratio", 0.8))
    rev_ratio = float(rules.get("counterparty_review_ratio", 0.6))
    mismatch = str(rules.get("counterparty_mismatch", "review"))
    no_contract = rules.get("payment_without_contract") or {"in": "issue", "out": "review"}
    if not isinstance(no_contract, dict):
        no_contract = {"in": str(no_contract), "out": str(no_contract)}
    legal = tuple(str(x) for x in (rules.get("legal_forms") or []))
    aliases = {norm_text(k): [norm_text(x) for x in (v or [])] for k, v in (rules.get("counterparty_aliases") or {}).items()}
    own = [norm_text(x) for x in (rules.get("own_names") or [])]
    order = [str(x) for x in (rules.get("expected_amount_from") or ["invoice", "contract_register", "ru_dt", "cn_dec",
                                                                     "purchase"])]

    universe = build_universe(sources, cfg, rules)
    invoices = build_invoices(sources, rules)
    for inv in invoices.values():
        if inv["contract"]:
            universe.add(inv["contract"], "invoice", amount=inv["amount"], currency=inv["currency"], split=False)

    def score(name: str, parties: set) -> tuple[float, str]:
        best, who = 0.0, ""
        for p in parties:
            if own and any(name_score(p, o, legal) >= 0.95 for o in own):
                continue
            cands = [p] + aliases.get(p, []) + [k for k, vs in aliases.items() if p in vs]
            for cand in cands:
                s = name_score(name, cand, legal)
                if s > best:
                    best, who = s, p
        return best, who

    rows, paid = [], {}           # paid: ключ контракта -> [(сумма в валюте контракта | NaN, валюта, направление, курс, дата)]
    dup_seen = {}
    df = bank.df
    skipped_n = 0
    for _, r in df.iterrows():
        g = lambda c: r.get(c) if c in df.columns and not is_blank(r.get(c)) else None  # noqa: E731
        direction = str(g("pay_direction") or "").lower()
        status_txt = str(g("entry_status") or "BOOK").upper()
        if direction and direction not in directions:
            continue
        if bool(rules.get("only_booked", True)) and status_txt and not any(s in status_txt for s in ("BOOK", "RVSL")):
            skipped_n += 1
            continue
        amount = norm_number(g("amount"))
        cur = norm_currency_safe(g("currency") or "")
        date = norm_date(g("pay_date") or "") or norm_date(g("value_date") or "")
        cp = norm_text(g("counterparty") or "")
        purpose = norm_text(g("purpose") or "")
        inn = re.sub(r"\D", "", str(g("counterparty_inn") or ""))
        subs: list[tuple[str, str, str, str]] = []          # (что, статус, ru, zh)
        text_forms = _latin_forms(purpose)
        refs = invoice_refs(purpose, rules)
        rec = {"check": TITLE, "level": LVL_PAY, "pay_date": date, "pay_direction": direction,
               "amount": _n(amount), "currency": cur, "counterparty": cp, "purpose": purpose,
               "invoice_ref": ", ".join(refs), "reference": norm_text(g("reference") or ""),
               "account": norm_text(g("account") or ""), "bank_format": norm_text(g("bank_format") or ""),
               "source_file": norm_text(g("source_file") or "")}
        vo = re.search(r"(?<![A-Za-z])VO\s*(\d{5})(?!\d)", purpose)        # код вида валютной операции {VO10100}
        if vo:
            rec["vo_code"] = vo.group(1)

        # дубль (одна выписка загружена дважды или пришла в двух форматах)
        dkey = (date, _n(amount), cur, direction, canon_contract(cp)[:40], rec["reference"])
        if dkey in dup_seen:
            subs.append(("дубль", REVIEW, f"такой же платёж уже есть в выписке ({dup_seen[dkey]})",
                         f"流水中已有相同付款（{dup_seen[dkey]}）"))
        dup_seen.setdefault(dkey, rec["source_file"] or rec["bank_format"] or "та же выписка")

        # 1. контракт
        contract, how = None, ""
        full, main = universe.find(text_forms)
        inv_hit = [inv for inv in invoices.values() if any(_rx(f).search(t) for f in inv["forms"] for t in text_forms)]
        unk_hit = [c for c in universe.by_key.values() if any(u and u in re.sub(r"\s", "", purpose) for u in c.unk)]
        if len(full) == 1:
            contract, how = full[0], "номер контракта в назначении"
        elif len(full) > 1:
            subs.append(("контракт", REVIEW, "в назначении несколько контрактов: " + ", ".join(c.number for c in full)
                         + " — сумма не распределена", "付款用途中有多个合同：" + ", ".join(c.number for c in full)))
        elif unk_hit and len(unk_hit) == 1:
            contract, how = unk_hit[0], "УНК в назначении"
        elif inv_hit and inv_hit[0]["contract"]:
            contract = universe.by_key.get(canon_contract(inv_hit[0]["contract"])) or next(
                (universe.by_key[canon_contract(p)] for p in split_contracts(inv_hit[0]["contract"])
                 if canon_contract(p) in universe.by_key), None)
            how = f"инвойс {inv_hit[0]['number']}" if contract else ""
        elif len(main) == 1:
            contract, how = main[0], "только основной номер контракта"
            subs.append(("контракт", REVIEW, f"в назначении только основной номер контракта — отнесено к {contract.number}",
                         f"付款用途只有主合同号，已归入 {contract.number}"))
        elif len(main) > 1:
            subs.append(("контракт", REVIEW, "в назначении основной номер нескольких контрактов: "
                         + ", ".join(c.number for c in main), "主合同号对应多个合同：" + ", ".join(c.number for c in main)))
        if contract is None and not full and not main:
            # по контрагенту
            linked = [c for c in universe.by_key.values() if c.parties and cp and score(cp, c.parties)[0] >= min_ratio]
            if inn:
                linked += [c for c in universe.by_key.values() if inn in c.inns and c not in linked]
            if len(linked) == 1 and bool(rules.get("assign_by_counterparty", False)):
                contract, how = linked[0], "по контрагенту"
                subs.append(("контракт", REVIEW, f"контракт не указан в назначении — отнесено по контрагенту к "
                             f"{contract.number}", f"付款用途未写合同号，按对方户名归入 {contract.number}"))
            elif linked:
                subs.append(("контракт", REVIEW, "контракт в назначении не указан; контрагент известен по контрактам: "
                             + ", ".join(c.number for c in linked[:5]),
                             "付款用途未写合同号；对方与以下合同相关：" + ", ".join(c.number for c in linked[:5])))
            else:
                st = str(no_contract.get(direction or "in", "issue")).upper()
                st = st if st in (OK, REVIEW, ISSUE) else ISSUE
                subs.append(("контракт", st, "платёж без контракта: номер контракта в назначении не найден, "
                             "контрагент не связан ни с одним контрактом",
                             "无对应合同：付款用途中没有合同号，对方户名与任何合同无关"))
        if contract is not None:
            rec["contract"] = contract.number
            rec["matched_by"] = how
            if contract.glued:
                subs.append(("контракт", REVIEW, f"контракт {contract.number} в документах указан в «склейке» с другими",
                             f"合同 {contract.number} 在单据中与其他合同合并填写"))

            # 2. контрагент
            parties = set(contract.parties)
            rec["contract_parties"] = " | ".join(sorted(parties))[:300]
            if inn and contract.inns and inn in contract.inns:
                subs.append(("контрагент", OK, "ИНН контрагента совпадает", "对方纳税人识别号一致"))
            elif not cp:
                subs.append(("контрагент", REVIEW, "в выписке нет названия плательщика/получателя", "流水中没有对方户名"))
            elif parties:
                sc, who = score(cp, parties)
                rec["counterparty_score"] = round(sc, 2)
                if sc >= min_ratio:
                    subs.append(("контрагент", OK, f"контрагент = сторона контракта ({who})", f"对方户名与合同方一致（{who}）"))
                elif sc >= rev_ratio:
                    subs.append(("контрагент", REVIEW, f"контрагент «{cp}» похож на «{who}» ({sc:.0%}) — проверить",
                                 f"对方户名「{cp}」与「{who}」相似（{sc:.0%}），需复核"))
                else:
                    st = ISSUE if mismatch == "issue" else REVIEW
                    subs.append(("контрагент", st, f"контрагент «{cp}» не сторона контракта ({rec['contract_parties']}) — "
                                 "оплата третьим лицом? нужен документ-основание",
                                 f"对方户名「{cp}」不是合同方（{rec['contract_parties']}）——第三方付款？需提供依据"))

            # 3. валюта и сумма инвойса
            exp_amt, exp_cur, exp_src, notes = _expected(contract, order)
            rec["contract_currency"] = exp_cur
            val, rate = amount, ""
            if exp_cur and cur and exp_cur != cur:
                val, rate = _conv(amount, cur, exp_cur, date, side)
                rec["fx_rate"] = rate
                if np.isnan(val):
                    subs.append(("валюта", REVIEW, f"платёж в {cur}, контракт в {exp_cur}: {rate}",
                                 f"付款币种 {cur}，合同币种 {exp_cur}：无汇率"))
                else:
                    subs.append(("валюта", REVIEW, f"платёж в {cur}, контракт в {exp_cur} — пересчитано: "
                                 f"{_fmt(val)} {exp_cur} ({rate})", f"付款币种 {cur}，合同币种 {exp_cur}，已按汇率折算"))
            elif exp_cur and cur:
                subs.append(("валюта", OK, "валюта = валюте контракта", "币种与合同一致"))
            rec["amount_in_contract_ccy"] = _n(val)
            if inv_hit and inv_hit[0]["amount"]:
                inv = inv_hit[0]
                iv, irate = val, rate
                if inv["currency"] and cur and inv["currency"] != (exp_cur or cur):
                    iv, irate = _conv(amount, cur, inv["currency"], date, side)
                if not np.isnan(iv):
                    if nearly_equal(iv, inv["amount"], tol_abs, tol_pct):
                        subs.append(("инвойс", OK, f"сумма = инвойсу {inv['number']}", f"金额与发票 {inv['number']} 一致"))
                    elif iv < inv["amount"]:
                        subs.append(("инвойс", REVIEW, f"оплачено {_fmt(iv)} из {_fmt(inv['amount'])} по инвойсу {inv['number']}",
                                     f"发票 {inv['number']} 已付 {_fmt(iv)} / {_fmt(inv['amount'])}"))
                    else:
                        subs.append(("инвойс", ISSUE, f"платёж {_fmt(iv)} больше инвойса {inv['number']} ({_fmt(inv['amount'])})",
                                     f"付款 {_fmt(iv)} 超过发票 {inv['number']}（{_fmt(inv['amount'])}）"))
            paid.setdefault(contract.key, []).append((val, cur, direction, rate, date))
        rows.append(_finish(rec, subs))

    # ---------------------------------------------------------------- уровень контракта
    for key, c in universe.by_key.items():
        exp_amt, exp_cur, exp_src, notes = _expected(c, order)
        items = paid.get(key, [])
        rec = {"check": TITLE, "level": LVL_CONTRACT, "contract": c.number,
               "contract_parties": " | ".join(sorted(c.parties))[:300], "contract_currency": exp_cur,
               "expected_amount": _n(exp_amt), "expected_from": SRC_TITLES.get(exp_src, (exp_src,))[0]}
        subs = [("валюта", REVIEW, n, "单据中有多种币种") for n in notes]
        if not items:
            # основной контракт: оплаты пришли по его доп. соглашениям / приложениям («ДЕМО.1001» ← «ДЕМО.1001-01А»)
            kids = [o for o in universe.by_key.values() if o.key != key and "-" in o.number
                    and canon_contract(o.number.split("-")[0]) == key and paid.get(o.key)]
            if kids:
                items = [x for o in kids for x in paid[o.key]]
                rec["contract_parts"] = ", ".join(o.number for o in kids)
                subs.append(("оплата", OK, f"оплаты учтены по доп. соглашениям: {rec['contract_parts']}",
                             f"按补充协议计入付款：{rec['contract_parts']}"))
        if not items:
            st = str(rules.get("contract_without_payment", "review")).upper()
            st = st if st in (OK, REVIEW, ISSUE) else REVIEW
            subs.append(("оплата", st, "оплат по контракту в выписках нет", "流水中没有该合同的付款"))
            rows.append(_finish(rec, subs))
            continue
        rates = [x[3] for x in items if x[3]]
        if any(np.isnan(x[0]) for x in items):
            rec["paid_amount"] = None
            subs.append(("оплата", REVIEW, "часть платежей в другой валюте и без курса — сумму не сравнить",
                         "部分付款为其他币种且无汇率，无法比较金额"))
            rows.append(_finish(rec, subs))
            continue
        incoming = sum(x[0] for x in items if x[2] != "out")
        outgoing = sum(x[0] for x in items if x[2] == "out")
        total = abs(incoming - outgoing)
        rec["paid_amount"] = _n(total)
        rec["payments_count"] = len(items)
        if rates:
            rec["fx_rate"] = "; ".join(dict.fromkeys(rates))
        conv = " после пересчёта валют" if rates else ""
        conv_zh = "（已按汇率折算）" if rates else ""
        if np.isnan(exp_amt):
            subs.append(("оплата", REVIEW, f"оплачено {_fmt(total)} {exp_cur}; ожидаемая сумма по контракту неизвестна",
                         f"已付 {_fmt(total)}；合同应付金额未知"))
        else:
            share = total / exp_amt if exp_amt else np.nan
            rec["paid_share"] = None if np.isnan(share) else round(share * 100, 1)
            if nearly_equal(total, exp_amt, tol_abs, tol_pct):
                subs.append(("оплата", REVIEW if rates else OK, f"оплачено полностью{conv}: {_fmt(total)} из {_fmt(exp_amt)} "
                             f"{exp_cur} ({SRC_TITLES.get(exp_src, (exp_src,))[0]})",
                             f"已全额付款{conv_zh}：{_fmt(total)} / {_fmt(exp_amt)} {exp_cur}"))
            elif total < exp_amt:
                subs.append(("оплата", REVIEW, f"оплачено частично{conv}: {_fmt(total)} из {_fmt(exp_amt)} {exp_cur} "
                             f"({share:.0%})", f"部分付款{conv_zh}：{_fmt(total)} / {_fmt(exp_amt)} {exp_cur}（{share:.0%}）"))
            elif rates:
                # после пересчёта по официальному курсу «переплата» может быть разницей курсов
                # (в контракте бывает свой курс) — проверить, а не расхождение
                subs.append(("оплата", REVIEW, f"больше ожидаемого после пересчёта валют: {_fmt(total)} при "
                             f"{_fmt(exp_amt)} {exp_cur} ({share:.0%}) — сверить курс по контракту",
                             f"按汇率折算后超过应付金额：{_fmt(total)} / {_fmt(exp_amt)} {exp_cur}，请核对合同汇率"))
            else:
                subs.append(("оплата", ISSUE, f"переплата: {_fmt(total)} при ожидаемых {_fmt(exp_amt)} {exp_cur}",
                             f"超额付款：{_fmt(total)}，应付 {_fmt(exp_amt)} {exp_cur}"))
        rows.append(_finish(rec, subs))

    meta = {"payments": int(sum(1 for r in rows if r["level"] == LVL_PAY)), "contracts": len(universe.by_key),
            "not_booked_skipped": skipped_n,
            "summary": f"Платежей: {sum(1 for r in rows if r['level'] == LVL_PAY)}, контрактов: {len(universe.by_key)}"
                       + (f", непроведённых пропущено: {skipped_n}" if skipped_n else "")}
    return CheckOutput("payments", TITLE, sort_df(pd.DataFrame(rows)), meta=meta)


def _finish(rec: dict, subs: list) -> dict:
    active = [s for s in subs if s[1] != SKIP]
    rec["checks"] = "; ".join(f"{n}: {'✓' if st == OK else ('✗' if st == ISSUE else '?')}" for n, st, _, _ in active)
    rec["status"] = worst([s[1] for s in active]) if active else OK
    bad = [s for s in active if s[1] in (ISSUE, REVIEW)]
    if bad:
        rec["detail"] = "; ".join(f"{n}: {ru}" for n, _, ru, _ in bad)
        rec["detail_zh"] = "；".join(zh for _, _, _, zh in bad)
    else:
        rec["detail"] = "всё совпадает: " + ", ".join(f"{n} — {ru}" for n, _, ru, _ in active) if active else "—"
        rec["detail_zh"] = "全部一致：" + "；".join(zh for _, _, _, zh in active) if active else "—"
    return rec


register("payments", TITLE, run)
