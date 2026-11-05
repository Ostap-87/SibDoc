#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI-прогон PostControl Suite без сервера.

    python3 run_cli.py --wide data/wide_table.xlsx --purchase data/purchase.xlsx
    python3 run_cli.py --wide data/demo/wide_table_demo.xlsx --purchase data/demo/purchase_demo.xlsx \\
        --ru-dt "data/demo/ru_dt/*.xml" --cn-dec data/demo/cn_dec_demo.xlsx --sap-vs-cd data/demo/sap_vs_cd_demo.csv

Отчёты: storage/runs/<номер прогона>/ и последний — storage/reports/post_control_report.xlsx / .pdf.
Код возврата: 1, если есть ISSUE или ошибки качества данных.
"""
from __future__ import annotations

import argparse
import glob
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from app.config_loader import load_config  # noqa: E402
from app.models import Source  # noqa: E402
from app.services import labels, runs  # noqa: E402
from app.services.engine import run as engine_run  # noqa: E402
from app.services.loader import load_sources, read_many  # noqa: E402


def _expand(values: list[str] | None) -> list[str]:
    out = []
    for v in values or []:
        hits = sorted(glob.glob(v))
        out.extend(hits or [v])
    return out


def main():
    ap = argparse.ArgumentParser(description="PostControl Suite CLI")
    ap.add_argument("--wide", help="wide table (xlsx/csv)")
    ap.add_argument("--purchase", help="purchase (xlsx/csv)")
    ap.add_argument("--sap-vs-cd", dest="sap_vs_cd", help="ручная таблица SAP vs CD")
    ap.add_argument("--drafts", dest="cd_drafts", help="черновики деклараций")
    ap.add_argument("--hs-ref", dest="hs_reference", help="справочник HS-кодов")
    ap.add_argument("--cde-ref", dest="cde_reference", help="справочник элементов декларации")
    ap.add_argument("--ru-dt", dest="ru_dt", nargs="*", help="ДТ РФ: XML ФТС / Excel / ZIP (можно маску «*.xml»)")
    ap.add_argument("--cn-dec", dest="cn_dec", nargs="*", help="报关单 КНР: Excel / CSV / XML / ZIP")
    ap.add_argument("--user", default=os.environ.get("USER", "cli"), help="кто запускает (для журнала и отчёта)")
    args = ap.parse_args()

    cfg = load_config()
    print("=" * 70)
    print(" POSTCONTROL SUITE (SibDoc) — CLI")
    print("=" * 70)

    sources = load_sources({}, cfg)
    plan = [("wide_table", [args.wide] if args.wide else []), ("purchase", [args.purchase] if args.purchase else []),
            ("sap_vs_cd", [args.sap_vs_cd] if args.sap_vs_cd else []),
            ("cd_drafts", [args.cd_drafts] if args.cd_drafts else []),
            ("hs_reference", [args.hs_reference] if args.hs_reference else []),
            ("cde_reference", [args.cde_reference] if args.cde_reference else []),
            ("ru_dt", _expand(args.ru_dt)), ("cn_dec", _expand(args.cn_dec))]
    for slot, paths in plan:
        if not paths:
            continue
        files = []
        for p in paths:
            with open(p, "rb") as fh:
                files.append((fh.read(), os.path.basename(p)))
        df, info = read_many(files, slot, cfg)
        sources[slot] = Source(slot=slot, name=", ".join(n for _, n in files[:3]) + (" …" if len(files) > 3 else ""),
                               df=df, info=info)
    for slot, src in sources.items():
        print(f"  {slot:<14} {src.name}  ({src.rows} строк)")

    result = engine_run(sources, cfg, user=args.user)

    print("-" * 70)
    print(" DATA-QUALITY (по источникам/секциям):")
    for f in result.findings:
        print(f"  [{f.severity:<7}] {f.source} / {f.section} / {f.column or '-'}: {f.issue}")
    if not result.findings:
        print("  дефектов данных не найдено")

    print("-" * 70)
    for row in result.summary_rows():
        print(f"  {row['check']:<18} total={row['total']:<4} OK={row['OK']:<4} "
              f"ISSUE={row['ISSUE']:<4} REVIEW={row['REVIEW']:<4} SKIPPED={row['SKIPPED']}"
              f"   — {labels.check_title(row['check_id'])}")
    for r in result.rates_used:
        print(f"  курс: {r}")

    files = runs.save(result, cfg)
    out = cfg["output"]
    print("-" * 70)
    print(f"  Прогон: {result.run_id}")
    print(f"  Excel: {os.path.join(out['dir'], out['excel_name'])}  (копия: {os.path.relpath(files['excel'])})")
    print(f"  PDF  : {os.path.join(out['dir'], out['pdf_name'])}  (копия: {os.path.relpath(files['pdf'])})")
    print(f"  DQ-ERROR: {result.dq_counts()['ERROR']}")
    print(f"  Всего ISSUE: {result.total_issues()}")
    try:
        from app import journal
        journal.log("run", args.user, f"CLI-прогон {result.run_id}: ISSUE {result.total_issues()}",
                    meta={"run_id": result.run_id, "summary": result.summary_rows()})
    except Exception:  # noqa: BLE001 — журнал не должен ронять CLI
        pass

    sys.exit(1 if result.total_issues() or result.dq_counts()["ERROR"] else 0)


if __name__ == "__main__":
    main()
