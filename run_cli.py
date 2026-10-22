from __future__ import annotations
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI-прогон PostControl Suite без сервера.

    python run_cli.py --wide data/wide_table.xlsx --purchase data/purchase.xlsx
    python run_cli.py                # использует файлы из config/примеров

Отчёты складываются в storage/reports (excel + pdf).
Код возврата: 1, если есть ISSUE или DQ-ERROR.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.config_loader import load_config
from app.services.engine import run as engine_run
from app.services.loader import load_sources
from app.services.report_excel import write_excel
from app.services.report_pdf import write_pdf


def main():
    ap = argparse.ArgumentParser(description="PostControl Suite CLI")
    ap.add_argument("--wide", help="wide table (xlsx/csv)")
    ap.add_argument("--purchase", help="purchase (xlsx/csv)")
    ap.add_argument("--sap-vs-cd", dest="sap_vs_cd", help="ручная таблица SAP vs CD")
    ap.add_argument("--drafts", dest="cd_drafts", help="черновики деклараций")
    ap.add_argument("--hs-ref", dest="hs_reference", help="справочник HS-кодов")
    ap.add_argument("--cde-ref", dest="cde_reference", help="справочник элементов декларации")
    args = ap.parse_args()

    cfg = load_config()
    uploaded = {}
    for slot, path in [("wide_table", args.wide), ("purchase", args.purchase),
                       ("sap_vs_cd", args.sap_vs_cd), ("cd_drafts", args.cd_drafts),
                       ("hs_reference", args.hs_reference), ("cde_reference", args.cde_reference)]:
        if path:
            uploaded[slot] = (open(path, "rb"), os.path.basename(path))

    print("=" * 66)
    print(" POSTCONTROL SUITE — CLI")
    print("=" * 66)

    sources = load_sources(uploaded, cfg)
    for slot, src in sources.items():
        print(f"  {slot:<14} {src.name}  ({src.rows} строк)")

    result = engine_run(sources, cfg)

    print("-" * 66)
    print(" DATA-QUALITY (по источникам/секциям):")
    for f in result.findings:
        print(f"  [{f.severity:<7}] {f.source} / {f.section} / {f.column or '-'}: {f.issue}")
    if not result.findings:
        print("  дефектов данных не найдено")

    print("-" * 66)
    for row in result.summary_rows():
        print(f"  {row['check']:<18} total={row['total']:<4} OK={row['OK']:<4} "
              f"ISSUE={row['ISSUE']:<4} REVIEW={row['REVIEW']:<4} SKIPPED={row['SKIPPED']}")

    out_dir = cfg["output"]["dir"]
    excel = write_excel(result, cfg, os.path.join(out_dir, cfg["output"]["excel_name"]))
    pdf = write_pdf(result, cfg, os.path.join(out_dir, cfg["output"]["pdf_name"]))
    print("-" * 66)
    print(f"  Excel: {excel}")
    print(f"  PDF  : {pdf}")
    print(f"  Всего ISSUE: {result.total_issues()}, DQ-ERROR: {result.dq_counts()['ERROR']}")

    sys.exit(1 if result.total_issues() or result.dq_counts()["ERROR"] else 0)


if __name__ == "__main__":
    main()
