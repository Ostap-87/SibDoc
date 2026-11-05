# -*- coding: utf-8 -*-
"""Демо-пакет SibDoc 3 (data/demo/package) — разбирается один раз на весь прогон тестов."""
from __future__ import annotations

import functools
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DEMO = os.path.join(ROOT, "data", "demo", "package")


def expected() -> dict:
    path = os.path.join(DEMO, "expected.json")
    if not os.path.exists(path):
        subprocess.check_call([sys.executable, os.path.join(ROOT, "tools", "make_demo_package.py")])
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


@functools.lru_cache(maxsize=1)
def storage_dir() -> str:
    import atexit
    import shutil
    d = tempfile.mkdtemp(prefix="sibdoc-test-packages-")
    atexit.register(shutil.rmtree, d, True)          # место на диске: после тестов не оставляем
    return d


@functools.lru_cache(maxsize=1)
def demo_package_id() -> str:
    """Разобрать демо-пакет (ZIP) во временное хранилище; -> id пакета."""
    from app.intake import package as pkg
    pkg.PACKAGES_DIR = storage_dir()
    exp = expected()
    with open(os.path.join(DEMO, exp["zip"]), "rb") as fh:
        data = fh.read()
    meta = pkg.create("realtime", "демо-пакет (тест)")
    meta = pkg.add_files(meta["id"], [(data, exp["zip"])])
    assert meta["status"] == "done", meta.get("message")
    return meta["id"]


def use_demo_storage() -> str:
    from app.intake import package as pkg
    pid = demo_package_id()
    pkg.PACKAGES_DIR = storage_dir()
    return pid
