from __future__ import annotations
# -*- coding: utf-8 -*-
"""Проверка ближайшей отгрузки: декларация со скана ↔ черновик ↔ система ↔ справочники ↔ БД.

Одна строка результата = одно поле шапки или одно поле товарной строки:
значение скана, черновика, системы, справочника, БД, статус OK / REVIEW / ISSUE
и причина по-русски и по-китайски. Правила — config/rules/realtime.yaml.

Логика существующих проверок используется, а не повторяется:
  - цена против системы — проверка «1. Price» (контракт сначала, повторный контейнер, единица цены);
  - HS-код против справочников — «2. HS code» (марка по материалу SAP и по 申报要素);
  - условия поставки против системы — «3. Incoterms» (синонимы, исключение Qingdao/BZA);
  - марка и 申报要素 против CDE — «4. Decl. elements»;
  - строки SAP в едином виде — _common.sap_lines, контракты — _common.contract_match.
Курсы валют без явного разрешения (currency.convert) не пересчитываются — сеть не нужна.
"""
import difflib
import re

import numpy as np
import pandas as pd

from app import fields as fdict
from app.checks import run_check
from app.checks._common import contract_match, norm_currency_safe, sap_lines, split_containers
from app.config_loader import load_rules
from app.models import Source, Status
from app.normalizers import (canon_model, is_blank, latinize, nearly_equal, norm_date, norm_decl, norm_hs,
                             norm_incoterms, norm_number, norm_text, norm_unit, price_per_ton)

OK, ISSUE, REVIEW = Status.OK.value, Status.ISSUE.value, Status.REVIEW.value
ORDER = {ISSUE: 0, REVIEW: 1, OK: 2}
DECL_TYPES = ("ru_dt", "cn_dec")
SRC = {"scan": ("скан", "扫描件"), "draft": ("черновик", "草稿"), "system": ("система", "系统数据"),
       "reference": ("справочник", "参考表"), "database": ("БД", "数据库")}
SIDES = ("draft", "system", "reference", "database")
DEFAULT_RULES = {"ocr_review_below": 0.6, "hs_prefix_len": 6, "per_kg_threshold": 100,
                 "match": {"item_no": 3, "container": 3, "hs_code": 2, "description": 2, "description_fuzzy": 0.6,
                           "min_score": 3},
                 "unmatched_draft_line": "issue", "unmatched_scan_line": "issue",
                 "system": {"source": "auto", "net_weight_unit": "auto", "weight_tolerance_pct": 0.5,
                            "weight_tolerance_kg": 1, "extra_containers": "issue"},
                 "reference": {"hs_code": True, "decl_elements": ["cn_dec"], "contracts": True},
                 "database": {"connector": "", "slots": ["purchase"]}, "currency": {"convert": False}}


def rules() -> dict:
    r = load_rules("realtime.yaml") or {}
    out = {**DEFAULT_RULES, **r}
    for k in ("match", "system", "reference", "database", "currency"):
        out[k] = {**DEFAULT_RULES[k], **(r.get(k) or {})}
    return out


# ------------------------------------------------------------------ форматирование и сообщения
def fmt(v) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return ""
    if isinstance(v, (int, float, np.integer, np.floating)):
        v = float(v)
        s = f"{v:,.3f}".rstrip("0").rstrip(".") if not v.is_integer() else f"{v:,.0f}"
        return s.replace(",", " ")
    if isinstance(v, (list, tuple, set)):
        return "; ".join(str(x) for x in v)
    return str(v)


def _src(side: str) -> tuple[str, str]:
    return SRC.get(side, (side, side))


def m_ok(side):
    ru, zh = _src(side)
    return f"{ru}: совпадает", f"{zh}：一致"


def m_ne(side, other, scan):
    ru, zh = _src(side)
    return f"{ru}: {other} ≠ скан {scan}", f"{zh}：{other} ≠ 扫描件 {scan}"


def m_ne_num(side, other, scan, diff, tol):
    ru, zh = _src(side)
    return (f"{ru}: {other} ≠ скан {scan} (разница {diff}, допуск {tol})",
            f"{zh}：{other} ≠ 扫描件 {scan}（差额 {diff}，允差 {tol}）")


def _worst(statuses) -> str:
    sts = [s for s in statuses if s in ORDER]
    return min(sts, key=lambda s: ORDER[s]) if sts else OK


# ------------------------------------------------------------------ сравнение значений
def _containers(v) -> list:
    if isinstance(v, (list, tuple, set)):
        out = []
        for x in v:
            out += split_containers(x)
        return list(dict.fromkeys(out))
    return split_containers(v)


def _similar(a, b) -> float:
    a, b = latinize(norm_text(a)), latinize(norm_text(b))
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def compare(kind: str, scan, other, spec: dict, rl: dict, side: str) -> tuple[str, str, str] | None:
    """-> (статус, причина RU, причина ZH) или None, если сравнивать не с чем."""
    if is_blank(other) or other in ([], ""):
        return None
    sev = ISSUE if spec.get("on_mismatch", "issue" if spec.get("key") else "review") == "issue" else REVIEW
    if is_blank(scan) or scan in ([], ""):
        if not spec.get("key"):
            return None                  # неключевое поле, которого нет на скане, — не шум
        ru, zh = _src(side)
        return (REVIEW, f"на скане не распознано; {ru}: {fmt(other)}", f"扫描件未识别；{zh}：{fmt(other)}")
    if kind == "decl":
        same = norm_decl(scan) == norm_decl(other)
    elif kind == "contract":
        k = contract_match(scan, str(other)) or contract_match(other, str(scan))
        if k == "main":
            ru, zh = _src(side)
            return REVIEW, f"{ru}: совпал только основной контракт ({other})", f"{zh}：仅主合同一致（{other}）"
        same = bool(k)
    elif kind == "incoterms":
        same = norm_incoterms(scan)[0] == norm_incoterms(other)[0]
    elif kind == "currency":
        same = norm_currency_safe(scan) == norm_currency_safe(other)
    elif kind == "hs":
        a, b = norm_hs(scan), norm_hs(other)
        n = int(rl.get("hs_prefix_len", 6))
        if a == b:
            return (OK, *m_ok(side))
        if a[:n] == b[:n] and len(a) >= n:
            ru, zh = _src(side)
            return (REVIEW, f"{ru}: {b} — совпадают первые {n} знаков, скан {a}",
                    f"{zh}：{b} — 前{n}位相同，扫描件 {a}")
        return (sev, *m_ne(side, b, a))
    elif kind == "number":
        a, b = norm_number(scan), norm_number(other)
        if np.isnan(a) or np.isnan(b):
            same = norm_text(scan) == norm_text(other)
        else:
            ta, tp = float(spec.get("tolerance_abs", 0) or 0), float(spec.get("tolerance_pct", 0) or 0)
            if nearly_equal(a, b, ta, tp):
                return (OK, *m_ok(side))
            tol = f"{tp:g} %" if tp else fmt(ta)
            diff = fmt(round(b - a, 6))
            pct = f" ({(b - a) / a * 100:+.1f} %)" if a else ""
            return (sev, *m_ne_num(side, fmt(b), fmt(a), diff + pct, tol))
    elif kind == "containers":
        a, b = set(_containers(scan)), set(_containers(other))
        if a == b:
            return (OK, *m_ok(side))
        miss, extra = sorted(b - a), sorted(a - b)
        ru, zh = _src(side)
        parts_ru, parts_zh = [], []
        if miss:
            parts_ru.append(f"нет на скане: {', '.join(miss)}")
            parts_zh.append(f"扫描件缺少：{', '.join(miss)}")
        if extra:
            parts_ru.append(f"нет в {'черновике' if side == 'draft' else 'источнике «' + ru + '»'}: {', '.join(extra)}")
            parts_zh.append(f"{zh}中没有：{', '.join(extra)}")
        return (sev, f"{ru}: " + "; ".join(parts_ru), f"{zh}：" + "；".join(parts_zh))
    elif kind == "model":
        a, b = canon_model(scan), canon_model(other)
        same = bool(a) and (a == b or a in b or b in a)
    elif kind == "date":
        same = (norm_date(scan) or norm_text(scan)) == (norm_date(other) or norm_text(other))
    elif kind == "text":
        r = _similar(scan, other)
        thr = float(spec.get("fuzzy", 0.8))
        if r >= thr:
            return (OK, *m_ok(side)) if r > 0.999 else (OK, f"{_src(side)[0]}: совпадает по смыслу ({r:.0%})",
                                                          f"{_src(side)[1]}：基本一致（{r:.0%}）")
        return (sev if spec.get("on_mismatch") else REVIEW, *m_ne(side, fmt(other), fmt(scan)))
    else:  # code
        same = latinize(norm_text(scan)).replace(" ", "") == latinize(norm_text(other)).replace(" ", "")
    return (OK, *m_ok(side)) if same else (sev, *m_ne(side, fmt(other), fmt(scan)))


# ------------------------------------------------------------------ стороны сравнения
def _doc_label(doc: dict) -> str:
    return f"{doc['id']} · {doc['file']}"


def side_of(doc: dict) -> str:
    """Российская или китайская сторона документа (по типу / по алфавиту текста)."""
    if doc.get("type") == "ru_dt":
        return "ru"
    if doc.get("type") == "cn_dec":
        return "cn"
    if "decl_cn" in (doc.get("fields") or {}) or any("decl_cn" in it["fields"] for it in doc.get("items") or []):
        return "cn"
    text = " ".join(t for u in doc.get("units") or [] for t, _ in u.get("lines") or [])
    cjk = len(re.findall(r"[一-鿿]", text))
    cyr = len(re.findall(r"[А-Яа-яЁё]", text))
    return "cn" if cjk > cyr else "ru"


def values(doc: dict) -> tuple[dict, list]:
    """Документ -> (шапка {поле: значение}, строки [{поле: значение, _no, _conf: {...}}])."""
    head = {f: fv.get("value") for f, fv in (doc.get("fields") or {}).items()}
    items = []
    for it in doc.get("items") or []:
        rec = {f: fv.get("value") for f, fv in it["fields"].items()}
        rec["_no"] = str(it.get("no") or "")
        rec["_fv"] = it["fields"]
        items.append(rec)
    return head, items


def doc_containers(doc: dict) -> list:
    head, items = values(doc)
    out = _containers(head.get("container"))
    for it in items:
        out += [c for c in _containers(it.get("container")) if c not in out]
    return out


def decl_number(doc: dict) -> str:
    head, items = values(doc)
    for f in ("decl_ru", "decl_cn", "draft_num"):
        if head.get(f):
            return str(head[f])
        for it in items:
            if it.get(f):
                return str(it[f])
    return doc.get("doc_number") or ""


def pick_draft(decl: dict, docs: list, spec) -> tuple[dict | None, str]:
    if spec in ("none", None, False):
        return None, "черновик не выбран"
    cands = [d for d in docs if d["type"] == "draft"]
    if isinstance(spec, (list, tuple)):
        cands = [d for d in docs if d["id"] in spec]
    if not cands:
        return None, "в пакете нет черновика декларации"
    side, conts, num = side_of(decl), set(doc_containers(decl)), norm_decl(decl_number(decl))
    contract = values(decl)[0].get("contract") or ""
    best, best_score, best_why = None, -99, ""
    for d in cands:
        sc, why = 0, []
        if side_of(d) == side:
            sc += 3
            why.append("та же сторона (" + ("РФ" if side == "ru" else "КНР") + ")")
        else:
            sc -= 5
        common = conts & set(doc_containers(d))
        if common:
            sc += 3 * len(common)
            why.append(f"общих контейнеров: {len(common)}")
        dn = norm_decl(decl_number(d))
        if num and dn and dn == num:
            sc += 5
            why.append("тот же номер декларации")
        dc = values(d)[0].get("contract") or ""
        if contract and dc and contract_match(contract, dc):
            sc += 2
            why.append("тот же контракт")
        if sc > best_score:
            best, best_score, best_why = d, sc, ", ".join(why)
    if best is None or (best_score < 1 and not isinstance(spec, (list, tuple))):
        return None, "подходящего черновика нет (другая сторона, нет общих контейнеров и номера)"
    return best, best_why or "выбран вручную"


def _purchase_df(docs: list, cfg: dict, rl: dict) -> tuple[pd.DataFrame, str]:
    """Строки выгрузок SAP / 1С пакета -> таблица в формате слота Purchase (как ждёт sap_lines)."""
    pf = ((cfg.get("sources") or {}).get("purchase") or {}).get("fields") or {}
    rows = []
    for d in docs:
        for it in d.get("items") or []:
            rec = {pf.get(f, f): fv.get("value") for f, fv in it["fields"].items()}
            rec["_doc"] = d["id"]
            rows.append(rec)
    df = pd.DataFrame(rows)
    note = ""
    wcol = pf.get("net_weight", "net_weight")
    unit = str((rl.get("system") or {}).get("net_weight_unit", "auto"))
    if wcol in df.columns:
        w = pd.to_numeric(df[wcol], errors="coerce")
        if unit == "kg" or (unit == "auto" and w.dropna().median() > 500):
            df[wcol] = w / 1000.0
            note = "вес в системе в килограммах — пересчитан в тонны"
    return df, note


def system_lines(decl: dict, docs: list, spec, retro: dict | None, cfg: dict, rl: dict) -> dict:
    """Системные строки (SAP / 1С) для отгрузки декларации."""
    src = spec if spec not in (None, True) else (rl.get("system") or {}).get("source", "auto")
    out = {"source": "none", "docs": [], "lines": pd.DataFrame(), "raw": pd.DataFrame(), "reason": "", "note": ""}
    if src in ("none", False):
        out["reason"] = "системные данные не выбраны"
        return out
    pkg = [d for d in docs if d["type"] == "system_export"] if not isinstance(src, (list, tuple)) else \
        [d for d in docs if d["id"] in src]
    sources = {}
    if pkg and src != "retro":
        raw, note = _purchase_df(pkg, cfg, rl)
        sources["purchase"] = Source(slot="purchase", name="пакет", df=raw)
        out.update(source="package", docs=[_doc_label(d) for d in pkg], raw=raw, note=note)
    elif src in ("auto", "retro") and retro:
        for slot in ("purchase", "wide_table"):
            if retro.get(slot) is not None:
                sources[slot] = retro[slot]
        if sources:
            raw = (sources.get("purchase") or sources.get("wide_table")).df
            out.update(source="retro", docs=[f"слот {s}: {sources[s].name}" for s in sources], raw=raw)
    if not sources:
        out["reason"] = "нет выгрузки SAP / 1С в пакете и данных в слотах ретро-режима"
        return out
    lines = sap_lines(sources, cfg)
    if lines.empty:
        out["reason"] = "в системных данных нет строк с контейнерами"
        return out
    conts = set(doc_containers(decl))
    num = norm_decl(decl_number(decl)) if decl.get("type") == "ru_dt" else ""
    sel = lines[lines["container"].isin(conts)] if conts else lines.iloc[0:0]
    why = f"по контейнерам декларации ({len(sel)} стр.)" if not sel.empty else ""
    if num:
        by_dt = lines[lines["decl_ru"] == num]
        if not by_dt.empty:
            sel = pd.concat([sel, by_dt]).drop_duplicates()
            why = (why + "; " if why else "") + f"по номеру ДТ ({len(by_dt)} стр.)"
    if sel.empty:
        contract = values(decl)[0].get("contract") or ""
        if contract:
            sel = lines[[bool(contract_match(contract, c)) for c in lines["contract"]]]
            why = f"по контракту {contract} ({len(sel)} стр.)" if not sel.empty else ""
    out["lines"] = sel.reset_index(drop=True)
    out["all_lines"] = len(lines)
    out["reason"] = why or "строк системы для этой отгрузки не найдено"
    out["sources"] = sources
    return out


def database_lines(decl: dict, cfg: dict, rl: dict, tmp_dir: str) -> dict:
    """Строки из БД через коннектор type: database (realtime.yaml → database.connector)."""
    cid = str((rl.get("database") or {}).get("connector") or "")
    out = {"source": "none", "lines": pd.DataFrame(), "reason": "", "docs": []}
    if not cid:
        out["reason"] = "БД не настроена (config/rules/realtime.yaml → database.connector)"
        return out
    from app import connectors
    from app.services.loader import read_table
    conn = connectors.get_connector(cfg, cid)
    if conn is None or getattr(conn, "type", "") != "database":
        out["reason"] = f"коннектор {cid} не найден или не типа database"
        return out
    try:
        got = conn.pull(tmp_dir)
    except Exception as exc:  # noqa: BLE001
        out["reason"] = f"БД: {exc}"
        return out
    slots = set((rl.get("database") or {}).get("slots") or ["purchase"])
    frames = []
    for item in got:
        if item.get("slot") in slots:
            with open(item["path"], "rb") as fh:
                df, _ = read_table(fh.read(), item.get("file") or "db.csv", "purchase", cfg)
            frames.append(df)
    if not frames:
        out["reason"] = "БД не вернула строк для слотов " + ", ".join(sorted(slots))
        return out
    lines = sap_lines({"purchase": Source("purchase", f"БД {cid}", pd.concat(frames, ignore_index=True))}, cfg)
    conts = set(doc_containers(decl))
    sel = lines[lines["container"].isin(conts)] if conts else lines.iloc[0:0]
    out.update(source="database", lines=sel.reset_index(drop=True), docs=[f"коннектор {cid}"],
               reason=f"по контейнерам декларации ({len(sel)} стр.)" if not sel.empty else "строк не найдено")
    return out


def reference_sources(docs: list, cfg: dict, rl: dict) -> dict:
    """Справочники: встроенные (references/) + справочники и контракты пакета."""
    import os
    from app.services.loader import load_sources, PROJECT_ROOT
    base = load_sources({}, cfg)
    info = []
    hs_cols = ((cfg.get("sources") or {}).get("hs_reference") or {}).get("fields") or {}
    cde_cols = ((cfg.get("sources") or {}).get("cde_reference") or {}).get("fields") or {}
    hs_rows, cde_rows = [], []
    for d in docs:
        if d["type"] != "reference":
            continue
        n = 0
        for it in d.get("items") or []:
            v = {f: fv.get("value") for f, fv in it["fields"].items()}
            if not v.get("hs_code"):
                continue
            n += 1
            hs_rows.append({hs_cols.get("hs_code", "hs_code"): v.get("hs_code"),
                            hs_cols.get("model", "declared_model"): v.get("model") or v.get("product_name")})
            cde_rows.append({cde_cols.get("material", "Material"): v.get("material"),
                             cde_cols.get("hs_code", "HS Code"): v.get("hs_code"),
                             cde_cols.get("model", "报关单申报型号"): v.get("model"),
                             cde_cols.get("decl_elements", "申报要素"): v.get("decl_elements")})
        info.append(f"{_doc_label(d)}: строк {n}")
    out = {"info": info}
    for slot, rows in (("hs_reference", hs_rows), ("cde_reference", cde_rows)):
        cur = base.get(slot)
        frames = [cur.df] if cur is not None else []
        if rows:
            frames.append(pd.DataFrame(rows))
        if frames:
            out[slot] = Source(slot=slot, name=(cur.name if cur else "пакет") + (" + пакет" if rows else ""),
                               df=pd.concat(frames, ignore_index=True, sort=False))
        if cur is not None:
            info.insert(0, f"{slot}: {cur.name} ({len(cur.df)} стр.)")
    contracts = []
    for d in docs:
        if d["type"] != "contract":
            continue
        for v in ((d.get("fields") or {}).get("contract") or {}).get("value"), d.get("doc_number"):
            if v and str(v) not in [c for c, _ in contracts]:
                contracts.append((str(v), _doc_label(d)))
    path = os.path.join(PROJECT_ROOT, "references", "contracts.csv")
    if os.path.exists(path):
        try:
            df = pd.read_csv(path, dtype=str)
            col = next((c for c in df.columns if fdict.field_of_column(c) == "contract"), df.columns[0])
            contracts += [(str(c), "references/contracts.csv") for c in df[col].dropna()]
            info.append(f"references/contracts.csv: {len(df)} контрактов")
        except Exception as exc:  # noqa: BLE001
            info.append(f"references/contracts.csv не читается: {exc}")
    if contracts:
        info.append("контракты пакета: " + ", ".join(c for c, _ in contracts))
    out["contracts"] = contracts
    return out


# ------------------------------------------------------------------ сопоставление строк
def match_items(scan_items: list, draft_items: list, rl: dict) -> tuple[list, list]:
    """-> ([(строка скана, строка черновика | None, по каким признакам)], несопоставленные строки черновика)."""
    m = rl.get("match") or {}
    thr = float(m.get("description_fuzzy", 0.6))
    pairs = []
    for i, s in enumerate(scan_items):
        for j, d in enumerate(draft_items):
            score, why = 0.0, []
            if s.get("_no") and s.get("_no") == d.get("_no"):
                score += float(m.get("item_no", 3))
                why.append("№ строки")
            if set(_containers(s.get("container"))) & set(_containers(d.get("container"))):
                score += float(m.get("container", 3))
                why.append("контейнер")
            hs_a, hs_b = norm_hs(s.get("hs_code")), norm_hs(d.get("hs_code"))
            if hs_a and hs_b and hs_a[:4] == hs_b[:4]:
                score += float(m.get("hs_code", 2))
                why.append("код (4 знака)")
            desc = max(_similar(s.get("product_name"), d.get("product_name")),
                       1.0 if canon_model(s.get("model")) and canon_model(s.get("model")) == canon_model(d.get("model"))
                       else 0.0)
            if desc >= thr:
                score += float(m.get("description", 2))
                why.append("наименование / марка")
            if score >= float(m.get("min_score", 3)):
                pairs.append((score, i, j, why))
    pairs.sort(key=lambda x: (-x[0], x[1], x[2]))
    used_s, used_d, out = set(), set(), {}
    for score, i, j, why in pairs:
        if i in used_s or j in used_d:
            continue
        used_s.add(i)
        used_d.add(j)
        out[i] = (draft_items[j], ", ".join(why))
    res = [(s, *(out.get(i, (None, "")))) for i, s in enumerate(scan_items)]
    return res, [d for j, d in enumerate(draft_items) if j not in used_d]


# ------------------------------------------------------------------ цены
def _price_unit(raw) -> str:
    m = re.search(r"/\s*(т|t|тн|ton|mt|кг|kg|吨|千克|公斤)\b", str(raw or ""), re.I)
    return norm_unit(m.group(1)) if m else ""


def price_per_t(item: dict, thr: float) -> tuple[float, str]:
    """Цена товара за тонну: из цены за единицу (единица из колонки или «/т» в значении), иначе стоимость / вес."""
    fv = (item.get("_fv") or {}).get("unit_price") or {}
    price = norm_number(item.get("unit_price"))
    unit = _price_unit(fv.get("raw")) or norm_unit(item.get("unit"))
    if not np.isnan(price):
        v, basis = price_per_ton(price, unit, thr)
        return v, {"unit:kg": "цена за кг ×1000", "unit:t": "цена за т", "threshold": "цена ниже порога — за кг",
                   "as_is": "цена как есть", "unit:g": "цена за г", "unit:lb": "цена за фунт"}.get(basis, basis)
    tv, nw = norm_number(item.get("total_value")), norm_number(item.get("net_weight"))
    if not np.isnan(tv) and not np.isnan(nw) and nw > 0:
        return tv / nw * 1000, "стоимость / вес нетто"
    return float("nan"), ""


# ------------------------------------------------------------------ основная сверка
def _row(decl: dict, level: str, no: str, field: str, label_ru: str, label_zh: str, scan_val, scan_fv: dict | None,
         comps: dict, key: bool, rl: dict, extra_ru: str = "", extra_zh: str = "") -> dict | None:
    """comps: {сторона: (значение для показа, (статус, ru, zh) | None)}."""
    results = {s: c for s, c in comps.items() if c and c[1]}
    scan_missing = is_blank(scan_val) or scan_val in ([], "")
    if not results and not (key and scan_missing):
        return None                      # сравнивать не с чем, а поле не ключевое
    statuses = [c[1][0] for c in results.values()]
    ru = [c[1][1] for c in results.values() if c[1][0] != OK]
    zh = [c[1][2] for c in results.values() if c[1][0] != OK]
    if key and scan_missing:
        statuses.append(REVIEW)
        if not results:
            ru.append("не распознано на скане — проверить по оригиналу")
            zh.append("扫描件未识别 — 请核对原件")
    st = _worst(statuses)
    conf = (scan_fv or {}).get("conf")
    low = conf is not None and conf < float(rl.get("ocr_review_below", 0.6))
    if st == ISSUE and low:
        st = REVIEW
    if st != OK and low:
        ru.append(f"низкая уверенность распознавания ({conf:.0%}) — проверить по оригиналу")
        zh.append(f"识别置信度低（{conf:.0%}）— 请核对原件")
    if extra_ru:
        ru.append(extra_ru)
        zh.append(extra_zh or extra_ru)
    # совпадения — после расхождений: видно, с чем значение скана сошлось
    ru += [c[1][1] for c in results.values() if c[1][0] == OK]
    zh += [c[1][2] for c in results.values() if c[1][0] == OK]
    s = (scan_fv or {}).get("source") or {}
    where = ", ".join(x for x in [f"стр. {s['page']}" if s.get("page") else "",
                                  f"лист «{s['sheet']}»" if s.get("sheet") else "",
                                  f"строка {s['row']}" if s.get("row") else "",
                                  {"ocr": "OCR", "text": "текстовый слой", "xml": "XML", "table": "таблица",
                                   "pattern": "шаблон"}.get(s.get("method", ""), "")] if x)
    return {"declaration": decl_number(decl) or decl["id"], "doc_id": decl["id"], "level": level, "item_no": no,
            "field": field, "label": label_ru, "label_zh": label_zh,
            "scan": fmt(scan_val), "draft": fmt(comps.get("draft", ("", None))[0]),
            "system": fmt(comps.get("system", ("", None))[0]), "reference": fmt(comps.get("reference", ("", None))[0]),
            "database": fmt(comps.get("database", ("", None))[0]),
            "status": st, "reason": "; ".join(x for x in ru if x), "reason_zh": "；".join(x for x in zh if x),
            "scan_conf": conf, "scan_source": where, "key": bool(key)}


def _lbl(f: str) -> tuple[str, str]:
    return fdict.label(f, "ru"), fdict.label(f, "zh")


def _sys_header(lines: pd.DataFrame, raw: pd.DataFrame | None, field: str):
    if lines is None or lines.empty:
        return None
    if field == "contract":
        vals = sorted({c for c in lines["contract"] if c})
        return vals[0] if len(vals) == 1 else (vals or None)
    if field == "currency":
        vals = sorted({c for c in lines["currency"] if c})
        return vals[0] if len(vals) == 1 else (vals or None)
    if field == "decl_ru":
        vals = sorted({c for c in lines["decl_ru"] if c})
        return vals[0] if len(vals) == 1 else (vals or None)
    if field == "container":
        return sorted(set(lines["container"]))
    return None


def _cmp_multi(kind, scan, other, spec, rl, side):
    """Сравнение со списком значений системы: совпадение с любым = OK."""
    if isinstance(other, list) and kind not in ("containers",):
        results = [compare(kind, scan, o, spec, rl, side) for o in other]
        results = [r for r in results if r]
        if not results:
            return None
        return min(results, key=lambda r: ORDER[r[0]]) if any(r[0] == OK for r in results) else \
            (results[0][0], results[0][1] + f" (в системе несколько значений: {fmt(other)})", results[0][2])
    return compare(kind, scan, other, spec, rl, side)


def _wide_rows(decl: dict, items: list, sys_lines: pd.DataFrame, cfg: dict, thr: float) -> pd.DataFrame:
    """Строки декларации в формате wide table — чтобы применить существующие проверки."""
    W = ((cfg.get("sources") or {}).get("wide_table") or {}).get("fields") or {}
    head = values(decl)[0]
    num = decl_number(decl)
    rows = []
    for it in items:
        p_t, _ = price_per_t(it, thr)
        conts = _containers(it.get("container")) or _containers(head.get("container")) or [""]
        if len(items) > 1 and not _containers(it.get("container")):
            conts = [""]
        for c in conts:
            sl = sys_lines[sys_lines["container"] == c] if (c and sys_lines is not None and not sys_lines.empty) \
                else pd.DataFrame()
            s0 = sl.iloc[0] if not sl.empty else None
            main, _, add = (str(s0["contract"]).partition("-") if s0 is not None else ("", "", ""))
            rows.append({W.get("decl_cn", "decl_num"): num, W.get("item_no", "products_item_number"): it.get("_no"),
                         W.get("container", "container_num"): c, W.get("contract", "contract"): head.get("contract"),
                         W.get("hs_code", "product_code"): it.get("hs_code"),
                         W.get("product_name", "product_name"): it.get("product_name"),
                         W.get("decl_elements", "products_specs_and_models"):
                             it.get("decl_elements") or (f"МАРКА {it['model']}" if it.get("model") else ""),
                         W.get("unit_price", "products_price"): None if np.isnan(p_t) else p_t,
                         W.get("unit", "products_unit"): "т",
                         W.get("currency", "products_currency"): it.get("currency") or head.get("currency"),
                         W.get("incoterms", "incoterms"): head.get("incoterms"),
                         W.get("decl_date", "declaration_dt"): head.get("decl_date"),
                         W.get("material", "material"): (s0["material"] if s0 is not None else it.get("material")),
                         W.get("main_contract", "main_contract"): main, W.get("add_contract", "add_contract"): add,
                         "_item": it.get("_no")})
    return pd.DataFrame(rows)


def _existing(check_id: str, sources: dict, cfg: dict, overrides: dict | None = None) -> pd.DataFrame:
    rf = ((cfg.get("checks") or {}).get(check_id) or {}).get("rules", f"{check_id}.yaml")
    rules_ = {**(load_rules(rf) or {}), **(overrides or {})}
    try:
        return run_check(check_id, sources, rules_, cfg).df
    except Exception:  # noqa: BLE001 — сбой проверки не должен ронять сверку
        return pd.DataFrame()


def _zh_check(check_id: str, status: str) -> str:
    base = {"price": "单价与系统数据", "hs_code": "商品编号与参考表", "incoterms": "成交方式与系统数据",
            "decl_elements": "申报要素与主数据"}.get(check_id, check_id)
    return base + {"OK": "一致", "ISSUE": "不符", "REVIEW": "需人工核对"}.get(status, "")


def compare_declaration(decl: dict, draft: dict | None, system: dict, database: dict, refs: dict, cfg: dict,
                        rl: dict) -> list:
    rows: list = []
    head, items = values(decl)
    dhead, ditems = values(draft) if draft else ({}, [])
    fvs = decl.get("fields") or {}
    sys_l = system.get("lines") if system else pd.DataFrame()
    db_l = database.get("lines") if database else pd.DataFrame()
    thr = float(rl.get("per_kg_threshold", 100))
    sysr = rl.get("system") or {}
    level_h = "Шапка"

    # ---------------- шапка
    scan_conts = doc_containers(decl)
    draft_conts = doc_containers(draft) if draft else []
    for spec in rl.get("header_fields") or []:
        f, kind = spec["field"], spec.get("kind", "code")
        if f == "decl_cn" and decl["type"] != "cn_dec" or f == "decl_ru" and decl["type"] != "ru_dt":
            continue
        sv = scan_conts if f == "container" else head.get(f)
        comps = {}
        dv = draft_conts if f == "container" else dhead.get(f)
        if draft:
            comps["draft"] = (dv, compare(kind, sv, dv, spec, rl, "draft"))
        for side, ln in (("system", sys_l), ("database", db_l)):
            ov = _sys_header(ln, None, f)
            if ov is not None:
                c = _cmp_multi(kind, sv, ov, spec, rl, side)
                if f == "container" and c and c[0] != OK:
                    extra = sorted(set(_containers(ov)) - set(scan_conts))
                    if not extra:          # строки системы выбраны по контейнерам скана — лишних там нет
                        c = (OK, *m_ok(side)) if not (set(scan_conts) - set(_containers(ov))) else c
                    elif sysr.get("extra_containers", "issue") != "issue":
                        c = (REVIEW, c[1], c[2])
                comps[side] = (ov, c)
        if f == "contract" and (rl.get("reference") or {}).get("contracts") and refs.get("contracts") and sv:
            hit = [(c, lab) for c, lab in refs["contracts"] if contract_match(sv, c) or contract_match(c, str(sv))]
            if hit:
                comps["reference"] = (hit[0][0], (OK, f"справочник: контракт есть ({hit[0][1]})",
                                                  f"参考表：合同存在（{hit[0][1]}）"))
            else:
                comps["reference"] = (", ".join(c for c, _ in refs["contracts"][:3]),
                                      (REVIEW, "справочник: контракта нет среди контрактов пакета",
                                       "参考表：包内合同中无此合同"))
        if f == "incoterms" and sys_l is not None and not sys_l.empty and system.get("raw") is not None:
            res = _incoterms_vs_system(decl, sv, sys_l, system, cfg)
            if res:
                comps["system"] = res
        r = _row(decl, level_h, "", f, *_lbl(f), sv, fvs.get(f), comps, bool(spec.get("key")), rl)
        if r:
            rows.append(r)

    # общий вес нетто: скан vs черновик vs система
    def total(its, hd):
        vals = [norm_number(i.get("net_weight")) for i in its]
        vals = [v for v in vals if not np.isnan(v)]
        return sum(vals) if vals else norm_number(hd.get("net_weight"))
    t_scan = total(items, head)
    comps = {}
    spec_w = {"key": True, "tolerance_pct": sysr.get("weight_tolerance_pct", 0.5),
              "tolerance_abs": sysr.get("weight_tolerance_kg", 1)}
    if draft:
        t_d = total(ditems, dhead)
        comps["draft"] = (None if np.isnan(t_d) else t_d, compare("number", t_scan, None if np.isnan(t_d) else t_d,
                                                                 spec_w, rl, "draft"))
    for side, ln in (("system", sys_l), ("database", db_l)):
        if ln is not None and not ln.empty and ln["net_t"].notna().any():
            kg = float(ln["net_t"].sum() * 1000)
            comps[side] = (kg, compare("number", t_scan, kg, spec_w, rl, side))
    r = _row(decl, level_h, "", "decl_total_net_weight", "Вес нетто, всего (кг)", "总净重（千克）",
             None if np.isnan(t_scan) else t_scan, None, comps, False, rl)
    if r:
        rows.append(r)

    # ---------------- строки товаров
    pairs, lost = match_items(items, ditems, rl) if draft else ([(i, None, "") for i in items], [])
    own_conts = {}
    for it in items:
        for c in _containers(it.get("container")):
            own_conts[c] = own_conts.get(c, 0) + 1
    price_res = _price_vs_system(decl, items, sys_l, system, cfg, rl, thr)
    hs_res, de_res = _reference_checks(decl, items, sys_l, refs, cfg, rl, thr)
    for it, dit, how in pairs:
        no = it.get("_no") or ""
        level = f"Строка {no}"
        ifv = it.get("_fv") or {}
        if draft and dit is None and ditems:
            st = ISSUE if rl.get("unmatched_scan_line", "issue") == "issue" else REVIEW
            rows.append(_flat(decl, level, no, "_line", "Строка декларации", "报关单商品项", st,
                              "строки нет в черновике (не сопоставилась ни по одному признаку)",
                              "草稿中无此商品项", scan=it.get("product_name") or it.get("hs_code")))
        mine = [c for c in _containers(it.get("container"))]
        exclusive = mine and all(own_conts.get(c, 0) == 1 for c in mine)
        it_sys = sys_l[sys_l["container"].isin(mine)] if (exclusive and sys_l is not None and not sys_l.empty) \
            else (sys_l if (len(items) == 1 and sys_l is not None) else pd.DataFrame())
        it_db = db_l[db_l["container"].isin(mine)] if (exclusive and db_l is not None and not db_l.empty) \
            else (db_l if (len(items) == 1 and db_l is not None) else pd.DataFrame())
        for spec in rl.get("item_fields") or []:
            f, kind = spec["field"], spec.get("kind", "code")
            sv = it.get(f)
            comps = {}
            if dit is not None:
                comps["draft"] = (dit.get(f), compare(kind, sv, dit.get(f), spec, rl, "draft"))
            extra_ru = extra_zh = ""
            if f == "net_weight":
                for side, ln in (("system", it_sys), ("database", it_db)):
                    if ln is not None and not ln.empty and ln["net_t"].notna().any():
                        kg = float(ln["net_t"].sum() * 1000)
                        comps[side] = (kg, compare("number", sv, kg, spec_w | {"key": spec.get("key")}, rl, side))
            if f == "unit_price" and no in price_res:
                comps["system"] = price_res[no]
            if f == "hs_code" and no in hs_res:
                comps["reference"] = hs_res[no]
            if f in ("model", "decl_elements") and no in de_res and (f == "decl_elements" or not it.get("decl_elements")):
                comps["reference"] = de_res[no]
            if f == "container" and dit is None and not draft:
                continue
            if f == "unit_price" and not comps and sv is None:
                continue
            r = _row(decl, level, no, f, *_lbl(f), sv, ifv.get(f), comps, bool(spec.get("key")), rl,
                     extra_ru, extra_zh)
            if r:
                if how and dit is not None:
                    r["matched_by"] = how
                rows.append(r)
    for d in lost:
        st = ISSUE if rl.get("unmatched_draft_line", "issue") == "issue" else REVIEW
        rows.append(_flat(decl, f"Строка черновика {d.get('_no')}", d.get("_no") or "", "_line",
                          "Строка черновика", "草稿商品项", st,
                          f"строка {d.get('_no')} черновика не найдена в декларации",
                          f"草稿第{d.get('_no')}项在报关单中未找到",
                          draft=d.get("product_name") or d.get("hs_code")))
    return rows


def _flat(decl, level, no, field, label, label_zh, status, ru, zh, scan="", draft="") -> dict:
    return {"declaration": decl_number(decl) or decl["id"], "doc_id": decl["id"], "level": level, "item_no": no,
            "field": field, "label": label, "label_zh": label_zh, "scan": fmt(scan), "draft": fmt(draft),
            "system": "", "reference": "", "database": "", "status": status, "reason": ru, "reason_zh": zh,
            "scan_conf": None, "scan_source": "", "key": True}


def _incoterms_vs_system(decl, sv, sys_l, system, cfg):
    """Условия поставки против системы — проверкой «3. Incoterms» (если в выгрузке есть условия)."""
    raw = system.get("raw")
    pf = ((cfg.get("sources") or {}).get("purchase") or {}).get("fields") or {}
    ccol = pf.get("container", "Container number")
    icol = next((c for c in ("sap_incoterms", "incoterms") if c in raw.columns), None)
    if icol is None or ccol not in raw.columns:
        return None
    from app.normalizers import norm_container
    by_c = {norm_container(c): t for c, t in zip(raw[ccol], raw[icol]) if not is_blank(t)}
    W = ((cfg.get("sources") or {}).get("wide_table") or {}).get("fields") or {}
    rows = [{W.get("decl_cn", "decl_num"): decl_number(decl), W.get("container", "container_num"): c,
             W.get("incoterms", "incoterms"): sv, W.get("sap_incoterms", "shipreport_incoterms"): by_c[c],
             W.get("storage_location", "storage_location"): "", W.get("extra_costs", "extra_costs_value"): ""}
            for c in sorted(set(sys_l["container"])) if c in by_c]
    if not rows:
        return None
    out = _existing("incoterms", {"wide_table": Source("wide_table", "скан", pd.DataFrame(rows))}, cfg)
    if out.empty or "status" not in out.columns:
        return None
    st = _worst(out["status"].tolist())
    vals = sorted({str(v) for v in by_c.values()})
    detail = "; ".join(sorted(set(out[out["status"] == st]["detail"].astype(str))))
    return (", ".join(vals), (st, f"система (проверка «3. Incoterms»): {detail}", _zh_check("incoterms", st)))


def _price_vs_system(decl, items, sys_l, system, cfg, rl, thr) -> dict:
    """Цена товара против системы — проверкой «1. Price» на строках декларации."""
    out = {}
    if sys_l is None or sys_l.empty or not system.get("sources"):
        return out
    wide = _wide_rows(decl, items, sys_l, cfg, thr)
    if wide.empty:
        return out
    convert = bool((rl.get("currency") or {}).get("convert"))
    W = ((cfg.get("sources") or {}).get("wide_table") or {}).get("fields") or {}
    cur_col, ctn_col = W.get("currency", "products_currency"), W.get("container", "container_num")
    for it in items:
        no = it.get("_no")
        part = wide[(wide["_item"] == no) & (wide[ctn_col] != "")]
        if part.empty:
            continue
        lines = sys_l[sys_l["container"].isin(set(part[ctn_col]))]
        cur = norm_currency_safe(part.iloc[0][cur_col] or "")
        sys_cur = sorted({c for c in lines["currency"] if c})
        p_t, basis = price_per_t(it, thr)
        sys_txt = "; ".join(f"{fmt(p)} {c}/т" for p, c in sorted({(p, c) for p, c in zip(lines["price"], lines["currency"])}))
        if np.isnan(p_t):
            continue
        if cur and sys_cur and cur not in sys_cur and not convert:
            out[no] = (sys_txt, (REVIEW, f"система: валюта {', '.join(sys_cur)}, на скане {cur} — пересчёт по курсу "
                                         "выключен (realtime.yaml → currency.convert)",
                                 f"系统数据：币制 {', '.join(sys_cur)}，扫描件 {cur} — 未按汇率换算"))
            continue
        src = dict(system["sources"])
        if "purchase" in src:
            pdf = src["purchase"].df
            pf = ((cfg.get("sources") or {}).get("purchase") or {}).get("fields") or {}
            from app.normalizers import norm_container
            ccol = pf.get("container", "Container number")
            if ccol in pdf.columns:
                src["purchase"] = Source("purchase", src["purchase"].name,
                                         pdf[pdf[ccol].map(norm_container).isin(set(part[ctn_col]))])
        res = _existing("price", {**src, "wide_table": Source("wide_table", "скан", part.drop(columns=["_item"]))},
                        cfg, {"report_sap_only": False, "cd_price_unit": "t"})
        if res.empty or "status" not in res.columns:
            continue
        st = _worst(res["status"].tolist())
        det = "; ".join(sorted(set(res[res["status"] == st]["detail"].astype(str))))[:400]
        out[no] = (sys_txt, (st, f"система (проверка «1. Price», скан {fmt(round(p_t, 3))} {cur}/т — {basis}): {det}",
                             _zh_check("price", st)))
    return out


def _reference_checks(decl, items, sys_l, refs, cfg, rl, thr) -> tuple[dict, dict]:
    """HS-код (проверка «2. HS code») и марка / 申报要素 (проверка «4. Decl. elements») против справочников."""
    hs_out, de_out = {}, {}
    rr = rl.get("reference") or {}
    if rr.get("hs_code", True) and (refs.get("hs_reference") is not None or refs.get("cde_reference") is not None):
        wide = _wide_rows(decl, items, sys_l if sys_l is not None else pd.DataFrame(), cfg, thr)
        if not wide.empty:
            srcs = {"wide_table": Source("wide_table", "скан", wide.drop(columns=["_item"]))}
            for s in ("hs_reference", "cde_reference"):
                if refs.get(s) is not None:
                    srcs[s] = refs[s]
            res = _existing("hs_code", srcs, cfg)
            if not res.empty and "item_no" in res.columns:
                for it in items:
                    no = it.get("_no")
                    part = res[res["item_no"].astype(str) == str(no)]
                    if part.empty:
                        continue
                    st = _worst(part["status"].tolist())
                    exp = ", ".join(sorted({e for e in part.get("expected_hs", pd.Series(dtype=str)).astype(str)
                                            if e and e != "nan"}))
                    det = "; ".join(sorted(set(part[part["status"] == st]["detail"].astype(str))))
                    hs_out[no] = (exp, (st, f"справочник (проверка «2. HS code»): {det}", _zh_check("hs_code", st)))
    if decl.get("type") in (rr.get("decl_elements") or []) and refs.get("cde_reference") is not None:
        rows = [{"型号": it.get("model") or "", "申报要素": it.get("decl_elements") or "", "_item": it.get("_no")}
                for it in items if it.get("model") or it.get("decl_elements")]
        for r in rows:
            df = pd.DataFrame([{k: v for k, v in r.items() if k != "_item"}])
            res = _existing("decl_elements", {"cd_drafts": Source("cd_drafts", "скан", df),
                                              "cde_reference": refs["cde_reference"]}, cfg)
            if res.empty or "status" not in res.columns or (res["status"] == Status.SKIPPED.value).all():
                continue
            row0 = res.iloc[0]
            st = row0["status"] if row0["status"] in ORDER else REVIEW
            ref_val = str(row0.get("reference_model") or "")
            de_out[r["_item"]] = (ref_val, (st, f"справочник CDE (проверка «4. Decl. elements»): {row0.get('detail')}",
                                            _zh_check("decl_elements", st)))
    return hs_out, de_out


def counts(rows: list) -> dict:
    c = {OK: 0, REVIEW: 0, ISSUE: 0}
    for r in rows:
        c[r["status"]] = c.get(r["status"], 0) + 1
    c["total"] = len(rows)
    return c


def sort_rows(rows: list) -> list:
    return sorted(rows, key=lambda r: (ORDER.get(r["status"], 9)))
