// SibDoc3: закрытый контур — индикатор в шапке, курсы из файла, «Обновление с носителя».
// Подключается из index.html одной строкой; работает с общими функциями страницы
// (LANG, api, toast, esc, renderRates, renderManual, renderJournal), если они есть.
// Внешних ресурсов нет: только запросы к своему серверу (/api/...).
(function(){
  const L = {
    ru: {
      on: "Закрытый контур: интернет не используется", off: "Есть доступ в интернет",
      allowed: "Разрешённые адреса внутри контура", nav: "Закрытый контур",
      rf_title: "Курсы из файла (флешка)",
      rf_hint: "ЦБ РФ XML_daily / XML_dynamic, CFETS 中间价 (Excel/CSV), шаблон Excel «дата, валюта, курс, источник». Можно несколько файлов или zip.",
      rf_ccy: "валюта для XML_dynamic", rf_load: "Загрузить курсы", rf_tpl: "Шаблон Excel",
      rf_none: "Выберите файл курсов", rf_net_off: "В закрытом контуре курсы не скачиваются — загрузите файл",
      up_title: "Обновление с носителя",
      up_hint: "Справочники (HS-коды, CDE, контракты), правила проверок и курсы — архив .zip с файлом SHA256SUMS (tools/make_update_package.py). Сначала «Проверить», затем «Установить». Прежние версии файлов сохраняются, установка записывается в журнал.",
      up_check: "Проверить", up_apply: "Установить", up_none: "Выберите архив .zip",
      up_ok: "Архив проверен: контрольные суммы совпадают", up_bad: "Архив не прошёл проверку",
      up_done: "Обновление установлено", up_hist: "Последние обновления",
      sig_ok: "подпись верна", sig_no: "без подписи", sig_req: "нужна подпись",
      col_file: "Файл", col_act: "Действие", col_ok: "Проверка", empty: "пока не было",
      confirm: "Установить обновление? Прежние версии файлов будут сохранены."
    },
    en: {
      on: "Closed network: internet is not used", off: "Internet access is enabled",
      allowed: "Allowed hosts inside the network", nav: "Closed network",
      rf_title: "Rates from a file (USB drive)",
      rf_hint: "Bank of Russia XML_daily / XML_dynamic, CFETS parity (Excel/CSV), Excel template “date, currency, rate, source”. Several files or a zip are fine.",
      rf_ccy: "currency for XML_dynamic", rf_load: "Load rates", rf_tpl: "Excel template",
      rf_none: "Choose a rates file", rf_net_off: "Rates are not downloaded in the closed network — load a file",
      up_title: "Update from media",
      up_hint: "References (HS codes, CDE, contracts), check rules and FX rates — a .zip with SHA256SUMS (tools/make_update_package.py). “Check” first, then “Install”. Previous files are backed up, the update is logged.",
      up_check: "Check", up_apply: "Install", up_none: "Choose a .zip archive",
      up_ok: "Archive verified: checksums match", up_bad: "Archive failed verification",
      up_done: "Update installed", up_hist: "Recent updates",
      sig_ok: "signature valid", sig_no: "not signed", sig_req: "signature required",
      col_file: "File", col_act: "Action", col_ok: "Check", empty: "none yet",
      confirm: "Install the update? Previous files will be backed up."
    },
    zh: {
      on: "封闭环境：不使用互联网", off: "可访问互联网",
      allowed: "环境内允许的地址", nav: "封闭环境",
      rf_title: "从文件导入汇率（U盘）",
      rf_hint: "俄罗斯央行 XML_daily / XML_dynamic、CFETS 中间价（Excel/CSV）、Excel 模板“日期、币种、汇率、来源”。可多个文件或 zip。",
      rf_ccy: "XML_dynamic 的币种", rf_load: "导入汇率", rf_tpl: "Excel 模板",
      rf_none: "请选择汇率文件", rf_net_off: "封闭环境中不在线获取汇率，请导入文件",
      up_title: "介质更新",
      up_hint: "参考表（HS编码、申报要素、合同）、检查规则和汇率：含 SHA256SUMS 的 .zip（tools/make_update_package.py）。先“校验”，再“安装”。旧文件会备份，更新写入日志。",
      up_check: "校验", up_apply: "安装", up_none: "请选择 .zip 文件",
      up_ok: "校验通过：校验和一致", up_bad: "校验未通过",
      up_done: "更新已安装", up_hist: "最近更新",
      sig_ok: "签名有效", sig_no: "未签名", sig_req: "需要签名",
      col_file: "文件", col_act: "操作", col_ok: "校验", empty: "暂无",
      confirm: "安装更新？旧文件将被备份。"
    }
  };
  const lang = () => { try { return (typeof LANG !== 'undefined' && L[LANG]) ? LANG : 'ru'; } catch(e){ return 'ru'; } };
  const t = k => (L[lang()] || L.ru)[k] || L.ru[k] || k;
  const h = s => (typeof esc === 'function') ? esc(s) : String(s ?? '').replace(/[&<>"']/g, c => '&#' + c.charCodeAt(0) + ';');
  const say = m => { if(typeof toast === 'function') toast(m); };
  async function call(url, opt){
    const r = await fetch(url, opt);
    let d = null; try { d = await r.json(); } catch(e){}
    if(!r.ok) throw new Error((d && d.detail) || r.status);
    return d;
  }
  const again = (...names) => names.forEach(n => { try { if(typeof window[n] === 'function') window[n](); } catch(e){} });
  let ST = null;

  // ---------------------------------------------------------------- индикатор в шапке
  function renderChip(){
    let chip = document.getElementById('netChip');
    if(!chip){
      const head = document.querySelector('header');
      if(!head) return;
      chip = document.createElement('span');
      chip.id = 'netChip'; chip.className = 'mode';
      head.insertBefore(chip, head.querySelector('.lang'));
    }
    if(!ST){ chip.classList.add('hidden'); return; }
    chip.classList.remove('hidden');
    chip.textContent = t(ST.offline ? 'on' : 'off');
    chip.title = ST.offline ? t('allowed') + ': ' + (ST.allow_hosts || []).join(', ') : '';
    chip.style.background = ST.offline ? '#0a7a30' : '#8a6100';
    chip.style.borderColor = '#ffffff88';
    // в закрытом контуре кнопки «Скачать курс …» не нужны
    document.querySelectorAll('button[onclick^="refreshRates("]').forEach(b => {
      b.disabled = !!ST.offline; b.title = ST.offline ? t('rf_net_off') : '';
    });
  }

  // ---------------------------------------------------------------- курсы из файла
  function renderRatesBlock(){
    const card = document.getElementById('c-rates');
    if(!card) return;
    let box = document.getElementById('offRates');
    if(!box){
      box = document.createElement('div');
      box.id = 'offRates'; box.className = 'note'; box.style.marginTop = '12px';
      card.appendChild(box);
    }
    box.innerHTML = `<b>${h(t('rf_title'))}</b> <span class="muted">${h(t('rf_hint'))}</span>
      <div class="row" style="margin-top:8px">
        <input type="file" id="offRateFiles" multiple accept=".xml,.xlsx,.xls,.csv,.txt,.zip">
        <input class="inp" id="offRateCcy" placeholder="${h(t('rf_ccy'))}" style="width:190px">
        <button class="btn sm" id="offRateBtn">${h(t('rf_load'))}</button>
        <a class="btn ghost sm" href="/api/rates/template">${h(t('rf_tpl'))}</a>
      </div><div id="offRateRes" class="muted" style="margin-top:6px"></div>`;
    document.getElementById('offRateBtn').onclick = importRates;
  }

  async function importRates(){
    const inp = document.getElementById('offRateFiles');
    if(!inp.files.length){ say(t('rf_none')); return; }
    const fd = new FormData();
    for(const f of inp.files) fd.append('files', f);
    const ccy = document.getElementById('offRateCcy').value.trim();
    const res = document.getElementById('offRateRes');
    try {
      const d = await call('/api/rates/import' + (ccy ? '?currency=' + encodeURIComponent(ccy) : ''), {method:'POST', body: fd});
      res.innerHTML = (d.results || []).map(r => r.ok
        ? `✓ ${h(r.file)}: ${h(r.format_title)} — ` + (r.loaded || []).map(x => `${h(x.title)} ${h(x.currencies.join(', '))} (${h(x.from)} … ${h(x.to)})`).join('; ')
          + (r.manual ? `; +${r.manual}` : '') + ((r.warnings || []).length ? ` <span title="${h(r.warnings.join('\n'))}">⚠ ${r.warnings.length}</span>` : '')
        : `✗ ${h(r.file)}: ${h(r.error)}`).join('<br>');
      say(`${d.ok} / ${d.processed}`);
      inp.value = '';
      again('renderRates', 'renderManual', 'renderJournal');
    } catch(e){ res.textContent = '✗ ' + e.message; say(e.message); }
  }

  // ---------------------------------------------------------------- обновление с носителя
  function renderUpdateCard(){
    let card = document.getElementById('c-offline');
    if(!card){
      card = document.createElement('div');
      card.className = 'card'; card.id = 'c-offline';
      const wrap = document.querySelector('.wrap');
      if(!wrap) return;
      wrap.insertBefore(card, document.getElementById('c-journal'));
      const nav = document.querySelector('nav.toc');
      if(nav && !nav.querySelector('a[href="#c-offline"]')){
        const a = document.createElement('a'); a.href = '#c-offline'; a.id = 'navOffline'; nav.appendChild(a);
      }
    }
    const navA = document.getElementById('navOffline'); if(navA) navA.textContent = t('nav');
    const sig = ST && ST.update ? ST.update : {};
    card.innerHTML = `<h2>${h(t('up_title'))}</h2>
      <p class="muted" style="margin-bottom:10px">${h(t('up_hint'))}</p>
      <div class="row">
        <input type="file" id="offUpFile" accept=".zip">
        <button class="btn ghost sm" id="offUpCheck">${h(t('up_check'))}</button>
        <button class="btn sm" id="offUpApply">${h(t('up_apply'))}</button>
        <span class="muted">${sig.require_signature ? h(t('sig_req')) : ''}</span>
      </div>
      <div id="offUpRes" style="margin-top:10px"></div>
      <h3>${h(t('up_hist'))}</h3><div id="offUpHist" class="muted"></div>
      <p class="muted" style="margin-top:8px">${ST ? h(t('allowed')) + ': ' + h((ST.allow_hosts || []).join(', ')) : ''}</p>`;
    document.getElementById('offUpCheck').onclick = () => sendUpdate(false);
    document.getElementById('offUpApply').onclick = () => sendUpdate(true);
    renderHistory();
  }

  function sigText(s){
    if(!s) return '';
    return s.valid ? `${t('sig_ok')} (${s.key})` : t('sig_no');
  }

  async function sendUpdate(apply){
    const inp = document.getElementById('offUpFile');
    if(!inp.files.length){ say(t('up_none')); return; }
    if(apply && !confirm(t('confirm'))) return;
    const fd = new FormData(); fd.append('file', inp.files[0]);
    const box = document.getElementById('offUpRes');
    try {
      const r = await call('/api/offline/update?apply=' + (apply ? 'true' : 'false'), {method:'POST', body: fd});
      const head = r.applied ? t('up_done') : (r.ok ? t('up_ok') : t('up_bad'));
      box.innerHTML = `<div class="note"><b>${r.ok ? '✓' : '✗'} ${h(head)}</b> · ${h(r.archive)} · ${h(sigText(r.signature))}`
        + (r.description ? `<br><span class="muted">${h(r.description)}</span>` : '')
        + ((r.errors || []).length ? '<br>' + r.errors.map(h).join('<br>') : '') + '</div>'
        + `<div class="tbl-wrap" style="max-height:240px;margin-top:6px"><table><tr><th>${h(t('col_file'))}</th><th>${h(t('col_act'))}</th><th>${h(t('col_ok'))}</th></tr>`
        + (r.files || []).map(f => `<tr><td>${h(f.path)}</td><td>${h(f.action)}</td><td>${f.ok ? '✓' : '✗ ' + h(f.error || '')}</td></tr>`).join('')
        + '</table></div>';
      if(apply){ inp.value = ''; renderHistory(); again('refreshAll'); }
    } catch(e){ box.innerHTML = `<div class="note">✗ ${h(e.message)}</div>`; say(e.message); renderHistory(); }
  }

  async function renderHistory(){
    const el = document.getElementById('offUpHist');
    if(!el) return;
    try {
      const rows = await call('/api/offline/updates?limit=5');
      el.innerHTML = rows.length ? rows.map(r => `${h(r.ts)} · ${h(r.user)} · ${h(r.detail)}`).join('<br>') : h(t('empty'));
    } catch(e){ el.textContent = ''; }
  }

  // ---------------------------------------------------------------- запуск
  async function renderOffline(){
    try { ST = await call('/api/offline'); } catch(e){ ST = null; }
    renderChip(); renderRatesBlock(); renderUpdateCard();
  }
  window.renderOffline = renderOffline;
  if(typeof window.setLang === 'function'){
    const orig = window.setLang;
    window.setLang = function(l){ const r = orig.apply(this, arguments); renderOffline(); return r; };
  }
  if(typeof window.refreshAll === 'function'){
    const origAll = window.refreshAll;
    window.refreshAll = async function(){ const r = await origAll.apply(this, arguments); renderChip(); return r; };
  }
  if(document.readyState === 'loading') document.addEventListener('DOMContentLoaded', renderOffline);
  else renderOffline();
})();
