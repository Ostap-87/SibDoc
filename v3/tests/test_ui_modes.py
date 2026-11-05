# -*- coding: utf-8 -*-
"""SibDoc 3: интерфейс из двух режимов (решение пользователя 05.11.2026) — «Ретро данные» и «Реальное время»,
выбор типов документов на первом экране, переключатель в шапке. Статические проверки webui/index.html и
webui/modes.js и короткий прогон в браузере (Playwright, если установлен)."""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time

import pytest
import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
WEBUI = os.path.join(ROOT, "webui")


def _read(name: str) -> str:
    return open(os.path.join(WEBUI, name), encoding="utf-8").read()


def _i18n() -> dict:
    m = re.search(r"/\*SD3-I18N\*/(.*?)/\*/SD3-I18N\*/", _read("modes.js"), re.S)
    assert m, "тексты модуля режимов — JSON между /*SD3-I18N*/ … /*/SD3-I18N*/"
    return json.loads(m.group(1))


def _js_list(name: str) -> list[str]:
    m = re.search(rf"const {name} = \[(.*?)\];", _read("modes.js"), re.S)
    return re.findall(r"'([a-z_]+)'", m.group(1))


def test_index_has_both_modes_switch_and_screens():
    html = _read("index.html")
    seg = re.search(r'<div class="seg" id="modeSeg"[^>]*>(.*?)</div>', html, re.S).group(1)
    assert 'data-mode="retro"' in seg and 'data-mode="realtime"' in seg          # переключатель режимов в шапке
    head = html[html.index("<header>"):html.index("</header>")]
    assert 'id="modeSeg"' in head and 'id="modeHome"' in head and 'id="netChip"' in head   # плашка контура — рядом
    for sid in ("scr-choose", "scr-retro", "scr-rt", "c-bulk"):
        assert f'id="{sid}"' in html
    # прежний пост-контроль — внутри экрана «Ретро данные»
    retro = html[html.index('id="scr-retro"'):html.index("</section>", html.index('id="scr-retro"'))]
    for card in ("c-data", "c-decl", "c-pkg", "c-run", "resCard", "c-journal", "c-conn"):
        assert f'id="{card}"' in retro
    assert html.index('<script src="modes.js"></script>') < html.index('<script src="offline.js"></script>')


def test_type_picker_covers_user_document_types_and_slots():
    types = _js_list("TYPES")
    # ДТ РФ, 报关单, черновик, выгрузка SAP/1С, инвойс, упаковочный лист, транспортные документы, сертификаты,
    # контракты, выписки банка, справочники
    assert types == ["ru_dt", "cn_dec", "draft", "system_export", "invoice", "packing", "transport", "conformity",
                     "contract", "bank", "reference"]
    classify = yaml.safe_load(open(os.path.join(ROOT, "config", "intake", "classify.yaml"), encoding="utf-8"))
    assert set(types) <= set(classify["types"])                      # те же типы, что у пакетной загрузки
    js = _read("modes.js")
    mapped = set(re.findall(r"'([a-z_]+)'", re.search(r"const TYPE_SLOTS = \{(.*?)\};", js, re.S).group(1)))
    sources = yaml.safe_load(open(os.path.join(ROOT, "config", "system.yaml"), encoding="utf-8"))["sources"]
    assert set(sources) <= mapped, f"слоты без типа документа: {set(sources) - mapped}"
    from app.api_intake import RETRO_SLOTS
    assert set(_js_list("RETRO_TYPES")) <= set(RETRO_SLOTS)          # «Добавить в сверку» — только то, что умеет API
    assert "localStorage" in js and "'sibdoc3.ui.' + USER" in js     # выбор запоминается для пользователя


def test_texts_ru_en_zh_and_used_keys():
    L = _i18n()
    assert set(L) == {"ru", "en", "zh"}
    for lang in ("en", "zh"):
        assert set(L[lang]) == set(L["ru"]), lang
    js = _read("modes.js")
    used = set(re.findall(r"\bt\('([a-z_0-9]+)'\)", js))
    used |= {"t_" + x for x in _js_list("TYPES")}
    used |= set(re.findall(r'data-(?:sd3|k)="([a-z_0-9]+)"', _read("index.html") + js))
    missing = sorted(k for k in used if k not in L["ru"])
    assert not missing, missing
    assert L["ru"]["seg_retro"] == "Ретро данные" and L["ru"]["seg_rt"] == "Реальное время"
    assert "пост-контроль прошедших отгрузок" in L["ru"]["m_retro_s"] and "до подачи" in L["ru"]["m_rt_s"]


def test_realtime_screen_uses_intake_and_realtime_api():
    js = _read("modes.js")
    for needle in ("/api/intake/packages", "/to-retro", "/documents/", "/type", "/excel", "/api/realtime/",
                   "/check", "/result", "reason_zh", "dataTransfer", "upload.onprogress", "'ISSUE'", "'REVIEW'", "'OK'"):
        assert needle in js, needle
    from app import server
    paths = set(server.app.openapi()["paths"])                       # и маршруты подключённых роутеров
    for p in ("/api/intake/packages", "/api/intake/packages/{pid}/to-retro", "/api/intake/packages/{pid}/excel",
              "/api/intake/packages/{pid}/documents/{doc_id}/type", "/api/realtime/{pid}/check",
              "/api/realtime/{pid}/result", "/api/realtime/{pid}/excel", "/api/intake/info"):
        assert p in paths, p


# ------------------------------------------------------------------ браузер (если есть Playwright)
def _playwright_node() -> dict | None:
    node = shutil.which("node")
    if not node:
        return None
    env = dict(os.environ)
    try:
        root = subprocess.run(["npm", "root", "-g"], capture_output=True, text=True, timeout=20).stdout.strip()
        if root:
            env["NODE_PATH"] = root + os.pathsep + env.get("NODE_PATH", "")
    except (OSError, subprocess.SubprocessError):
        pass
    ok = subprocess.run([node, "-e", "require('playwright')"], env=env, capture_output=True, timeout=30)
    return env if ok.returncode == 0 else None


SMOKE = r"""
const { chromium } = require('playwright');
(async () => {
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1280, height: 800 } });
  const errs = [];
  p.on('pageerror', e => errs.push(e.message));
  await p.goto(process.argv[2], { waitUntil: 'networkidle' });
  const out = {choose: await p.isVisible('#go-retro') && await p.isVisible('#go-realtime'),
               chips: await p.locator('#typeChips input').count()};
  await p.click('label.chip[data-type="reference"] input');
  await p.click('#go-realtime');
  await p.waitForSelector('#rt-top .drop');
  out.rt = await p.getAttribute('#modeSeg button[data-mode="realtime"]', 'aria-selected');
  await p.click('#modeSeg button[data-mode="retro"]');
  await p.waitForSelector('#otherSrc summary');
  out.retro = await p.isVisible('#c-bulk') && !(await p.isVisible('#scr-rt'));
  out.saved = await p.evaluate(() => JSON.parse(localStorage.getItem('sibdoc3.ui.local')));
  out.errs = errs;
  console.log(JSON.stringify(out));
  await b.close();
})().catch(e => { console.log(JSON.stringify({fatal: String(e)})); process.exit(1); });
"""


def test_browser_smoke_two_modes(isolated_storage, tmp_path, monkeypatch):
    env = _playwright_node()
    if env is None:
        pytest.skip("нет node + playwright")
    import uvicorn
    from app import server
    from app.intake import package as pkg
    monkeypatch.setattr(pkg, "PACKAGES_DIR", str(tmp_path / "packages"))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    try:
        for _ in range(100):
            if srv.started:
                break
            time.sleep(0.1)
        script = tmp_path / "smoke.js"
        script.write_text(SMOKE, encoding="utf-8")
        res = subprocess.run([shutil.which("node"), str(script), f"http://127.0.0.1:{port}/"], env=env,
                             capture_output=True, text=True, timeout=120)
        out = json.loads(res.stdout.strip().splitlines()[-1])
    finally:
        srv.should_exit = True
        th.join(10)
    assert "fatal" not in out, out
    assert out["choose"] and out["chips"] == 11
    assert out["rt"] == "true" and out["retro"]
    assert out["saved"]["mode"] == "retro" and "reference" not in out["saved"]["types"]
    assert out["errs"] == []
