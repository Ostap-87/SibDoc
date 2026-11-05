from __future__ import annotations
# -*- coding: utf-8 -*-
"""Выписки банков (Коннекторы/банк): файлы, которые бухгалтер уже получает из банка,
-> таблица платежей «одна строка = одна операция».

Форматы:
  * «1CClientBankExchange» — текстовый формат обмена 1С с «Клиент-банком»
    (Windows-1251 или DOS/cp866, строки «Ключ=Значение», секции СекцияРасчСчет …
    КонецРасчСчет и СекцияДокумент=<вид> … КонецДокумента, в конце КонецФайла);
  * ISO 20022 camt.053 (выписка), а также camt.052 (отчёт за день) и camt.054
    (уведомление) — у них одинаковые проводки Ntry / NtryDtls / TxDtls;
  * SWIFT MT940 (поля :20: :25: :28C: :60F: :61: :86: :62F:);
  * CSV / Excel из интернет-банков КНР и РФ (交易日期 / 借方发生额 / 贷方发生额 / 币种 /
    对方户名 / 摘要 …, «Дата операции / Дебет / Кредит / Назначение платежа»):
    колонки распознаются по словарю полей (config/fields.yaml, блок «выписки банка»)
    и по запасным названиям ниже (WEAK_HEADERS).

Колонки результата (поля словаря):
  pay_date, value_date, amount (всегда > 0), currency, pay_direction (in — поступление,
  out — списание), counterparty, counterparty_inn, counterparty_account, counterparty_bank,
  purpose, account (наш счёт), reference, entry_status, bank_format.
"""
import csv
import io
import re
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd

from app import fields as fdict
from app.normalizers import is_blank, norm_date, norm_number, norm_text

COLUMNS = ["pay_date", "value_date", "amount", "currency", "pay_direction", "counterparty",
           "counterparty_inn", "counterparty_account", "counterparty_bank", "purpose", "account",
           "reference", "entry_status", "bank_format"]
IN, OUT = "in", "out"
MAX_BYTES = 50 * 1024 * 1024


class BankFormatError(ValueError):
    pass


# ================================================================== определение формата
def _head_text(data: bytes, n: int = 4000) -> str:
    head = data[:n]
    if head.startswith(b"\xef\xbb\xbf"):
        head = head[3:]
    for enc in ("utf-8", "cp1251", "cp866"):
        try:
            return head.decode(enc)
        except UnicodeDecodeError:
            continue
    return head.decode("latin-1")


_CAMT_ROOTS = ("BkToCstmrStmt", "BkToCstmrAcctRpt", "BkToCstmrDbtCdtNtfctn")


def detect(data: bytes, filename: str = "") -> str | None:
    """-> '1c' | 'camt' | 'mt940' | None (таблицы банков определяются по слоту, а не по содержимому)."""
    if not data:
        return None
    text = _head_text(data)
    stripped = text.lstrip(" \r\n\t")
    if stripped.startswith("1CClientBankExchange"):
        return "1c"
    if stripped.startswith("<"):
        if any(t in text for t in _CAMT_ROOTS) or "urn:iso:std:iso:20022:tech:xsd:camt.05" in text:
            return "camt"
        return None
    if re.search(r"(^|\n|\{4:\s*\n?):20:", text) and re.search(r"\n:61:", text) \
            and re.search(r"\n:6[02][FM]:", text):
        return "mt940"
    return None


def parse(data: bytes, filename: str = "") -> tuple[pd.DataFrame, dict]:
    """Файл выписки (1С / camt / MT940) -> (таблица платежей, сведения о разборе)."""
    if len(data) > MAX_BYTES:
        raise BankFormatError("файл выписки больше 50 МБ — проверьте файл")
    kind = detect(data, filename)
    if kind == "1c":
        rows, meta = parse_1c(data)
    elif kind == "camt":
        rows, meta = parse_camt(data)
    elif kind == "mt940":
        rows, meta = parse_mt940(data)
    else:
        raise BankFormatError("не похоже на выписку: ожидается 1CClientBankExchange, camt.05x или MT940")
    df = pd.DataFrame(rows, columns=COLUMNS) if rows else pd.DataFrame(columns=COLUMNS)
    meta["rows"] = int(len(df))
    return df, meta


# ================================================================== общие помощники
def account_currency(account) -> str:
    """Валюта российского счёта по 6–8 цифрам (План счетов ЦБ: 810 — рубли, 840 — доллары, 156 — юани)."""
    s = re.sub(r"\D", "", str(account or ""))
    if len(s) != 20:
        return ""
    code = s[5:8]
    try:
        from app.rates import norm_currency
        cur = norm_currency(code)
    except Exception:  # noqa: BLE001
        cur = {"810": "RUB", "643": "RUB", "840": "USD", "156": "CNY", "978": "EUR"}.get(code, "")
    return cur if re.fullmatch(r"[A-Z]{3}", cur or "") else ""


def _norm_ccy(v) -> str:
    if is_blank(v):
        return ""
    try:
        from app.rates import norm_currency
        return norm_currency(v)
    except Exception:  # noqa: BLE001
        return norm_text(v).upper()


def _clean_party(name: str) -> str:
    """«ИНН 7700000000 ООО "Ромашка"» -> «ООО "Ромашка"»; //-разделители MT940 убираются."""
    s = norm_text(name)
    s = re.sub(r"^\s*ИНН\s*\d{10,12}\s*", "", s, flags=re.I)
    s = re.sub(r"\s*//+\s*$", "", s)
    return s.strip(" ,;/")


def _row(**kw) -> dict:
    rec = {c: "" for c in COLUMNS}
    rec.update({k: ("" if v is None else v) for k, v in kw.items()})
    if isinstance(rec.get("amount"), str):
        rec["amount"] = norm_number(rec["amount"])
    return rec


# ================================================================== 1CClientBankExchange
_1C_HEADER = "1CClientBankExchange"


def _decode_1c(data: bytes) -> str:
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", "replace")
    head = data[:2000]
    enc = "cp1251"
    m = re.search(rb"\xca\xee\xe4\xe8\xf0\xee\xe2\xea\xe0\s*=\s*(\w+)", head)          # «Кодировка» в cp1251
    m = m or re.search(rb"\x8a\xae\xa4\xa8\xe0\xae\xa2\xaa\xa0\s*=\s*(\w+)", head)     # «Кодировка» в cp866
    if m and m.group(1).upper().startswith(b"DOS"):
        enc = "cp866"
    try:
        text = data.decode("utf-8")
        if "Кодировка" in text or "СекцияДокумент" in text:
            return text
    except UnicodeDecodeError:
        pass
    return data.decode(enc, "replace")


def parse_1c(data: bytes) -> tuple[list[dict], dict]:
    """Формат обмена 1С:Предприятие ↔ «Клиент банка» (1CClientBankExchange, версии 1.01–1.03).

    Направление операции: документ, где наш счёт — счёт получателя (или заполнена
    «ДатаПоступило»), — поступление; где наш счёт — счёт плательщика («ДатаСписано»), —
    списание. Валюты в формате нет — она берётся из номера счёта (цифры 6–8)."""
    text = _decode_1c(data)
    lines = [ln.rstrip("\r") for ln in text.split("\n")]
    if not lines or not lines[0].lstrip("\ufeff \t").startswith(_1C_HEADER):
        raise BankFormatError("нет заголовка 1CClientBankExchange")
    header: dict[str, str] = {}
    accounts: list[str] = []
    docs: list[dict] = []
    cur: dict | None = None
    section = None
    for raw in lines[1:]:
        ln = raw.strip()
        if not ln:
            continue
        if ln.startswith("СекцияДокумент"):
            cur = {"_kind": ln.split("=", 1)[1].strip() if "=" in ln else ""}
            section = "doc"
            continue
        if ln == "КонецДокумента":
            if cur is not None:
                docs.append(cur)
            cur, section = None, None
            continue
        if ln == "СекцияРасчСчет":
            section = "acc"
            continue
        if ln == "КонецРасчСчет":
            section = None
            continue
        if ln == "КонецФайла":
            break
        if "=" not in ln:
            continue
        k, v = ln.split("=", 1)
        k, v = k.strip(), v.strip()
        if section == "doc" and cur is not None:
            cur[k] = v
        elif section == "acc":
            if k == "РасчСчет" and v and v not in accounts:
                accounts.append(v)
        else:
            if k == "РасчСчет" and v and v not in accounts:
                accounts.append(v)
            header.setdefault(k, v)
    own = set(accounts)
    rows = []
    for d in docs:
        payer_acc = d.get("ПлательщикСчет") or d.get("ПлательщикРасчСчет", "")
        payee_acc = d.get("ПолучательСчет") or d.get("ПолучательРасчСчет", "")
        if payee_acc in own and payer_acc not in own:
            direction = IN
        elif payer_acc in own and payee_acc not in own:
            direction = OUT
        elif d.get("ДатаПоступило") and not d.get("ДатаСписано"):
            direction = IN
        elif d.get("ДатаСписано"):
            direction = OUT
        else:
            direction = ""
        side = "Плательщик" if direction == IN else "Получатель"
        name = d.get(side + "1") or d.get(side) or ""
        purpose = " ".join(d.get(k, "") for k in ["НазначениеПлатежа"]
                           + [f"НазначениеПлатежа{i}" for i in range(1, 7)] if d.get(k))
        own_acc = payee_acc if direction == IN else payer_acc
        date = d.get("ДатаПоступило") if direction == IN else d.get("ДатаСписано")
        rows.append(_row(
            pay_date=norm_date(date or d.get("Дата", "")), value_date=norm_date(d.get("Дата", "")),
            amount=norm_number(d.get("Сумма", "")), currency=account_currency(own_acc),
            pay_direction=direction, counterparty=_clean_party(name),
            counterparty_inn=d.get(side + "ИНН", ""),
            counterparty_account=payer_acc if direction == IN else payee_acc,
            counterparty_bank=d.get(side + "Банк1", ""), purpose=norm_text(purpose), account=own_acc,
            reference=f"{d.get('_kind', '')} № {d.get('Номер', '')}".strip(" №"), entry_status="BOOK",
            bank_format="1CClientBankExchange " + header.get("ВерсияФормата", "")))
    meta = {"format": "1CClientBankExchange", "version": header.get("ВерсияФормата", ""),
            "encoding": header.get("Кодировка", ""), "accounts": accounts,
            "sender": header.get("Отправитель", ""), "period": f"{header.get('ДатаНачала', '')}–"
                                                                f"{header.get('ДатаКонца', '')}"}
    return rows, meta


# ================================================================== ISO 20022 camt.052/053/054
def _local(tag) -> str:
    return str(tag).rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _child(el, *names):
    """Цепочка дочерних элементов по локальным именам (без пространств имён)."""
    node = el
    for n in names:
        if node is None:
            return None
        node = next((c for c in node if _local(c.tag) == n), None)
    return node


def _children(el, name):
    return [c for c in (el if el is not None else []) if _local(c.tag) == name]


def _txt(el, *names) -> str:
    node = _child(el, *names) if names else el
    return (node.text or "").strip() if node is not None and node.text else ""


def _date_of(el) -> str:
    """<BookgDt><Dt>…</Dt></BookgDt> или <DtTm>…</DtTm>."""
    if el is None:
        return ""
    return norm_date(_txt(el, "Dt") or _txt(el, "DtTm")[:10])


def _acct_id(el) -> str:
    if el is None:
        return ""
    return _txt(el, "Id", "IBAN") or _txt(el, "Id", "Othr", "Id")


def _party_name(el) -> str:
    """camt.053.001.02: <Dbtr><Nm>; с версии 08: <Dbtr><Pty><Nm>; бывает <Agt>."""
    if el is None:
        return ""
    return _txt(el, "Nm") or _txt(el, "Pty", "Nm") or _txt(el, "Agt", "FinInstnId", "Nm")


def _agent(tx, role: str) -> str:
    """Банк контрагента: BIC (BICFI с версии 08, BIC в 02) или название."""
    fin = _child(tx, "RltdAgts", role, "FinInstnId") if tx is not None else None
    return _txt(fin, "BICFI") or _txt(fin, "BIC") or _txt(fin, "Nm") if fin is not None else ""


def _amount(el) -> tuple[float, str]:
    if el is None:
        return np.nan, ""
    return norm_number(el.text), el.get("Ccy", "")


def parse_camt(data: bytes) -> tuple[list[dict], dict]:
    """camt.053 (выписка), camt.052, camt.054: одна строка = одна TxDtls (или проводка Ntry,
    если деталей нет). Контрагент: для CRDT — плательщик (Dbtr), для DBIT — получатель (Cdtr)."""
    from app.services.xml_decl import _decode   # те же защиты: кодировка, DOCTYPE/ENTITY запрещены
    try:
        root = ET.fromstring(_decode(data))
    except ET.ParseError as exc:
        raise BankFormatError(f"XML выписки не читается: {exc}")
    except ValueError as exc:
        raise BankFormatError(str(exc))
    ns = root.tag.split("}")[0].lstrip("{") if root.tag.startswith("{") else ""
    msg = next((c for c in root.iter() if _local(c.tag) in _CAMT_ROOTS), None)
    if msg is None:
        raise BankFormatError("нет BkToCstmrStmt / BkToCstmrAcctRpt / BkToCstmrDbtCdtNtfctn")
    rows = []
    stmts = [c for c in msg if _local(c.tag) in ("Stmt", "Rpt", "Ntfctn")]
    accounts = []
    for st in stmts:
        acct = _child(st, "Acct")
        own = _acct_id(acct)
        acct_ccy = _txt(acct, "Ccy") if acct is not None else ""
        if own and own not in accounts:
            accounts.append(own)
        for ntry in _children(st, "Ntry"):
            n_amt, n_ccy = _amount(_child(ntry, "Amt"))
            ind = _txt(ntry, "CdtDbtInd")
            rvsl = _txt(ntry, "RvslInd").lower() == "true"
            status = _txt(ntry, "Sts") or _txt(ntry, "Sts", "Cd")
            bdate = _date_of(_child(ntry, "BookgDt"))
            vdate = _date_of(_child(ntry, "ValDt"))
            n_ref = _txt(ntry, "AcctSvcrRef") or _txt(ntry, "NtryRef")
            n_info = _txt(ntry, "AddtlNtryInf")
            txs = [tx for d in _children(ntry, "NtryDtls") for tx in _children(d, "TxDtls")]
            for tx in txs or [None]:
                ind_tx = (_txt(tx, "CdtDbtInd") if tx is not None else "") or ind
                if tx is not None and len(txs) > 1:
                    amt, ccy = _amount(_child(tx, "Amt"))
                    if np.isnan(amt):
                        amt, ccy = _amount(_child(tx, "AmtDtls", "TxAmt", "Amt"))
                    if np.isnan(amt):
                        amt, ccy = _amount(_child(tx, "AmtDtls", "InstdAmt", "Amt"))
                else:
                    amt, ccy = n_amt, n_ccy
                credit = ind_tx == "CRDT"
                if rvsl:                                   # сторно: направление обратное
                    credit = not credit
                parties = _child(tx, "RltdPties") if tx is not None else None
                if credit:
                    name = _party_name(_child(parties, "Dbtr")) or _party_name(_child(parties, "UltmtDbtr"))
                    cp_acc = _acct_id(_child(parties, "DbtrAcct"))
                    cp_bank = _agent(tx, "DbtrAgt")
                else:
                    name = _party_name(_child(parties, "Cdtr")) or _party_name(_child(parties, "UltmtCdtr"))
                    cp_acc = _acct_id(_child(parties, "CdtrAcct"))
                    cp_bank = _agent(tx, "CdtrAgt")
                purpose_parts = []
                rmt = _child(tx, "RmtInf") if tx is not None else None
                if rmt is not None:
                    purpose_parts += [(u.text or "").strip() for u in _children(rmt, "Ustrd") if u.text]
                    for strd in _children(rmt, "Strd"):
                        for el in strd.iter():
                            if _local(el.tag) in ("Nb", "Ref") and (el.text or "").strip():
                                purpose_parts.append(el.text.strip())
                if tx is not None and _txt(tx, "AddtlTxInf"):
                    purpose_parts.append(_txt(tx, "AddtlTxInf"))
                if n_info:
                    purpose_parts.append(n_info)
                refs = _child(tx, "Refs") if tx is not None else None
                ref = (_txt(refs, "EndToEndId") if refs is not None else "")
                if ref.upper() == "NOTPROVIDED":
                    ref = ""
                ref = ref or (_txt(refs, "AcctSvcrRef") if refs is not None else "") or n_ref
                rows.append(_row(pay_date=bdate or vdate, value_date=vdate, amount=amt,
                                 currency=_norm_ccy(ccy or n_ccy or acct_ccy),
                                 pay_direction=IN if credit else OUT, counterparty=norm_text(name),
                                 counterparty_account=cp_acc, counterparty_bank=cp_bank,
                                 purpose=norm_text(" ".join(dict.fromkeys(p for p in purpose_parts if p))),
                                 account=own, reference=ref,
                                 entry_status=("RVSL " if rvsl else "") + (status or ""),
                                 bank_format=(re.search(r"camt\.\d{3}\.\d{3}\.\d{2}", ns).group(0)
                                              if re.search(r"camt\.\d{3}\.\d{3}\.\d{2}", ns) else "camt")))
    meta = {"format": "ISO 20022 " + (re.search(r"camt\.\d{3}\.\d{3}\.\d{2}", ns).group(0)
                                      if re.search(r"camt\.\d{3}\.\d{3}\.\d{2}", ns) else "camt"),
            "accounts": accounts, "statements": len(stmts)}
    return rows, meta


# ================================================================== SWIFT MT940
_MT_TAG = re.compile(r"^:(\d{2}[A-Z]?):", re.M)
_F61 = re.compile(r"^(?P<vd>\d{6})(?P<ed>\d{4})?(?P<dc>R?[CD])(?P<fc>[A-Z])?(?P<amt>\d[\d,]*)"
                  r"(?P<tt>[NFS][A-Z0-9]{3})(?P<ref>[^/\n]*)(?://(?P<bref>[^\n]*))?(?:\n(?P<sup>.*))?", re.S)


def _mt_date(yymmdd: str) -> str:
    try:
        return pd.Timestamp(2000 + int(yymmdd[:2]), int(yymmdd[2:4]), int(yymmdd[4:6])).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return ""


def _mt_amount(s: str) -> float:
    return norm_number(s.replace(",", "."))


def _f86_parts(text: str) -> tuple[str, str, str]:
    """:86: — свободный текст; у части банков структурирован. -> (назначение, контрагент, счёт).

    Поддержано: подполя «?20…?29» (назначение), «?32/?33» (имя), «?31» (счёт) — немецкая
    структура; ключи «/NAME/», «/ORDP//NAME/», «/BENM//NAME/», «/REMI/» — по рекомендации SWIFT
    для MT940 в ряде банков. Прочие варианты — текст целиком в назначение."""
    t = text.replace("\r", "")
    if re.search(r"\?2\d", t):
        sub = dict(re.findall(r"\?(\d{2})([^?]*)", t.replace("\n", "")))
        purpose = " ".join(sub[k] for k in sorted(sub) if "20" <= k <= "29" or "60" <= k <= "63")
        return norm_text(purpose), norm_text(sub.get("32", "") + sub.get("33", "")), norm_text(sub.get("31", ""))
    flat = t.replace("\n", "")
    if re.search(r"/(NAME|REMI|ORDP|BENM)/", flat):
        name = re.search(r"/(?:ORDP|BENM)//NAME/([^/]+)", flat) or re.search(r"/NAME/([^/]+)", flat)
        remi = re.search(r"/REMI/(?:USTD//)?([^/]+(?:/(?!\w{4}/)[^/]+)*)", flat)
        acct = re.search(r"/(?:ORDP|BENM)//ACCT/([^/]+)", flat) or re.search(r"/ACCT/([^/]+)", flat)
        purpose = remi.group(1) if remi else flat
        return norm_text(purpose), norm_text(name.group(1) if name else ""), norm_text(acct.group(1) if acct else "")
    return norm_text(t.replace("\n", " ")), "", ""


def _mt_fields(stmt: str) -> list[tuple[str, str]]:
    """Текст одной выписки -> [(тег, значение)]; служебные строки «-}», «{5:…}» отбрасываются."""
    tags = list(_MT_TAG.finditer(stmt))
    out = []
    for i, m in enumerate(tags):
        end = tags[i + 1].start() if i + 1 < len(tags) else len(stmt)
        val = "\n".join(ln for ln in stmt[m.end():end].split("\n")
                        if not re.match(r"^\s*(-\}?|\{\d:.*)\s*$", ln)).strip("\n")
        out.append((m.group(1), val))
    return out


def parse_mt940(data: bytes) -> tuple[list[dict], dict]:
    """MT940 (Customer Statement Message): выписка начинается с :20:, валюта — из :60F:/:60M:,
    проводка — :61: и следующее за ней :86:."""
    text = None
    for enc in ("utf-8", "cp1251", "latin-1"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    starts = [m.start() for m in re.finditer(r"(?m)^:20:", text)]
    rows, accounts = [], []
    for i, pos in enumerate(starts):
        stmt = text[pos:starts[i + 1] if i + 1 < len(starts) else len(text)]
        account, ccy, last = "", "", None
        for tag, val in _mt_fields(stmt):
            if tag == "25":
                account = val.split("/")[-1].strip()
                if account and account not in accounts:
                    accounts.append(account)
            elif tag in ("60F", "60M"):
                m = re.match(r"[CD]\d{6}([A-Z]{3})", val)
                ccy = m.group(1) if m else ccy
            elif tag == "61":
                m = _F61.match(val)
                if not m:
                    last = None
                    continue
                dc = m.group("dc")
                credit = dc in ("C", "RD")             # RD — сторно дебета = поступление
                vd = _mt_date(m.group("vd"))
                ed = ""
                if m.group("ed"):
                    try:
                        ed = pd.Timestamp(2000 + int(m.group("vd")[:2]), int(m.group("ed")[:2]),
                                          int(m.group("ed")[2:])).strftime("%Y-%m-%d")
                    except ValueError:
                        ed = ""
                ref = (m.group("ref") or "").strip()
                if ref.upper() == "NONREF":
                    ref = ""
                last = _row(pay_date=ed or vd, value_date=vd, amount=_mt_amount(m.group("amt")),
                            currency=_norm_ccy(ccy), pay_direction=IN if credit else OUT, account=account,
                            reference=ref or (m.group("bref") or "").strip(),
                            purpose=norm_text(m.group("sup") or ""),
                            entry_status="BOOK" + (" RVSL" if dc.startswith("R") else ""), bank_format="MT940")
                rows.append(last)
            elif tag == "86" and last is not None:
                purpose, name, acc = _f86_parts(val)
                last["purpose"] = norm_text(" ".join(x for x in (last["purpose"], purpose) if x))
                last["counterparty"] = last["counterparty"] or name
                last["counterparty_account"] = last["counterparty_account"] or acc
                last = None
    if not starts:
        raise BankFormatError("в файле нет выписок MT940 (поля :20:)")
    return rows, {"format": "SWIFT MT940", "accounts": accounts, "statements": len(starts)}


# ================================================================== таблицы банков (CSV / Excel)
# Запасные названия колонок, слишком общие для единого словаря полей (там они «поймали» бы
# колонки деклараций): применяются только к выпискам. Свои названия — синонимом в fields.yaml.
WEAK_HEADERS = {
    "pay_date": ["Дата", "日期", "交易时间", "Date", "记账时间"],
    "amount": ["Сумма", "金额", "Amount", "交易额"],
    "currency": ["交易货币", "货币", "Валюта счета", "Currency", "Ccy"],
    "purpose": ["备注", "附言", "交易附言", "用途", "摘要", "Комментарий", "Описание"],
}
DIRECTION_WORDS = {
    IN: ["in", "c", "cr", "crdt", "credit", "кредит", "к", "приход", "поступление", "зачисление", "+",
         "贷", "贷方", "收", "收入", "来账", "入账", "收款"],
    OUT: ["out", "d", "dr", "dbit", "debit", "дебет", "д", "расход", "списание", "-",
          "借", "借方", "支", "支出", "往账", "出账", "付款"],
}
_DIR_INDEX = {fdict.header_key(w): k for k, ws in DIRECTION_WORDS.items() for w in ws}


def norm_direction(v) -> str:
    if is_blank(v):
        return ""
    return _DIR_INDEX.get(fdict.header_key(v), _DIR_INDEX.get(norm_text(v).lower(), ""))


def _find_col(df: pd.DataFrame, names) -> str | None:
    keys = {fdict.header_key(c): c for c in df.columns}
    for n in names:
        c = keys.get(fdict.header_key(n))
        if c is not None:
            return c
    return None


def normalize_table(df: pd.DataFrame, own_accounts=None) -> pd.DataFrame:
    """Таблица интернет-банка (колонки уже переименованы словарём полей) -> COLUMNS.

    Сумма: одна колонка со знаком или с признаком «借/贷», «Дебет/Кредит»; либо две колонки
    «списано» (debit_amount) и «зачислено» (credit_amount). Контрагент: «对方户名», а если
    есть только плательщик и получатель — та сторона, чей счёт не наш."""
    if df is None or df.empty:
        return pd.DataFrame(columns=COLUMNS)
    df = df.copy()
    if set(COLUMNS[:3]) <= set(df.columns) and "bank_format" in df.columns:
        return df                                       # уже разобранная выписка
    for field, names in WEAK_HEADERS.items():
        if field not in df.columns:
            c = _find_col(df, names)
            if c is not None and c not in COLUMNS:
                df = df.rename(columns={c: field})
    extra_purpose = [c for c in df.columns if c not in COLUMNS and
                     fdict.header_key(c) in {fdict.header_key(n) for n in WEAK_HEADERS["purpose"]}]
    own = {re.sub(r"\s", "", str(a)) for a in (own_accounts or []) if a}
    if not own and {"payer_account", "payee_account"} <= set(df.columns):
        # наш счёт — тот, что стоит в каждой строке то плательщиком, то получателем
        counts: dict[str, int] = {}
        for a, b in zip(df["payer_account"], df["payee_account"]):
            for acc in {re.sub(r"\s", "", str(x)) for x in (a, b) if not is_blank(x)}:
                counts[acc] = counts.get(acc, 0) + 1
        if counts:
            acc, n = max(counts.items(), key=lambda kv: kv[1])
            if n >= max(2, int(0.8 * len(df))):
                own = {acc}
    signed = "amount" in df.columns and any(norm_number(v) < 0 for v in df["amount"] if not is_blank(v))
    out = []
    g = lambda r, c: r.get(c) if c in df.columns and not is_blank(r.get(c)) else None  # noqa: E731
    for _, r in df.iterrows():
        debit, credit = norm_number(g(r, "debit_amount")), norm_number(g(r, "credit_amount"))
        amount = norm_number(g(r, "amount"))
        direction = norm_direction(g(r, "pay_direction"))
        if not np.isnan(credit) and credit != 0:
            amount, direction = credit, direction or IN
        elif not np.isnan(debit) and debit != 0:
            amount, direction = debit, direction or OUT
        if np.isnan(amount):
            continue                                    # итоговые и пустые строки
        if amount < 0:
            amount, direction = -amount, direction or OUT
        elif not direction and g(r, "amount") is not None and (signed or str(g(r, "amount")).strip().startswith("+")):
            direction = IN                              # в файле суммы со знаком: без минуса — поступление
        account = norm_text(g(r, "account") or "")
        payer, payee = norm_text(g(r, "payer_name") or ""), norm_text(g(r, "payee_name") or "")
        payer_acc = re.sub(r"\s", "", norm_text(g(r, "payer_account") or ""))
        payee_acc = re.sub(r"\s", "", norm_text(g(r, "payee_account") or ""))
        if not direction and (payer_acc or payee_acc) and own:
            direction = IN if payee_acc in own else OUT if payer_acc in own else ""
        if not direction and account and (payer_acc or payee_acc):
            acc = re.sub(r"\s", "", account)
            direction = IN if payee_acc == acc else OUT if payer_acc == acc else ""
        cp = norm_text(g(r, "counterparty") or "")
        cp_acc = norm_text(g(r, "counterparty_account") or "")
        if not cp and (payer or payee):
            cp = payer if direction == IN else payee if direction == OUT else (payer or payee)
            cp_acc = cp_acc or (payer_acc if direction == IN else payee_acc)
        if not account and (payer_acc or payee_acc):
            account = payee_acc if direction == IN else payer_acc if direction == OUT else ""
        purpose = [norm_text(g(r, "purpose") or "")] + [norm_text(r.get(c)) for c in extra_purpose
                                                         if not is_blank(r.get(c))]
        out.append(_row(pay_date=norm_date(g(r, "pay_date") or ""), value_date=norm_date(g(r, "value_date") or ""),
                        amount=amount, currency=_norm_ccy(g(r, "currency")) or account_currency(account),
                        pay_direction=direction, counterparty=cp,
                        counterparty_inn=norm_text(g(r, "counterparty_inn") or ""), counterparty_account=cp_acc,
                        counterparty_bank=norm_text(g(r, "counterparty_bank") or ""),
                        purpose=norm_text(" ".join(dict.fromkeys(p for p in purpose if p))),
                        account=account, reference=norm_text(g(r, "reference") or ""),
                        entry_status="BOOK", bank_format="таблица банка"))
    res = pd.DataFrame(out, columns=COLUMNS)
    if "source_file" in df.columns and len(res) == len(df):
        res.insert(0, "source_file", df["source_file"].values)
    return res


def read_csv_text(data: bytes) -> pd.DataFrame:
    """CSV интернет-банка с «шапкой» над таблицей (выписки КНР начинаются с реквизитов счёта):
    строка заголовков — первая, где есть дата и сумма/дебет/кредит."""
    text = None
    for enc in ("utf-8-sig", "gb18030", "cp1251"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    text = text or data.decode("utf-8", "replace")
    lines = text.splitlines()
    syn = fdict.synonyms_index()
    want = {"pay_date", "amount", "debit_amount", "credit_amount"}
    weak = {fdict.header_key(n) for k in ("pay_date", "amount") for n in WEAK_HEADERS[k]}
    start = 0
    for i, ln in enumerate(lines[:40]):
        cells = next(csv.reader([ln], delimiter=_delim(ln)), [])
        hits = sum(1 for c in cells if syn.get(fdict.header_key(c)) in want or fdict.header_key(c) in weak)
        if hits >= 2:
            start = i
            break
    body = "\n".join(lines[start:])
    df = pd.read_csv(io.StringIO(body), dtype=object, sep=_delim(lines[start] if lines else ","),
                     keep_default_na=True, skipinitialspace=True)
    df.columns = [str(c).strip().strip("\t") for c in df.columns]
    # реквизиты счёта в «шапке» над таблицей: «账号：6222…», «Счет: 40702…», «币种：人民币»
    head = " ".join(lines[:start])
    acc = re.search(r"(?:账号|帐号|Сч[её]т|Account)\s*[:：]?\s*([0-9][0-9 ]{9,33}[0-9])", head)
    if acc and not any(fdict.synonyms_index().get(fdict.header_key(c)) == "account" for c in df.columns):
        df["account"] = re.sub(r"\s", "", acc.group(1))
    ccy = re.search(r"(?:币种|Валюта)\s*[:：]\s*([^,，;\s]+)", head)
    if ccy and not any(fdict.header_key(c) in {fdict.header_key(x) for x in ("币种", "Валюта", "currency")}
                       for c in df.columns):
        df["currency"] = ccy.group(1)
    return df


def _delim(line: str) -> str:
    counts = {d: line.count(d) for d in (",", ";", "\t", "|")}
    best = max(counts, key=counts.get)
    return best if counts[best] else ","
