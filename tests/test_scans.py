# -*- coding: utf-8 -*-
"""Сканы деклараций: разбор полей из OCR-текста, поиск tesseract, демо-сканы (если tesseract есть)."""
import io
import os
import stat

import pytest

from app.services import ocr_reader
from app.services.decl_parser import parse_declaration

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCANS = os.path.join(ROOT, "data", "demo", "scans")


def _v(parsed, field):
    return parsed[field]["value"], parsed[field]["status"]


def test_values_do_not_run_into_next_line():
    text = "33. Код ТН ВЭД: 4002311000\n\n38. Вес нетто: 23940 кг\n42. Цена за единицу: 9963,00 CNY/т\n"
    p = parse_declaration(text)
    assert _v(p, "hs_code") == ("4002311000", "found")
    assert _v(p, "net_weight") == ("23940", "found")
    assert _v(p, "unit_price") == ("9963.00", "found")


def test_cn_declaration_labels():
    text = ("海关 编号 : 220220260000000102\n合同 协议 号 : ДЕМО.1001-02А\n商品 编号 : 4002311000\n"
            "净重 : 23940 千克\n单价 : 9.176\n")
    p = parse_declaration(text)
    assert _v(p, "decl_num") == ("220220260000000102", "found")
    assert _v(p, "contract")[0] == "ДЕМО.1001-02А"
    assert _v(p, "hs_code") == ("4002311000", "found")


def test_model_l_read_as_exclamation():
    p = parse_declaration("Модель: Bromobuty! rubber BIIR-232\n")
    assert _v(p, "declared_model")[0] == "Bromobutyl rubber BIIR-232"


def test_tesseract_from_env(tmp_path, monkeypatch):
    exe = tmp_path / "tesseract"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("SIBDOC_TESSERACT", str(exe))
    assert ocr_reader.tesseract_cmd() == str(exe) and ocr_reader.tesseract_available()


@pytest.mark.skipif(not ocr_reader.tesseract_available(), reason="нет tesseract")
@pytest.mark.parametrize("name,field,value", [
    ("scan_DT_0000002.png", "decl_ru", "10000010/160926/0000002"),
    ("scan_CN_220220260000000102.jpg", "unit_price", "9.176"),
    ("scan_DT_0000004.pdf", "net_weight", "49500"),
])
def test_demo_scans(name, field, value):
    with open(os.path.join(SCANS, name), "rb") as fh:
        res = ocr_reader.read_scan(io.BytesIO(fh.read()), name)
    assert parse_declaration(res["text"])[field]["value"] == value
