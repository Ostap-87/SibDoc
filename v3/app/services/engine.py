from __future__ import annotations
# -*- coding: utf-8 -*-
"""Движок прогона: DQ-детекция -> все включённые проверки -> сводка."""
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from app import checks as registry
from app.config_loader import load_rules
from app.models import CheckOutput, Finding, Source
from app.services import data_quality


@dataclass
class RunResult:
    outputs: dict[str, CheckOutput] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)
    sources_info: list[dict] = field(default_factory=list)
    duration_sec: float = 0.0
    run_id: str = ""
    created_at: str = ""
    user: str = "-"
    sources: dict = field(default_factory=dict)      # загруженные источники (для листов ДТ/报关单 в отчётах)
    rates_used: list[str] = field(default_factory=list)

    def summary_rows(self) -> list[dict]:
        rows = []
        for cid, out in self.outputs.items():
            c = out.counts()
            rows.append({"check_id": cid, "check": out.title, **c})
        return rows

    def dq_counts(self) -> dict:
        return {
            "ERROR": sum(1 for f in self.findings if f.severity == "ERROR"),
            "WARNING": sum(1 for f in self.findings if f.severity == "WARNING"),
            "INFO": sum(1 for f in self.findings if f.severity == "INFO"),
        }

    def total_issues(self) -> int:
        return sum(o.counts()["ISSUE"] for o in self.outputs.values())


def new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]


def run(sources: dict[str, Source], cfg: dict, user: str = "-") -> RunResult:
    t0 = time.time()
    result = RunResult(run_id=new_run_id(), created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                       user=user, sources=dict(sources))

    # 1. DQ-детекция по источникам/секциям/колонкам
    result.findings = data_quality.analyze(sources, cfg)

    # 2. Информация о загруженных источниках
    for slot, src in sources.items():
        label = ((cfg.get("sources") or {}).get(slot) or {}).get("label", slot)
        info = getattr(src, "info", {}) or {}
        how = ", ".join(x for x in [
            info.get("format", ""), f"лист «{info['sheet']}»" if info.get("sheet") else "",
            f"кодировка {info['encoding']}" if info.get("encoding") else "",
            f"профиль {info['profile']}" if info.get("profile") else "",
            f"файлов: {len(info['files'])}" if len(info.get("files") or []) > 1 else ""] if x)
        result.sources_info.append({
            "slot": slot, "label": label, "file": src.name, "rows": src.rows,
            "columns": len(src.df.columns), "read_as": how,
            "mapped": len(info.get("mapped") or [])})

    # 3. Все включённые проверки
    for check_id, ccfg in (cfg.get("checks") or {}).items():
        if not ccfg.get("enabled", False):
            continue
        if check_id not in registry.available():
            continue
        rules = load_rules(ccfg.get("rules", f"{check_id}.yaml"))
        try:
            result.outputs[check_id] = registry.run_check(check_id, sources, rules, cfg)
        except Exception as exc:  # проверка не должна ронять весь прогон
            import traceback
            import pandas as pd
            result.outputs[check_id] = CheckOutput(
                check_id, check_id, pd.DataFrame([{
                    "check": check_id, "status": "ISSUE",
                    "detail": f"Внутренняя ошибка проверки: {exc}"}]),
                meta={"error": str(exc), "traceback": traceback.format_exc()[-2000:]})

    # курсы, по которым пересчитывались цены (для листа «Курсы» и PDF)
    seen = []
    for out in result.outputs.values():
        for col in ("fx_rate", "fx_rate_ru"):
            if col in out.df.columns:
                for v in out.df[col].dropna().astype(str):
                    if v and v not in seen and v.lower() != "nan":
                        seen.append(v)
    result.rates_used = seen
    result.duration_sec = round(time.time() - t0, 2)
    return result
