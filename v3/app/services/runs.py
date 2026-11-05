from __future__ import annotations
# -*- coding: utf-8 -*-
"""История прогонов: у каждого прогона свой номер, свои отчёты и сводка.

storage/runs/<номер>/summary.json, post_control_report_<номер>.xlsx / .pdf.
Последний отчёт дополнительно копируется под постоянным именем
(storage/reports/post_control_report.xlsx/.pdf) — для CLI, verify и интеграций.
Хранится output.keep_runs последних прогонов.
"""
import json
import os
import re
import shutil

from app.services.engine import RunResult
from app.services.report_excel import write_excel
from app.services.report_pdf import write_pdf

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
RUNS_DIR = os.path.join(PROJECT_ROOT, "storage", "runs")
_ID = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{4}$")


def _abs(path: str) -> str:
    return path if os.path.isabs(path) else os.path.join(PROJECT_ROOT, path)


def save(result: RunResult, cfg: dict) -> dict:
    """Записать отчёты и сводку прогона. -> {"excel", "pdf", "dir"}."""
    out = cfg.get("output") or {}
    run_dir = os.path.join(RUNS_DIR, result.run_id)
    os.makedirs(run_dir, exist_ok=True)
    excel = write_excel(result, cfg, os.path.join(run_dir, f"post_control_report_{result.run_id}.xlsx"))
    pdf = write_pdf(result, cfg, os.path.join(run_dir, f"post_control_report_{result.run_id}.pdf"))
    latest_dir = _abs(out.get("dir", "storage/reports"))
    os.makedirs(latest_dir, exist_ok=True)
    shutil.copy2(excel, os.path.join(latest_dir, out.get("excel_name", "post_control_report.xlsx")))
    shutil.copy2(pdf, os.path.join(latest_dir, out.get("pdf_name", "post_control_report.pdf")))
    summary = {"run_id": result.run_id, "created_at": result.created_at, "user": result.user,
               "duration_sec": result.duration_sec, "total_issues": result.total_issues(),
               "dq_counts": result.dq_counts(), "summary": result.summary_rows(),
               "sources": result.sources_info, "rates_used": result.rates_used,
               "excel": os.path.basename(excel), "pdf": os.path.basename(pdf)}
    with open(os.path.join(run_dir, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=1, default=str)
    _prune(int(out.get("keep_runs", 100)))
    return {"excel": excel, "pdf": pdf, "dir": run_dir}


def _prune(keep: int):
    if keep <= 0 or not os.path.isdir(RUNS_DIR):
        return
    ids = sorted((d for d in os.listdir(RUNS_DIR) if _ID.match(d)), reverse=True)
    for old in ids[keep:]:
        shutil.rmtree(os.path.join(RUNS_DIR, old), ignore_errors=True)


def list_runs(limit: int = 50) -> list[dict]:
    if not os.path.isdir(RUNS_DIR):
        return []
    out = []
    for d in sorted((d for d in os.listdir(RUNS_DIR) if _ID.match(d)), reverse=True)[:limit]:
        p = os.path.join(RUNS_DIR, d, "summary.json")
        if os.path.exists(p):
            try:
                with open(p, encoding="utf-8") as fh:
                    s = json.load(fh)
                out.append({k: s.get(k) for k in ("run_id", "created_at", "user", "total_issues", "dq_counts",
                                                  "summary", "duration_sec")})
            except (OSError, json.JSONDecodeError):
                continue
    return out


def report_path(run_id: str, kind: str) -> str | None:
    if not _ID.match(run_id or "") or kind not in ("excel", "pdf"):
        return None
    ext = "xlsx" if kind == "excel" else "pdf"
    p = os.path.join(RUNS_DIR, run_id, f"post_control_report_{run_id}.{ext}")
    return p if os.path.exists(p) else None
