# -*- coding: utf-8 -*-
"""Иконка приложения SibDoc для Mac: assets/SibDoc.icns (+ assets/icon-1024.png).

    python3 tools/make_icon.py

Форма — как у иконок macOS (скруглённый квадрат 824 из 1024 с тенью), цвета — из интерфейса
(синий --blue, зелёный --ok): декларация с загнутым углом и зелёная отметка «сверено».
"""
import os

from PIL import Image, ImageDraw, ImageFilter

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUT = os.path.join(ROOT, "assets")
S = 2048                                   # рисуем вдвое крупнее и уменьшаем — ровные края


def _grad(size, top, bottom):
    g = Image.new("RGB", (1, 256))
    for y in range(256):
        t = y / 255
        g.putpixel((0, y), tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)))
    return g.resize(size)


def base(top, bottom):
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    box = (200, 200, S - 200, S - 200)
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle(box, radius=370, fill=255)
    shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((box[0], box[1] + 24, box[2], box[3] + 24), radius=370,
                                             fill=(0, 0, 0, 90))
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(28)))
    img.paste(_grad((S, S), top, bottom), (0, 0), mask)
    return img


def sibdoc():
    img = base((47, 107, 236), (18, 66, 168))
    d = ImageDraw.Draw(img)
    # лист декларации с загнутым углом
    x0, y0, x1, y1, fold = 600, 470, 1450, 1580, 230
    d.polygon([(x0, y0), (x1 - fold, y0), (x1, y0 + fold), (x1, y1), (x0, y1)], fill=(255, 255, 255))
    d.polygon([(x1 - fold, y0), (x1 - fold, y0 + fold), (x1, y0 + fold)], fill=(200, 214, 245))
    for i, w in enumerate((520, 640, 580, 640, 400)):
        y = 760 + i * 130
        d.rounded_rectangle((x0 + 110, y, x0 + 110 + w, y + 46), radius=23, fill=(170, 190, 230))
    # зелёная отметка «сверено»
    cx, cy, r = 1430, 1520, 300
    d.ellipse((cx - r - 24, cy - r - 24, cx + r + 24, cy + r + 24), fill=(18, 66, 168))
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(18, 160, 72))
    d.line([(cx - 150, cy + 10), (cx - 40, cy + 120), (cx + 160, cy - 110)], fill=(255, 255, 255), width=78,
           joint="curve")
    return img.resize((1024, 1024), Image.LANCZOS)


def save(img, name):
    os.makedirs(OUT, exist_ok=True)
    img.save(os.path.join(OUT, "icon-1024.png"))
    img.save(os.path.join(OUT, f"{name}.icns"), sizes=[(16, 16), (32, 32), (64, 64), (128, 128), (256, 256),
                                                       (512, 512), (1024, 1024)])
    print("Иконка:", os.path.join(OUT, f"{name}.icns"))


if __name__ == "__main__":
    save(sibdoc(), "SibDoc")
