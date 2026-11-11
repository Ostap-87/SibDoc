from __future__ import annotations
# -*- coding: utf-8 -*-
"""SibDoc 3, режим «реальное время»: проверка ближайшей отгрузки до подачи декларации.

    from app import realtime
    result = realtime.run(package_id)                     # все декларации-сканы пакета
    result = realtime.run(package_id, declarations=["d007"], draft=["d008"], system="retro",
                          retro_sources=server_sources)    # выбрать источники явно

Результат — JSON для интерфейса (сводка + строки сверки) и Excel «Проверка отгрузки.xlsx»
в storage/packages/<id>/realtime/. Сверка — app/realtime/compare.py, правила —
config/rules/realtime.yaml, отчёт — app/realtime/report.py.
"""
import os
import shutil
import tempfile
import time
from datetime import datetime

from app.intake import classify as cls_mod
from app.intake import package as pkg
from app.intake import pipeline
from app.realtime import compare as cmp

RESULT_JSON = "result.json"
EXCEL_NAME = "Проверка отгрузки.xlsx"


def default_declarations(docs: list) -> list:
    """Все декларации пакета — ДТ РФ и 报关单, включая Excel и XML.

    Раньше по умолчанию брались только сканы (PDF / картинки) — и в проверку не попадали
    декларации из XML и Excel, хотя они есть в пакете. Теперь проверяются все: неполный охват
    без предупреждения хуже, чем лишняя строка.
    """
    return [d for d in docs if d["type"] in cmp.DECL_TYPES
            and d.get("status") not in (pipeline.STATUS_UNKNOWN, pipeline.STATUS_ERROR)]


def run(package_id: str, declarations: list | None = None, draft="auto", system="auto", references: bool = True,
        database: bool = False, retro_sources: dict | None = None, cfg: dict | None = None, user: str = "-") -> dict:
    from app.config_loader import load_config
    cfg = cfg or load_config()
    t0 = time.time()
    rl = cmp.rules()
    docs = pkg.documents(package_id)
    by_id = {d["id"]: d for d in docs}
    explicit = bool(declarations)
    if declarations:
        missing = [x for x in declarations if x not in by_id]
        if missing:
            raise pkg.NotFound(f"нет документов: {', '.join(missing)}")
        decls = [by_id[x] for x in declarations]
    else:
        decls = default_declarations(docs)
    if not decls:
        raise pkg.PackageError("в пакете нет деклараций (ДТ РФ или 报关单) — загрузите скан или укажите "
                               "тип документа вручную")
    refs = cmp.reference_sources(docs, cfg, rl) if references else {"info": ["справочники не выбраны"], "contracts": []}
    out_decls = []
    tmp = tempfile.mkdtemp(prefix="sibdoc-rt-")
    try:
        for decl in decls:
            draft_doc, draft_why = cmp.pick_draft(decl, docs, draft)
            sysd = cmp.system_lines(decl, docs, system, retro_sources, cfg, rl)
            dbd = cmp.database_lines(decl, cfg, rl, tmp) if database else \
                {"source": "none", "lines": None, "reason": "БД не выбрана", "docs": []}
            rows = cmp.sort_rows(cmp.compare_declaration(decl, draft_doc, sysd, dbd, refs, cfg, rl))
            out_decls.append({
                "doc_id": decl["id"], "file": decl["file"], "type": decl["type"],
                "type_label": cls_mod.type_label(decl["type"]), "format": decl.get("format"),
                "number": cmp.decl_number(decl), "side": cmp.side_of(decl),
                "draft": ({"doc_id": draft_doc["id"], "file": draft_doc["file"], "reason": draft_why}
                          if draft_doc else {"doc_id": None, "reason": draft_why}),
                "system": {"source": sysd.get("source"), "docs": sysd.get("docs") or [],
                           "lines": 0 if sysd.get("lines") is None else int(len(sysd["lines"])),
                           "reason": sysd.get("reason", ""), "note": sysd.get("note", "")},
                "database": {"source": dbd.get("source"), "docs": dbd.get("docs") or [],
                             "lines": 0 if dbd.get("lines") is None else int(len(dbd["lines"])),
                             "reason": dbd.get("reason", "")},
                "references": refs.get("info") or [],
                "counts": cmp.counts(rows), "rows": rows})
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    # охват: сколько деклараций пакета проверено и какие остались вне проверки
    all_decl = [d for d in docs if d["type"] in cmp.DECL_TYPES
                and d.get("status") not in (pipeline.STATUS_UNKNOWN, pipeline.STATUS_ERROR)]
    checked_ids = {d["id"] for d in decls}
    all_ids = {d["id"] for d in all_decl} | checked_ids      # явный выбор может включать документы вне all_decl
    coverage = {
        "checked": len(checked_ids),
        "available": len(all_ids),
        "explicit": explicit,
        "not_checked": [{"doc_id": d["id"], "file": d["file"],
                         "type_label": cls_mod.type_label(d["type"])}
                        for d in all_decl if d["id"] not in checked_ids],
    }
    total = {"OK": 0, "REVIEW": 0, "ISSUE": 0, "total": 0}
    for d in out_decls:
        for k in total:
            total[k] += d["counts"].get(k, 0)
    result = {"package_id": package_id, "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "user": user,
              "duration_sec": round(time.time() - t0, 2), "counts": total, "declarations": out_decls,
              "coverage": coverage,
              "rules": "config/rules/realtime.yaml",
              "excel": f"/api/realtime/{package_id}/excel"}
    d = pkg.realtime_dir(package_id)
    from app.realtime.report import write_excel
    write_excel(result, os.path.join(d, EXCEL_NAME))
    pkg._write_json(os.path.join(d, RESULT_JSON), result)
    return result


def last_result(package_id: str) -> dict | None:
    path = os.path.join(pkg.pkg_dir(package_id), "realtime", RESULT_JSON)
    return pkg._read_json(path) if os.path.exists(path) else None


def excel_path(package_id: str) -> str | None:
    path = os.path.join(pkg.pkg_dir(package_id), "realtime", EXCEL_NAME)
    return path if os.path.exists(path) else None
