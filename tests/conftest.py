# -*- coding: utf-8 -*-
"""Общие фикстуры тестов: эталонные и демо-данные, изолированное хранилище."""
from __future__ import annotations

import glob
import os
import shutil
import sys

import pandas as pd
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


@pytest.fixture(scope="session")
def cfg():
    from app.config_loader import load_config
    return load_config()


def _read(path, slot, cfg):
    from app.services.loader import read_many
    from app.models import Source
    files = [(open(p, "rb").read(), os.path.basename(p)) for p in ([path] if isinstance(path, str) else path)]
    df, info = read_many(files, slot, cfg)
    return Source(slot=slot, name=files[0][1], df=df, info=info)


@pytest.fixture()
def ref_sources(cfg):
    """Эталонные данные репозитория (wide + purchase) + встроенные справочники."""
    from app.services.loader import load_sources
    src = load_sources({}, cfg)
    src["wide_table"] = _read(os.path.join(ROOT, "data", "wide_table.xlsx"), "wide_table", cfg)
    src["purchase"] = _read(os.path.join(ROOT, "data", "purchase.xlsx"), "purchase", cfg)
    return src


@pytest.fixture(scope="session")
def demo_dir():
    d = os.path.join(ROOT, "data", "demo")
    if not os.path.exists(os.path.join(d, "wide_table_demo.xlsx")):
        import subprocess
        subprocess.check_call([sys.executable, os.path.join(ROOT, "tools", "make_demo.py")])
    return d


@pytest.fixture()
def demo_sources(cfg, demo_dir):
    from app.services.loader import load_sources
    src = load_sources({}, cfg)
    src["wide_table"] = _read(os.path.join(demo_dir, "wide_table_demo.xlsx"), "wide_table", cfg)
    src["purchase"] = _read(os.path.join(demo_dir, "purchase_demo.xlsx"), "purchase", cfg)
    src["ru_dt"] = _read(sorted(glob.glob(os.path.join(demo_dir, "ru_dt", "*.xml"))), "ru_dt", cfg)
    src["cn_dec"] = _read(os.path.join(demo_dir, "cn_dec_demo.xlsx"), "cn_dec", cfg)
    src["sap_vs_cd"] = _read(os.path.join(demo_dir, "sap_vs_cd_demo.csv"), "sap_vs_cd", cfg)
    return src


@pytest.fixture()
def offline_rates(monkeypatch):
    """Курсы без сети: фиксированный курс USD/CNY = 6.7489 (как центральный курс на 24.09.2026)."""
    from app.checks import _common

    def fake(amount, f, t, on, side="cn"):
        table = {("CNY", "USD"): 1 / 6.7489, ("USD", "CNY"): 6.7489}
        if (f, t) not in table:
            return float("nan"), f"нет курса {f}->{t}"
        return amount * table[(f, t)], f"курс {f}/{t} = {table[(f, t)]:.6g} (тест, {on})"
    monkeypatch.setattr(_common, "fx_convert", fake)
    import app.checks.price as price
    import app.checks.cross_border as cb
    monkeypatch.setattr(price, "fx_convert", fake)
    monkeypatch.setattr(cb, "fx_convert", fake)
    return fake


@pytest.fixture()
def isolated_storage(tmp_path, monkeypatch):
    """Журнал, загрузки, прогоны, история правил и правила — во временной папке."""
    from app import config_loader, journal
    from app.services import runs
    monkeypatch.setattr(journal, "JOURNAL_FILE", str(tmp_path / "journal.jsonl"))
    monkeypatch.setattr(runs, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(config_loader, "HISTORY_DIR", str(tmp_path / "rules_history"))
    rules = tmp_path / "rules"
    shutil.copytree(os.path.join(ROOT, "config", "rules"), rules)
    monkeypatch.setattr(config_loader, "RULES_DIR", str(rules))
    return tmp_path


def row_of(df: pd.DataFrame, **kw) -> pd.DataFrame:
    m = pd.Series(True, index=df.index)
    for k, v in kw.items():
        m &= df[k].astype(str) == str(v)
    return df[m]
