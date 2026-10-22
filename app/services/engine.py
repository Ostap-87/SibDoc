from __future__ import annotations
# -*- coding: utf-8 -*-
"""Движок прогона: DQ-детекция -> все включённые проверки -> сводка."""
import time
from dataclasses import dataclass, field

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


def run(sources: dict[str, Source], cfg: dict) -> RunResult:
    t0 = time.time()
    result = RunResult()

    # 1. DQ-детекция по источникам/секциям/колонкам
    result.findings = data_quality.analyze(sources, cfg)

    # 2. Информация о загруженных источниках
    for slot, src in sources.items():
        result.sources_info.append({
            "slot": slot, "file": src.name, "rows": src.rows,
            "columns": len(src.df.columns)})

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
            import pandas as pd
            result.outputs[check_id] = CheckOutput(
                check_id, check_id, pd.DataFrame([{
                    "check": check_id, "status": "ISSUE",
                    "detail": f"Внутренняя ошибка проверки: {exc}"}]),
                meta={"error": str(exc)})

    result.duration_sec = round(time.time() - t0, 2)
    return result
