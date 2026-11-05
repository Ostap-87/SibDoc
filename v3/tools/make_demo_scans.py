# -*- coding: utf-8 -*-
"""Демо-сканы деклараций для блока «Сканы деклараций» (OCR): вымышленные данные из make_demo.py.

    python3 tools/make_demo_scans.py   ->  data/demo/scans/

* scan_DT_0000002.png — ДТ РФ отгрузки …0000002 (картинка, как со сканера);
* scan_CN_220220260000000102.jpg — 报关单 той же отгрузки (цена ниже, чем в ДТ и SAP);
* scan_DT_0000004.pdf — ДТ …0000004 PDF-сканом без текстового слоя (вес больше, чем в SAP).
Шрифты: DejaVu Sans (кириллица) и WenQuanYi Zen Hei (китайский) — в Linux/Docker есть.
"""
from __future__ import annotations

import io
import os
import random
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, os.path.dirname(__file__))
from make_demo import C, CONSIGNEE_CN, CONSIGNEE_EN, CONSIGNOR, OUT, SHIP  # noqa: E402

FONT_RU = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_RU_B = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_ZH = "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"
W, H = 1654, 2339                      # A4, 200 dpi


def _font(path, size):
    return ImageFont.truetype(path, size)


def _page(title: str, sub: str, rows: list[tuple[str, str]], zh: bool = False) -> Image.Image:
    img = Image.new("L", (W, H), 250)
    d = ImageDraw.Draw(img)
    body = _font(FONT_ZH if zh else FONT_RU, 36)
    bold = _font(FONT_ZH if zh else FONT_RU_B, 46)
    small = _font(FONT_ZH if zh else FONT_RU, 28)
    d.text((110, 110), title, font=bold, fill=20)
    d.text((110, 180), sub, font=small, fill=70)
    d.line((110, 235, W - 110, 235), fill=40, width=3)
    y = 280
    for label, value in rows:
        d.text((130, y), f"{label}: {value}", font=body, fill=15)
        d.line((110, y + 70, W - 110, y + 70), fill=170, width=1)
        y += 96
    d.text((110, H - 160), "ДЕМО: все номера, компании и цены вымышленные" if not zh else
           "示例：所有编号、公司和价格均为虚构", font=small, fill=90)
    return img


def _scanned(img: Image.Image, seed: int) -> Image.Image:
    """Как со сканера: лёгкий поворот, размытие, шум, серая бумага."""
    rnd = random.Random(seed)
    img = img.rotate(rnd.uniform(-0.8, 0.8), resample=Image.BICUBIC, expand=False, fillcolor=245)
    img = img.filter(ImageFilter.GaussianBlur(0.7))
    noise = Image.effect_noise(img.size, 18).point(lambda v: (v - 128) // 6)
    px = img.load()
    nz = noise.load()
    for yy in range(0, img.height, 2):
        for xx in range(0, img.width, 2):
            px[xx, yy] = max(0, min(255, px[xx, yy] + nz[xx, yy] - 10))
    return img


def ru_dt(s: dict) -> Image.Image:
    rows = [
        ("Номер ДТ", s["dt"]),
        ("1. Отправитель / экспортёр", CONSIGNOR),
        ("8. Получатель", CONSIGNEE_EN),
        ("20. Условия поставки", s["terms"]),
        ("44. Контракт №", s.get("contract_ru", s["contract"])),
        ("31. Описание товара", s["name_ru"]),
        ("Модель", s["model"]),
        ("33. Код ТН ВЭД", s["hs_ru"]),
        ("38. Вес нетто", f"{s['net']} кг"),
        ("42. Цена за единицу", f"{s['price_t']:.2f}".replace(".", ",") + f" {s['cur']}/т"),
        ("31. Контейнер", ", ".join(C[c] for c in s["conts"])),
    ]
    return _page("ДЕКЛАРАЦИЯ НА ТОВАРЫ (ЭКСПОРТ)", "Форма ДТ — образец для проверки распознавания", rows)


def cn_dec(s: dict) -> Image.Image:
    net = s.get("cn_net", s["net"])
    rows = [
        ("海关编号", s["cn"]),
        ("境内收发货人", CONSIGNEE_CN),
        ("合同协议号", s["contract"]),
        ("成交方式", s["cn_terms"]),
        ("商品编号", s["hs_cn"]),
        ("商品名称", s["name_cn"]),
        ("规格型号", s["model"]),
        ("净重", f"{net} 千克"),
        ("单价", f"{s['cn_price']:.3f}"),
        ("币制", s["cn_cur"]),
        ("集装箱号", ", ".join(C[c] for c in s["conts"])),
    ]
    return _page("中华人民共和国海关出口货物报关单", "报关单样本（用于识别测试）", rows, zh=True)


def main():
    out = os.path.join(OUT, "scans")
    os.makedirs(out, exist_ok=True)
    by_dt = {s["dt"][-7:]: s for s in SHIP}
    s2, s4 = by_dt["0000002"], by_dt["0000004"]
    _scanned(ru_dt(s2), 2).save(os.path.join(out, "scan_DT_0000002.png"), optimize=True)
    _scanned(cn_dec(s2), 3).convert("RGB").save(os.path.join(out, f"scan_CN_{s2['cn']}.jpg"), quality=82)
    page = _scanned(ru_dt(s4), 4).convert("RGB")
    buf = io.BytesIO()
    page.save(buf, format="PDF", resolution=200.0)          # только картинка: текстового слоя нет
    with open(os.path.join(out, "scan_DT_0000004.pdf"), "wb") as fh:
        fh.write(buf.getvalue())
    print("Демо-сканы:", out)


if __name__ == "__main__":
    main()
