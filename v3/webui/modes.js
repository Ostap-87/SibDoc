// SibDoc3: два режима работы — «Ретро данные» (пост-контроль прошедших отгрузок) и «Реальное время»
// (проверка ближайших отгрузок до подачи). Решение пользователя 05.11.2026: «разделяем инструмент на два
// блока для выбора: работа с ретро данными и работа с данными в реальном времени… сразу возможность выбора,
// с каким типом документов будет идти работа, а также возможность переключения между этими двумя экранами».
//
// Первый экран — выбор режима и типов документов (запоминается для пользователя в браузере). В шапке —
// переключатель режимов: экраны только показываются / прячутся, поэтому загруженные источники, пакеты
// и результаты не теряются. Ретро-экран — прежний интерфейс пост-контроля, источники отфильтрованы по
// выбранным типам (остальные — в «Других источниках»), плюс «Загрузить пакет документов» -> «В сверку».
// Реальное время — пакет документов (перетаскивание, ZIP), прогресс по файлам, типы с правкой вручную,
// источники проверки, «Проверить отгрузку», фильтр OK / REVIEW / ISSUE, причины RU + 中文, Excel.
// Только свой сервер (/api/...), без внешних ресурсов — закрытый контур.
(function(){
  'use strict';
  // Тексты RU / EN / 中文 — JSON (проверяется тестом tests/test_ui_modes.py: одинаковые ключи во всех языках).
  const L = /*SD3-I18N*/{
  "ru": {
    "home": "⌂ Выбор режима",
    "home_title": "Первый экран: режим и типы документов",
    "seg_retro": "Ретро данные",
    "seg_rt": "Реальное время",
    "ch_title": "С какими данными работаем?",
    "ch_sub": "Выберите режим и типы документов. Переключиться между режимами можно в любой момент — в шапке; загруженные данные и пакеты не теряются.",
    "m_retro_t": "Ретро данные",
    "m_retro_s": "пост-контроль прошедших отгрузок, контрактов и деклараций",
    "m_retro_l1": "сверка ДТ РФ, 报关单, SAP / 1С и всего пакета ВЭД",
    "m_retro_l2": "проверки 1–8: цена, HS-код, условия, элементы, контракт, ДТ ↔ 报关单 ↔ SAP, пакет документов, оплаты",
    "m_retro_l3": "отчёты Excel и PDF (RU + 中文), журнал, коннекторы",
    "m_rt_t": "Реальное время",
    "m_rt_s": "проверка ближайших отгрузок до подачи",
    "m_rt_l1": "пакет документов одним действием: ZIP, PDF, Excel, Word, сканы",
    "m_rt_l2": "тип каждого документа определяется сам, правится вручную",
    "m_rt_l3": "скан ↔ черновик ↔ SAP / 1С ↔ справочники ↔ БД — список расхождений до подачи",
    "open": "Открыть →",
    "last": "последний выбор",
    "types_t": "Типы документов, с которыми идёт работа",
    "types_hint": "Невыбранные источники не пропадают — они свёрнуты в «Другие источники». Выбор запоминается для пользователя",
    "all": "Выбрать все",
    "none": "Снять все",
    "t_ru_dt": "ДТ РФ",
    "t_cn_dec": "报关单",
    "t_draft": "Черновик декларации",
    "t_system_export": "Выгрузка SAP / 1С",
    "t_invoice": "Инвойс",
    "t_packing": "Упаковочный лист",
    "t_transport": "Транспортные документы",
    "t_conformity": "Сертификаты",
    "t_contract": "Контракты",
    "t_bank": "Выписки банка",
    "t_reference": "Справочники",
    "t_other": "Прочее",
    "others": "Другие источники",
    "others_hint": "типы не выбраны на первом экране",
    "others_req": "в т. ч. обязательные",
    "bulk_t": "Загрузить пакет документов",
    "bulk_hint": "Много файлов сразу — ZIP, PDF, Excel, Word, сканы, выписки банка, XML. Тип каждого документа определяется сам (можно поправить), «Добавить в сверку» раскладывает документы по источникам ниже.",
    "drop": "Перетащите сюда файлы или ZIP — или нажмите, чтобы выбрать",
    "drop_sub": "20–30 документов за раз; PDF из нескольких документов делится по страницам; без интернета",
    "f_send": "передача",
    "f_wait": "в очереди",
    "f_work": "разбор…",
    "f_done": "готово",
    "f_docs": "док.",
    "f_err": "ошибка",
    "pk_new": "Новый пакет",
    "pk_pick": "Пакет",
    "pk_none": "— новый пакет —",
    "pk_docs": "док.",
    "pk_add": "Файлы добавятся в выбранный пакет",
    "d_doc": "Документ",
    "d_type": "Тип",
    "d_conf": "Уверенность",
    "d_status": "Статус",
    "d_num": "Номер",
    "d_fields": "Поля / строки",
    "d_open": "Открыть",
    "d_orig": "Оригинал",
    "d_pages": "стр.",
    "d_sheet": "лист",
    "d_manual": "тип задан вручную",
    "d_none": "Документов пока нет — загрузите файлы выше.",
    "st_rec": "распознан",
    "st_rev": "требует проверки",
    "st_unk": "не распознан",
    "st_err": "ошибка",
    "other_docs": "Другие документы пакета (типы не выбраны)",
    "xls_pkg": "Скачать Пакет.xlsx",
    "to_retro": "Добавить в сверку",
    "to_retro_rt": "Перенести в ретро (в сверку)",
    "to_retro_done": "Добавлено в сверку",
    "skipped": "не перенесено",
    "nothing": "нечего добавить",
    "rt_title": "Реальное время",
    "rt_sub": "Проверка ближайшей отгрузки до подачи декларации: пакет документов → распознавание → сверка со сканом, черновиком, SAP / 1С, справочниками и базой данных.",
    "rt_t1": "Пакет документов отгрузки",
    "rt_t2": "Распознанные документы",
    "rt_t3": "Источники для проверки",
    "rt_t4": "Результат проверки",
    "src_decl": "Декларации (ДТ РФ / 报关单)",
    "src_draft": "Черновик декларации",
    "src_sys": "Система (SAP / 1С)",
    "src_more": "Справочники и база данных",
    "auto": "автоматически",
    "none_src": "не использовать",
    "sys_pkg": "выгрузка из пакета",
    "sys_retro": "данные ретро-режима (Purchase, wide table)",
    "ref_l": "справочники HS / CDE и контракты",
    "db_l": "база данных (коннектор из realtime.yaml)",
    "scan_mark": "скан",
    "no_decl": "В пакете нет деклараций — загрузите скан ДТ РФ или 报关单.",
    "check": "Проверить отгрузку",
    "checking": "Проверяю…",
    "xls_check": "Скачать Проверка отгрузки.xlsx",
    "pick_decl": "Отметьте хотя бы одну декларацию",
    "r_all": "Все",
    "r_decl": "Декларация",
    "r_level": "Где",
    "r_field": "Поле",
    "r_scan": "Скан",
    "r_draft": "Черновик",
    "r_sys": "Система",
    "r_ref": "Справочник",
    "r_db": "БД",
    "r_status": "Статус",
    "r_reason": "Причина · 原因",
    "r_search": "поиск в результатах…",
    "r_none": "Строк с таким статусом нет",
    "r_empty": "Проверка по этому пакету ещё не запускалась.",
    "r_first": "показаны первые 1000 строк — полный список в Excel",
    "ds_draft": "черновик",
    "ds_sys": "система",
    "ds_db": "БД",
    "v_fields": "Поля документа",
    "v_items": "Строки",
    "v_class": "Почему такой тип",
    "v_src": "Источник",
    "v_val": "Значение",
    "v_conf": "Уверенность",
    "v_text": "Текст документа",
    "v_field": "Поле",
    "busy": "Пакет ещё обрабатывается — дождитесь окончания",
    "pkg_done": "Пакет разобран",
    "pkg_err": "Ошибка обработки пакета",
    "tl": "Типы документов",
    "tl_edit": "изменить",
    "tl_all": "все",
    "nav_bulk": "Пакет документов",
    "r_where": "Где · поле",
    "r_vals": "Значения"
  },
  "en": {
    "home": "⌂ Choose mode",
    "home_title": "First screen: mode and document types",
    "seg_retro": "Retro data",
    "seg_rt": "Real time",
    "ch_title": "Which data are we working with?",
    "ch_sub": "Choose a mode and the document types. You can switch modes at any time in the header; loaded data and packages are kept.",
    "m_retro_t": "Retro data",
    "m_retro_s": "post-control of past shipments, contracts and declarations",
    "m_retro_l1": "RU DT, CN declaration, SAP / 1C and the full shipment package",
    "m_retro_l2": "checks 1–8: price, HS code, terms, elements, contract, DT ↔ CN ↔ SAP, shipment documents, payments",
    "m_retro_l3": "Excel and PDF reports (RU + 中文), event log, connectors",
    "m_rt_t": "Real time",
    "m_rt_s": "check upcoming shipments before filing",
    "m_rt_l1": "a document package in one go: ZIP, PDF, Excel, Word, scans",
    "m_rt_l2": "document type detected automatically, editable by hand",
    "m_rt_l3": "scan ↔ draft ↔ SAP / 1C ↔ references ↔ DB — discrepancies before filing",
    "open": "Open →",
    "last": "last choice",
    "types_t": "Document types to work with",
    "types_hint": "Unselected sources do not disappear — they are folded under “Other sources”. The choice is remembered for the user",
    "all": "Select all",
    "none": "Clear",
    "t_ru_dt": "RU DT",
    "t_cn_dec": "CN declaration",
    "t_draft": "Declaration draft",
    "t_system_export": "SAP / 1C export",
    "t_invoice": "Invoice",
    "t_packing": "Packing list",
    "t_transport": "Transport documents",
    "t_conformity": "Certificates",
    "t_contract": "Contracts",
    "t_bank": "Bank statements",
    "t_reference": "References",
    "t_other": "Other",
    "others": "Other sources",
    "others_hint": "types not selected on the first screen",
    "others_req": "incl. required",
    "bulk_t": "Upload a document package",
    "bulk_hint": "Many files at once — ZIP, PDF, Excel, Word, scans, bank statements, XML. Each document type is detected automatically (editable); “Add to check” routes documents into the sources below.",
    "drop": "Drop files or a ZIP here — or click to choose",
    "drop_sub": "20–30 documents at once; a PDF with several documents is split by pages; no internet",
    "f_send": "uploading",
    "f_wait": "queued",
    "f_work": "processing…",
    "f_done": "done",
    "f_docs": "docs",
    "f_err": "error",
    "pk_new": "New package",
    "pk_pick": "Package",
    "pk_none": "— new package —",
    "pk_docs": "docs",
    "pk_add": "Files are added to the selected package",
    "d_doc": "Document",
    "d_type": "Type",
    "d_conf": "Confidence",
    "d_status": "Status",
    "d_num": "Number",
    "d_fields": "Fields / lines",
    "d_open": "Open",
    "d_orig": "Original",
    "d_pages": "p.",
    "d_sheet": "sheet",
    "d_manual": "type set by hand",
    "d_none": "No documents yet — upload files above.",
    "st_rec": "recognized",
    "st_rev": "needs review",
    "st_unk": "not recognized",
    "st_err": "error",
    "other_docs": "Other documents in the package (types not selected)",
    "xls_pkg": "Download Package.xlsx",
    "to_retro": "Add to check",
    "to_retro_rt": "Send to retro (add to check)",
    "to_retro_done": "Added to check",
    "skipped": "not added",
    "nothing": "nothing to add",
    "rt_title": "Real time",
    "rt_sub": "Check the upcoming shipment before filing: document package → recognition → comparison of the scan with the draft, SAP / 1C, references and the database.",
    "rt_t1": "Shipment document package",
    "rt_t2": "Recognized documents",
    "rt_t3": "Sources for the check",
    "rt_t4": "Check result",
    "src_decl": "Declarations (RU DT / CN)",
    "src_draft": "Declaration draft",
    "src_sys": "System (SAP / 1C)",
    "src_more": "References and database",
    "auto": "automatic",
    "none_src": "do not use",
    "sys_pkg": "export from the package",
    "sys_retro": "retro data (Purchase, wide table)",
    "ref_l": "HS / CDE references and contracts",
    "db_l": "database (connector from realtime.yaml)",
    "scan_mark": "scan",
    "no_decl": "No declarations in the package — upload a scan of the RU DT or CN declaration.",
    "check": "Check shipment",
    "checking": "Checking…",
    "xls_check": "Download Shipment check.xlsx",
    "pick_decl": "Select at least one declaration",
    "r_all": "All",
    "r_decl": "Declaration",
    "r_level": "Where",
    "r_field": "Field",
    "r_scan": "Scan",
    "r_draft": "Draft",
    "r_sys": "System",
    "r_ref": "Reference",
    "r_db": "DB",
    "r_status": "Status",
    "r_reason": "Reason · 原因",
    "r_search": "search results…",
    "r_none": "No rows with this status",
    "r_empty": "The check has not been run for this package yet.",
    "r_first": "first 1000 rows shown — the full list is in Excel",
    "ds_draft": "draft",
    "ds_sys": "system",
    "ds_db": "DB",
    "v_fields": "Document fields",
    "v_items": "Lines",
    "v_class": "Why this type",
    "v_src": "Source",
    "v_val": "Value",
    "v_conf": "Confidence",
    "v_text": "Document text",
    "v_field": "Field",
    "busy": "The package is still being processed — please wait",
    "pkg_done": "Package processed",
    "pkg_err": "Package processing error",
    "tl": "Document types",
    "tl_edit": "change",
    "tl_all": "all",
    "nav_bulk": "Document package",
    "r_where": "Where · field",
    "r_vals": "Values"
  },
  "zh": {
    "home": "⌂ 选择模式",
    "home_title": "首页：模式与单据类型",
    "seg_retro": "历史数据",
    "seg_rt": "实时",
    "ch_title": "选择要处理的数据",
    "ch_sub": "请选择模式和单据类型。可随时在页眉切换模式，已加载的数据和文件包不会丢失。",
    "m_retro_t": "历史数据",
    "m_retro_s": "已完成发货、合同和报关单的事后核查",
    "m_retro_l1": "核对俄罗斯报关单、中国报关单、SAP / 1C 及全部外贸单证",
    "m_retro_l2": "检查 1–8：价格、HS 编码、贸易条款、申报要素、合同、俄中报关单 ↔ SAP、单证包、付款",
    "m_retro_l3": "Excel 与 PDF 报告（俄文 + 中文）、日志、连接器",
    "m_rt_t": "实时",
    "m_rt_s": "申报前核查即将发货的批次",
    "m_rt_l1": "一次上传整个文件包：ZIP、PDF、Excel、Word、扫描件",
    "m_rt_l2": "自动识别单据类型，可手动修改",
    "m_rt_l3": "扫描件 ↔ 草稿 ↔ SAP / 1C ↔ 参考表 ↔ 数据库 —— 申报前列出差异",
    "open": "打开 →",
    "last": "上次选择",
    "types_t": "要处理的单据类型",
    "types_hint": "未选择的数据源不会消失，会折叠在“其他数据源”中。选择按用户保存",
    "all": "全选",
    "none": "全不选",
    "t_ru_dt": "俄罗斯报关单",
    "t_cn_dec": "中国报关单",
    "t_draft": "报关单草稿",
    "t_system_export": "SAP / 1C 导出",
    "t_invoice": "发票",
    "t_packing": "装箱单",
    "t_transport": "运输单据",
    "t_conformity": "证书",
    "t_contract": "合同",
    "t_bank": "银行流水",
    "t_reference": "参考表",
    "t_other": "其他",
    "others": "其他数据源",
    "others_hint": "首页未选择的类型",
    "others_req": "其中必需",
    "bulk_t": "上传文件包",
    "bulk_hint": "一次上传多个文件：ZIP、PDF、Excel、Word、扫描件、银行流水、XML。自动识别每份单据的类型（可修改），“加入核对”会把单据分配到下方的数据源。",
    "drop": "将文件或 ZIP 拖到这里，或点击选择",
    "drop_sub": "一次 20–30 份单据；含多份单据的 PDF 按页拆分；无需互联网",
    "f_send": "上传中",
    "f_wait": "排队",
    "f_work": "识别中…",
    "f_done": "完成",
    "f_docs": "份",
    "f_err": "错误",
    "pk_new": "新文件包",
    "pk_pick": "文件包",
    "pk_none": "— 新文件包 —",
    "pk_docs": "份",
    "pk_add": "文件将加入所选文件包",
    "d_doc": "单据",
    "d_type": "类型",
    "d_conf": "置信度",
    "d_status": "状态",
    "d_num": "编号",
    "d_fields": "字段 / 行",
    "d_open": "打开",
    "d_orig": "原件",
    "d_pages": "页",
    "d_sheet": "工作表",
    "d_manual": "手动指定类型",
    "d_none": "暂无单据 — 请在上方上传文件。",
    "st_rec": "已识别",
    "st_rev": "需复核",
    "st_unk": "未识别",
    "st_err": "错误",
    "other_docs": "文件包中的其他单据（未选择的类型）",
    "xls_pkg": "下载 文件包.xlsx",
    "to_retro": "加入核对",
    "to_retro_rt": "转入历史数据（加入核对）",
    "to_retro_done": "已加入核对",
    "skipped": "未加入",
    "nothing": "没有可加入的单据",
    "rt_title": "实时",
    "rt_sub": "申报前核查即将发货的批次：文件包 → 识别 → 扫描件与草稿、SAP / 1C、参考表和数据库比对。",
    "rt_t1": "发货单证包",
    "rt_t2": "已识别的单据",
    "rt_t3": "核查数据源",
    "rt_t4": "核查结果",
    "src_decl": "报关单（俄罗斯 / 中国）",
    "src_draft": "报关单草稿",
    "src_sys": "系统（SAP / 1C）",
    "src_more": "参考表和数据库",
    "auto": "自动",
    "none_src": "不使用",
    "sys_pkg": "文件包中的导出",
    "sys_retro": "历史数据（Purchase、宽表）",
    "ref_l": "HS / 申报要素参考表及合同",
    "db_l": "数据库（realtime.yaml 中的连接器）",
    "scan_mark": "扫描件",
    "no_decl": "文件包中没有报关单 — 请上传俄罗斯或中国报关单扫描件。",
    "check": "核查发货",
    "checking": "核查中…",
    "xls_check": "下载 发货核查.xlsx",
    "pick_decl": "请至少选择一份报关单",
    "r_all": "全部",
    "r_decl": "报关单",
    "r_level": "位置",
    "r_field": "字段",
    "r_scan": "扫描件",
    "r_draft": "草稿",
    "r_sys": "系统",
    "r_ref": "参考表",
    "r_db": "数据库",
    "r_status": "状态",
    "r_reason": "原因 · Причина",
    "r_search": "在结果中搜索…",
    "r_none": "没有此状态的行",
    "r_empty": "此文件包尚未核查。",
    "r_first": "仅显示前 1000 行 — 完整列表见 Excel",
    "ds_draft": "草稿",
    "ds_sys": "系统",
    "ds_db": "数据库",
    "v_fields": "单据字段",
    "v_items": "行",
    "v_class": "类型判断依据",
    "v_src": "来源",
    "v_val": "值",
    "v_conf": "置信度",
    "v_text": "单据文本",
    "v_field": "字段",
    "busy": "文件包仍在处理中，请稍候",
    "pkg_done": "文件包已识别",
    "pkg_err": "文件包处理错误",
    "tl": "单据类型",
    "tl_edit": "修改",
    "tl_all": "全部",
    "nav_bulk": "文件包",
    "r_where": "位置 · 字段",
    "r_vals": "数值"
  }
}/*/SD3-I18N*/;

  // типы документов первого экрана (порядок — как в решении пользователя) и слоты ретро-режима
  const TYPES = ['ru_dt', 'cn_dec', 'draft', 'system_export', 'invoice', 'packing', 'transport', 'conformity',
                 'contract', 'bank', 'reference'];
  const TYPE_SLOTS = {ru_dt: ['ru_dt'], cn_dec: ['cn_dec'], draft: ['cd_drafts'],
                      system_export: ['wide_table', 'purchase', 'sap_vs_cd'], invoice: ['invoice'], packing: ['packing'],
                      transport: ['transport'], conformity: ['conformity'], contract: ['contract_register'],
                      bank: ['bank'], reference: ['hs_reference', 'cde_reference']};
  const RETRO_TYPES = ['ru_dt', 'cn_dec', 'draft', 'system_export', 'invoice', 'packing', 'transport', 'conformity',
                       'contract', 'bank'];
  const VED = ['invoice', 'packing', 'transport', 'conformity'];
  const ST_CLASS = {'распознан': ['st-rec', 'st_rec'], 'требует проверки': ['st-rev', 'st_rev'],
                    'не распознан': ['st-unk', 'st_unk'], 'ошибка': ['st-err', 'st_err']};
  const ICONS = {
    retro: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 2.6-6.4"/><path d="M3 4v5h5"/><path d="M12 7v5l3 2"/></svg>',
    realtime: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2 4 14h7l-1 8 9-12h-7z"/></svg>'
  };

  // ---------------------------------------------------------------- общие помощники
  const lang = () => { try { return (typeof LANG !== 'undefined' && L[LANG]) ? LANG : 'ru'; } catch(e){ return 'ru'; } };
  const t = k => (L[lang()] || L.ru)[k] || L.ru[k] || k;
  const h = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const say = m => { if(typeof toast === 'function') toast(m); };
  const $ = id => document.getElementById(id);
  async function call(url, opt){
    const r = await fetch(url, opt);
    let d = null; try { d = await r.json(); } catch(e){}
    if(!r.ok) throw new Error((d && d.detail) || r.status);
    return d;
  }
  const again = (...names) => names.forEach(n => { try { if(typeof window[n] === 'function') window[n](); } catch(e){} });
  const fmtSize = n => n > 1048576 ? (n / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round(n / 1024)) + ' KB';
  const typeLabel = ty => {
    const it = (INFO.types || []).find(x => x.type === ty);
    if(!it) return t('t_' + ty);
    return lang() === 'zh' ? it.label_zh : lang() === 'en' ? it.label_en : it.label;
  };

  // ---------------------------------------------------------------- выбор пользователя (localStorage)
  let USER = 'local', INFO = {types: []}, CUR = 'choose';
  const UI = {mode: '', types: TYPES.slice(), rtPkg: '', retroPkg: ''};
  const key = () => 'sibdoc3.ui.' + USER;
  function loadUI(){
    try {
      const s = JSON.parse(localStorage.getItem(key()) || 'null');
      if(s && typeof s === 'object'){
        if(Array.isArray(s.types)) UI.types = s.types.filter(x => TYPES.includes(x));
        ['mode', 'rtPkg', 'retroPkg'].forEach(k => { if(typeof s[k] === 'string') UI[k] = s[k]; });
      }
    } catch(e){}
  }
  function saveUI(){ try { localStorage.setItem(key(), JSON.stringify(UI)); } catch(e){} }
  const chosen = ty => UI.types.includes(ty);
  function slotVisible(slot){
    for(const [ty, slots] of Object.entries(TYPE_SLOTS)) if(slots.includes(slot)) return chosen(ty);
    return true;                                   // новый слот без типа — всегда на виду
  }
  const docVisible = d => !TYPES.includes(d.type) || chosen(d.type);

  // ---------------------------------------------------------------- шапка: переключатель режимов
  function setHdr(){
    const hd = document.querySelector('header');
    if(hd) document.documentElement.style.setProperty('--hdr-h', hd.offsetHeight + 'px');
  }
  function renderHeader(){
    const home = $('modeHome');
    if(home){ home.textContent = t('home'); home.title = t('home_title'); home.classList.toggle('active', CUR === 'choose'); }
    document.querySelectorAll('#modeSeg button').forEach(b => {
      b.textContent = t(b.dataset.sd3);
      b.classList.toggle('active', b.dataset.mode === CUR);
      b.setAttribute('aria-selected', b.dataset.mode === CUR ? 'true' : 'false');
    });
    setHdr();
  }

  function show(screen, opts){
    CUR = screen;
    $('scr-choose').classList.toggle('hidden', screen !== 'choose');
    $('scr-retro').classList.toggle('hidden', screen !== 'retro');
    $('scr-rt').classList.toggle('hidden', screen !== 'realtime');
    const toc = $('tocRetro'); if(toc) toc.classList.toggle('hidden', screen !== 'retro');
    if(screen !== 'choose'){ UI.mode = screen; saveUI(); }
    try { history.replaceState(null, '', location.pathname + '?mode=' + screen); } catch(e){}
    renderHeader();
    if(screen === 'choose') renderChooser();
    if(screen === 'retro'){ applyRetroFilter(); BULK.enter(); }
    if(screen === 'realtime') RT.enter();
    renderTypesLines();
    if(!(opts && opts.keepScroll)) window.scrollTo(0, 0);
  }

  // ---------------------------------------------------------------- первый экран
  function renderChooser(){
    const box = $('scr-choose');
    const card = (m, cls) => {
      const p = m === 'retro' ? 'm_retro_' : 'm_rt_';
      return `<button class="mcard ${cls} ${UI.mode === m ? 'last' : ''}" data-go="${m}" id="go-${m}">
        <div class="mh"><span class="ico">${ICONS[m]}</span><div><div class="mt">${h(t(p + 't'))}</div><div class="ms">${h(t(p + 's'))}</div></div></div>
        <ul><li>${h(t(p + 'l1'))}</li><li>${h(t(p + 'l2'))}</li><li>${h(t(p + 'l3'))}</li></ul>
        <div class="go"><span class="btn sm">${h(t('open'))}</span>${UI.mode === m ? `<span class="lastlab">${h(t('last'))}</span>` : ''}</div>
      </button>`;
    };
    box.innerHTML = `<div class="screen-title">${h(t('ch_title'))}</div><p class="screen-sub">${h(t('ch_sub'))}</p>
      <div class="mcards">${card('retro', 'retro')}${card('realtime', 'rt')}</div>
      <div class="card" id="typePicker"><h2>${h(t('types_t'))}</h2>
        <p class="muted" style="margin-bottom:10px">${h(t('types_hint'))}${USER !== 'local' ? ' «' + h(USER) + '»' : ''}.</p>
        <div class="chips" id="typeChips">${TYPES.map(ty => `<label class="chip ${chosen(ty) ? 'on' : ''}" data-type="${ty}">
          <input type="checkbox" value="${ty}" ${chosen(ty) ? 'checked' : ''}> ${h(t('t_' + ty))}</label>`).join('')}</div>
        <div class="row" style="margin-top:12px"><button class="btn ghost sm" id="typesAll">${h(t('all'))}</button>
          <button class="btn ghost sm" id="typesNone">${h(t('none'))}</button></div>
      </div>`;
    box.querySelectorAll('[data-go]').forEach(b => b.onclick = () => show(b.dataset.go));
    box.querySelectorAll('#typeChips input').forEach(i => i.onchange = () => {
      UI.types = TYPES.filter(ty => box.querySelector(`#typeChips input[value="${ty}"]`).checked);
      saveUI();
      i.closest('.chip').classList.toggle('on', i.checked);
    });
    $('typesAll').onclick = () => { UI.types = TYPES.slice(); saveUI(); renderChooser(); };
    $('typesNone').onclick = () => { UI.types = []; saveUI(); renderChooser(); };
  }

  // строка «Типы документов: … · изменить» на экранах режимов
  function typesLine(){
    const list = UI.types.length === TYPES.length ? t('tl_all') : (UI.types.map(ty => t('t_' + ty)).join(' · ') || '—');
    return `${h(t('tl'))}: <b>${h(list)}</b> · <a href="#" data-home>${h(t('tl_edit'))}</a>`;
  }
  function renderTypesLines(){
    document.querySelectorAll('.typesline').forEach(el => {
      el.innerHTML = typesLine();
      const a = el.querySelector('[data-home]');
      if(a) a.onclick = e => { e.preventDefault(); show('choose'); };
    });
    const nb = $('navBulk'); if(nb) nb.textContent = t('nav_bulk');
  }

  // ---------------------------------------------------------------- ретро: фильтр источников по типам
  let lastSources = [];
  function afterSlots(sources){
    lastSources = sources || lastSources;
    const data = $('c-data');
    if(!data) return;
    let det = $('otherSrc');
    if(!det){
      det = document.createElement('details');
      det.id = 'otherSrc'; det.className = 'others';
      det.innerHTML = '<summary></summary><div class="slots" id="otherSlots"></div>';
      const pk = $('pkgSlots');
      pk.parentNode.insertBefore(det, pk.nextSibling);
    }
    const other = $('otherSlots');
    other.innerHTML = '';
    let n = 0;
    const req = [];
    ['slots', 'pkgSlots'].forEach(id => {
      $(id).querySelectorAll('.slot[data-slot]').forEach(el => {
        const sl = el.dataset.slot;
        if(!slotVisible(sl)){
          other.appendChild(el); n++;
          const s = lastSources.find(x => x.slot === sl);
          if(s && s.required && !s.loaded) req.push((lang() === 'zh' && s.label_zh) || (lang() === 'en' && s.label_en) || s.label);
        }
      });
    });
    det.classList.toggle('hidden', !n);
    det.querySelector('summary').textContent = `${t('others')} (${n}) — ${t('others_hint')}` + (req.length ? `; ${t('others_req')}: ${req.join(', ')}` : '');
    const pkgEmpty = !$('pkgSlots').querySelector('.slot');
    const head = data.querySelector('.pkg-head');
    if(head){ head.classList.toggle('hidden', pkgEmpty); if(head.nextElementSibling) head.nextElementSibling.classList.toggle('hidden', pkgEmpty); }
    $('pkgSlots').classList.toggle('hidden', pkgEmpty);
    $('slots').classList.toggle('hidden', !$('slots').querySelector('.slot'));
  }

  function applyRetroFilter(){
    // источники: перерисовать (renderSlots зовёт afterSlots)
    again('renderSlots');
    // декларации ДТ РФ / 报关单
    const ru = chosen('ru_dt'), cn = chosen('cn_dec');
    const decl = $('c-decl');
    if(decl){
      decl.classList.toggle('hidden', !ru && !cn);
      const tr = $('dt_ru'), tc = $('dt_cn');
      if(tr) tr.classList.toggle('hidden', !ru);
      if(tc) tc.classList.toggle('hidden', !cn);
      try { if(ru !== cn && typeof declSlot !== 'undefined'){ const want = ru ? 'ru_dt' : 'cn_dec';   // eslint-disable-line no-undef
        if(declSlot !== want){ declSlot = want; again('renderDecl'); } } } catch(e){}               // eslint-disable-line no-undef
    }
    // документы пакета ВЭД
    const pkgCard = $('c-pkg');
    if(pkgCard){ pkgCard.classList.toggle('hidden', !VED.some(chosen)); if(VED.some(chosen)) again('renderPkg'); }
    // оглавление: ссылки на спрятанные карточки
    document.querySelectorAll('#tocRetro a').forEach(a => {
      const el = document.querySelector(a.getAttribute('href'));
      a.classList.toggle('hidden', !!(el && el.classList.contains('hidden')));
    });
  }

  // ---------------------------------------------------------------- пакет документов (общий для двух экранов)
  function makePack(cfg){
    // cfg: mode ('retro' | 'realtime'), uiKey, ids: {top, files, docs, tools}, retroBtn, onDocs
    const S = {pid: '', meta: null, docs: [], batch: null, timer: null, picked: new Set(), compact: false};

    function el(id){ return $(cfg.ids[id]); }

    async function packagesOptions(){
      let list = [];
      try { list = await call('/api/intake/packages'); } catch(e){}
      list = list.filter(p => p.mode === cfg.mode);
      const sel = el('top').querySelector('select');
      if(!sel) return;
      sel.innerHTML = `<option value="">${h(t('pk_none'))}</option>` + list.map(p =>
        `<option value="${h(p.id)}" ${p.id === S.pid ? 'selected' : ''}>${h(p.title || p.id)} · ${p.documents ?? (p.counts && p.counts.total) ?? ''} ${h(t('pk_docs'))}</option>`).join('');
    }

    function renderTop(){
      const top = el('top');
      top.innerHTML = `<div class="row" style="margin-bottom:10px">
          <label class="muted">${h(t('pk_pick'))}</label><select class="inp" style="min-width:240px"></select>
          <button class="btn ghost sm" data-act="new">${h(t('pk_new'))}</button>
          <span class="muted" data-role="pkinfo"></span></div>
        <div class="drop" tabindex="0" role="button"><b>${h(t('drop'))}</b><div class="muted">${h(t('drop_sub'))}</div>
          <input type="file" multiple style="display:none"></div>`;
      const drop = top.querySelector('.drop'), inp = drop.querySelector('input');
      drop.onclick = () => inp.click();
      drop.onkeydown = e => { if(e.key === 'Enter' || e.key === ' '){ e.preventDefault(); inp.click(); } };
      inp.onchange = () => { upload(inp.files); inp.value = ''; };
      ['dragenter', 'dragover'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add('over'); }));
      ['dragleave', 'drop'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove('over'); }));
      drop.addEventListener('drop', e => { if(e.dataTransfer && e.dataTransfer.files.length) upload(e.dataTransfer.files); });
      top.querySelector('[data-act="new"]').onclick = () => { setPid(''); render(); };
      top.querySelector('select').onchange = ev => { setPid(ev.target.value); load(); };
      packagesOptions();
      info();
    }

    function info(){
      const i = el('top').querySelector('[data-role="pkinfo"]');
      if(i) i.textContent = S.pid ? t('pk_add') : '';
    }

    function setPid(pid){
      S.pid = pid || ''; S.meta = null; S.docs = []; S.batch = null; S.picked = new Set();
      UI[cfg.uiKey] = S.pid; saveUI();
      if(cfg.onPid) cfg.onPid(S.pid);
    }

    function xhrUpload(url, files, extra, onBytes){
      return new Promise((resolve, reject) => {
        const x = new XMLHttpRequest();
        x.open('POST', url);
        x.upload.onprogress = e => { if(e.lengthComputable) onBytes(e.loaded / e.total); };
        x.onload = () => { let d = null; try { d = JSON.parse(x.responseText); } catch(e){}
          if(x.status >= 200 && x.status < 300) resolve(d); else reject(new Error((d && d.detail) || x.status)); };
        x.onerror = () => reject(new Error('network'));
        const fd = new FormData();
        for(const f of files) fd.append('files', f, f.name);
        for(const [k, v] of Object.entries(extra || {})) fd.append(k, v);
        x.send(fd);
      });
    }

    async function upload(fileList){
      const files = Array.from(fileList || []);
      if(!files.length) return;
      if(S.meta && S.meta.status === 'processing'){ say(t('busy')); return; }
      const start = (S.meta && S.meta.files) ? S.meta.files.length : 0;
      S.batch = {start, files: files.map(f => ({name: f.name, size: f.size, frac: 0, state: 'send'}))};
      renderFiles();
      const total = files.reduce((a, f) => a + f.size, 0) || 1;
      const url = S.pid ? `/api/intake/packages/${encodeURIComponent(S.pid)}/files` : '/api/intake/packages';
      const extra = S.pid ? {} : {mode: cfg.mode, title: files[0].name + (files.length > 1 ? ` (+${files.length - 1})` : '')};
      try {
        const meta = await xhrUpload(url, files, extra, frac => {
          let acc = 0; const sent = frac * total;
          for(const f of S.batch.files){ f.frac = Math.max(0, Math.min(1, (sent - acc) / (f.size || 1))); acc += f.size; }
          renderFiles();
        });
        S.batch.files.forEach(f => { f.frac = 1; f.state = 'wait'; });
        if(!S.pid){ S.pid = meta.id; UI[cfg.uiKey] = S.pid; saveUI(); if(cfg.onPid) cfg.onPid(S.pid); }
        S.meta = meta;
        renderFiles(); info(); packagesOptions(); poll();
      } catch(e){
        S.batch.files.forEach(f => { f.state = 'err'; f.err = e.message; });
        renderFiles(); say('⚠ ' + e.message);
      }
    }

    async function poll(){
      clearTimeout(S.timer);
      if(!S.pid) return;
      try { S.meta = await call(`/api/intake/packages/${encodeURIComponent(S.pid)}`); }
      catch(e){ if(String(e.message).includes('404') || /не найден|not found/i.test(e.message)){ setPid(''); render(); } return; }
      S.docs = S.meta.documents || [];
      const p = S.meta.progress || {};
      if(S.batch){
        S.batch.files.forEach((f, k) => {
          f.state = S.meta.status === 'error' ? 'err' : (S.meta.status === 'done' || k < (p.done || 0)) ? 'done'
            : (k === (p.done || 0) ? 'work' : 'wait');
          if(f.state === 'work') f.current = p.current || '';
        });
      }
      renderFiles(); renderDocs(); info();
      if(S.meta.status === 'processing'){ S.timer = setTimeout(poll, 900); return; }
      if(S.batch){ say(S.meta.status === 'error' ? t('pkg_err') + ': ' + (S.meta.message || '') : t('pkg_done') + ': ' + S.docs.length + ' ' + t('pk_docs')); S.batch = null; }
      packagesOptions();
      if(cfg.onDocs) cfg.onDocs(S);
    }

    async function load(){
      if(!S.pid){ render(); return; }
      await poll();
      if(cfg.onLoad) cfg.onLoad(S);
    }

    function countDocs(name){
      return S.docs.filter(d => d.file === name || String(d.file || '').startsWith(name + '/')).length;
    }

    function renderFiles(){
      const box = el('files');
      if(!box) return;
      const rows = [];
      const known = (S.meta && S.meta.files) || [];
      known.forEach((f, i) => { if(S.batch && i >= S.batch.start) return;
        rows.push({name: f.name, size: f.size, state: 'done', frac: 1}); });
      if(S.batch) S.batch.files.forEach(f => rows.push(f));
      if(!rows.length){ box.innerHTML = ''; return; }
      const stateText = f => {
        if(f.state === 'send') return `${t('f_send')} ${Math.round(f.frac * 100)} %`;
        if(f.state === 'wait') return t('f_wait');
        if(f.state === 'work') return t('f_work') + (f.current && f.current !== f.name ? ' ' + String(f.current).split('/').pop() : '');
        if(f.state === 'err') return t('f_err') + (f.err ? ': ' + f.err : '');
        const n = countDocs(f.name);
        return `${t('f_done')}: ${n} ${t('f_docs')}`;
      };
      const barCls = f => f.state === 'done' ? 'done' : f.state === 'err' ? 'err' : f.state === 'work' ? 'work' : '';
      const barW = f => f.state === 'send' ? f.frac * 100 : 100;
      box.innerHTML = `<div class="flist" style="max-height:260px;overflow:auto">` + rows.map(f => `<div class="fitem">
          <span class="nm" title="${h(f.name)}">${h(f.name)} <span class="muted">${f.size ? fmtSize(f.size) : ''}</span></span>
          <span class="bar ${barCls(f)}"><i style="width:${barW(f)}%"></i></span>
          <span class="fs" title="${h(stateText(f))}">${h(stateText(f))}</span></div>`).join('') + '</div>';
    }

    function statusBadge(d){
      const sc = ST_CLASS[d.status] || ['st-unk', ''];
      return `<span class="st ${sc[0]}" title="${h(d.status_reason || '')}">${h(sc[1] ? t(sc[1]) : d.status || '')}</span>`;
    }
    function confCell(c){
      if(c === null || c === undefined) return '—';
      const v = Math.round(c * 100);
      return `<span class="conf ${v >= 80 ? 'conf-h' : v >= 60 ? 'conf-m' : 'conf-l'}">${v} %</span>`;
    }
    function typeSelect(d){
      const opts = (INFO.types || []).map(x => `<option value="${h(x.type)}" ${x.type === d.type ? 'selected' : ''}>${h(typeLabel(x.type))}</option>`).join('');
      return `<select data-doc="${h(d.id)}" title="${d.type_source === 'manual' ? h(t('d_manual')) : ''}">${opts}</select>${d.type_source === 'manual' ? ' ✎' : ''}`;
    }
    function docRow(d, n){
      const dirs = String(d.file || '').split('/').slice(0, -1);
      const dir = dirs.length ? (dirs.length > 1 ? '… / ' : '') + dirs[dirs.length - 1] : '';   // папка в архиве; путь — в подсказке
      const where = [dir,
                     d.pages ? `${t('d_pages')} ${d.pages[0]}${d.pages[1] !== d.pages[0] ? '–' + d.pages[1] : ''}` : '',
                     (d.sheets || []).length ? `${t('d_sheet')} «${d.sheets.join(', ')}»` : '',
                     d.split && d.split.of ? `${d.split.index}/${d.split.of}` : ''].filter(Boolean).join(' · ');
      const pick = cfg.retroBtn ? `<td><input type="checkbox" data-pick="${h(d.id)}" ${RETRO_TYPES.includes(d.type) ? '' : 'disabled'}
        ${RETRO_TYPES.includes(d.type) && !S.picked.has('-' + d.id) ? 'checked' : ''}></td>` : '';
      const reason = d.status_reason ? `<span class="sub">${h(d.status_reason)}</span>` : '';
      if(S.compact)                                 // планшет: номер — под именем, уверенность — рядом со статусом
        return `<tr>${pick}<td class="nm" title="${h(d.file)}">${h(d.name || d.file)}<span class="sub">${h(d.id)}${d.doc_number ? ' · ' + h(d.doc_number) : ''}${where ? ' · ' + h(where) : ''}</span></td>
          <td>${typeSelect(d)}</td><td>${statusBadge(d)} ${confCell(d.confidence)}${reason}</td>
          <td style="white-space:nowrap">${d.fields} / ${d.items}</td>
          <td style="white-space:nowrap"><button class="btn ghost sm" data-open="${h(d.id)}">${h(t('d_open'))}</button></td></tr>`;
      return `<tr>${pick}<td class="muted">${h(d.id)}</td>
        <td class="nm" title="${h(d.file)}">${h(d.name || d.file)}<span class="sub">${h(where)}</span></td>
        <td>${typeSelect(d)}</td><td>${confCell(d.confidence)}</td>
        <td>${statusBadge(d)}${reason}</td>
        <td>${h(d.doc_number || '')}</td><td style="white-space:nowrap">${d.fields} / ${d.items}${d.review_fields ? ` <span class="muted">(⚠ ${d.review_fields})</span>` : ''}</td>
        <td style="white-space:nowrap"><button class="btn ghost sm" data-open="${h(d.id)}">${h(t('d_open'))}</button></td></tr>`;
    }
    function table(list){
      const pickTh = cfg.retroBtn ? '<th></th>' : '';
      if(S.compact)
        return `<div class="tbl-wrap" style="max-height:640px"><table class="dtbl"><tr>${pickTh}<th>${h(t('d_doc'))}</th><th>${h(t('d_type'))}</th>
          <th>${h(t('d_status'))} · ${h(t('d_conf'))}</th><th>${h(t('d_fields'))}</th><th></th></tr>${list.map(docRow).join('')}</table></div>`;
      return `<div class="tbl-wrap" style="max-height:640px"><table class="dtbl"><tr>${pickTh}<th>№</th><th>${h(t('d_doc'))}</th><th>${h(t('d_type'))}</th>
        <th>${h(t('d_conf'))}</th><th>${h(t('d_status'))}</th><th>${h(t('d_num'))}</th><th>${h(t('d_fields'))}</th><th></th></tr>
        ${list.map(docRow).join('')}</table></div>`;
    }

    function renderDocs(){
      const box = el('docs');
      if(!box) return;
      const tools = el('tools');
      if(!S.docs.length){
        box.innerHTML = `<p class="muted">${h(t('d_none'))}</p>`;
        if(tools) tools.innerHTML = '';
        return;
      }
      S.compact = window.innerWidth < 1000;
      const vis = S.docs.filter(docVisible), oth = S.docs.filter(d => !docVisible(d));
      const counts = {};
      S.docs.forEach(d => { counts[d.status] = (counts[d.status] || 0) + 1; });
      if(tools){
        const done = S.meta && S.meta.status === 'done';
        tools.innerHTML = `<div class="row" style="margin-bottom:10px">
          ${Object.entries(ST_CLASS).filter(([k]) => counts[k]).map(([k, v]) => `<span class="st ${v[0]}">${h(t(v[1]))}: ${counts[k]}</span>`).join(' ')}
          <span class="spacer" style="flex:1"></span>
          ${cfg.retroBtn ? `<button class="btn sm" data-act="retro" ${done ? '' : 'disabled'}>${h(t('to_retro'))}</button>` : ''}
          ${cfg.rtRetroBtn ? `<button class="btn ghost sm" data-act="retro" ${done ? '' : 'disabled'}>${h(t('to_retro_rt'))}</button>` : ''}
          <a class="btn ghost sm ${done ? '' : 'hidden'}" href="/api/intake/packages/${encodeURIComponent(S.pid)}/excel">${h(t('xls_pkg'))}</a>
        </div><div class="note hidden" data-role="retroRes" style="margin-bottom:10px"></div>`;
        tools.querySelectorAll('[data-act="retro"]').forEach(b => b.onclick = toRetro);
      }
      box.innerHTML = (vis.length ? table(vis) : '') +
        (oth.length ? `<details class="others"><summary>${h(t('other_docs'))} (${oth.length})</summary>${table(oth)}</details>` : '');
      box.querySelectorAll('select[data-doc]').forEach(s => s.onchange = () => setType(s.dataset.doc, s.value));
      box.querySelectorAll('[data-open]').forEach(b => b.onclick = () => openDoc(b.dataset.open));
      box.querySelectorAll('[data-pick]').forEach(c => c.onchange = () => {
        if(c.checked) S.picked.delete('-' + c.dataset.pick); else S.picked.add('-' + c.dataset.pick); });
    }

    async function setType(docId, ty){
      try {
        await call(`/api/intake/packages/${encodeURIComponent(S.pid)}/documents/${encodeURIComponent(docId)}/type`,
                   {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({type: ty})});
        S.docs = await call(`/api/intake/packages/${encodeURIComponent(S.pid)}/documents`);
        if(S.meta) S.meta.documents = S.docs;
        renderDocs();
        if(cfg.onDocs) cfg.onDocs(S);
        say('✓ ' + typeLabel(ty));
      } catch(e){ say('⚠ ' + e.message); }
    }

    async function toRetro(){
      const ids = S.docs.filter(d => RETRO_TYPES.includes(d.type) && d.status !== 'не распознан' && d.status !== 'ошибка'
                                     && !S.picked.has('-' + d.id)).map(d => d.id);
      const res = el('tools').querySelector('[data-role="retroRes"]');
      if(!ids.length){ say(t('nothing')); return; }
      try {
        const r = await call(`/api/intake/packages/${encodeURIComponent(S.pid)}/to-retro`,
                             {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({doc_ids: ids})});
        const slotName = sl => { const s = lastSources.find(x => x.slot === sl);
          return s ? ((lang() === 'zh' && s.label_zh) || (lang() === 'en' && s.label_en) || s.label).split(' (')[0] : sl; };
        const added = Object.entries(r.added || {});
        res.innerHTML = `<b>${h(t('to_retro_done'))}:</b> ` + (added.length ? added.map(([sl, v]) =>
            `${h(slotName(sl))} — ${v.documents.length} ${h(t('pk_docs'))}`).join('; ') : h(t('nothing')))
          + ((r.skipped || []).length ? `<br><span class="muted">${h(t('skipped'))}: ` + r.skipped.map(s => `${h(s.doc_id)} — ${h(s.reason)}`).join('; ') + '</span>' : '');
        res.classList.remove('hidden');
        say(t('to_retro_done') + ': ' + added.length);
        try { pkgCache = {}; declCache = {}; } catch(e){}               // eslint-disable-line no-undef
        again('renderSlots', 'renderDecl', 'renderPkg', 'renderJournal');
      } catch(e){ say('⚠ ' + e.message); }
    }

    async function openDoc(docId){
      let d;
      try { d = await call(`/api/intake/packages/${encodeURIComponent(S.pid)}/documents/${encodeURIComponent(docId)}`); }
      catch(e){ say('⚠ ' + e.message); return; }
      const lab = f => { try { const c = (typeof LABELS !== 'undefined' && LABELS.columns) ? LABELS.columns[f] : null;   // eslint-disable-line no-undef
        return c ? (lang() === 'zh' ? c.zh : c.ru) : f; } catch(e){ return f; } };
      const src = s => s ? [s.page ? `${t('d_pages')} ${s.page}` : '', s.sheet ? `${t('d_sheet')} «${s.sheet}»` : '',
                            s.row ? 'r' + s.row : '', s.col || '', s.line ? 'l' + s.line : '', s.method || ''].filter(Boolean).join(' · ') : '';
      const fields = Object.entries(d.fields || {});
      const items = d.items || [];
      const icols = [...new Set(items.flatMap(it => Object.keys(it.fields || {})))];
      const html = `<p class="muted">${h(d.file)} · ${h(typeLabel(d.type))} · ${h(d.extractor || '')}</p>
        <div class="row" style="margin:8px 0"><a class="btn ghost sm" href="${h(d.original_url)}">${h(t('d_orig'))}</a>
          ${(d.pages_info || []).filter(p => p.image_url).slice(0, 6).map(p => `<a class="btn ghost sm" target="_blank" href="${h(p.image_url)}">${h(p.label || p.index)}</a>`).join('')}</div>
        <h3>${h(t('v_fields'))}</h3>
        <div class="tbl-wrap" style="max-height:300px"><table><tr><th>${h(t('v_field'))}</th><th>${h(t('v_val'))}</th><th>${h(t('v_conf'))}</th><th>${h(t('v_src'))}</th></tr>
          ${fields.map(([f, v]) => `<tr><td>${h(lab(f))} <span class="muted">${h(f)}</span></td><td>${h(v.value)}</td><td>${confCell(v.conf)}</td><td class="muted">${h(src(v.source))}${v.how ? '<br>' + h(v.how) : ''}</td></tr>`).join('')}</table></div>
        ${items.length ? `<h3>${h(t('v_items'))} (${items.length})</h3><div class="tbl-wrap" style="max-height:300px"><table><tr><th>№</th>${icols.map(c => `<th>${h(lab(c))}</th>`).join('')}</tr>
          ${items.slice(0, 300).map(it => `<tr><td>${h(it.no)}</td>${icols.map(c => `<td>${h(((it.fields || {})[c] || {}).value)}</td>`).join('')}</tr>`).join('')}</table></div>` : ''}
        <h3>${h(t('v_class'))}</h3><p class="muted">${h(((d.classification || {}).reasons || []).join('; '))}</p>
        <h3>${h(t('v_text'))}</h3><pre style="white-space:pre-wrap;font-size:11.5px;max-height:240px;overflow:auto;background:#f6f8fb;padding:8px;border-radius:6px">${h((d.text || '').slice(0, 6000))}</pre>`;
      if(typeof openModal === 'function') openModal(`${d.id} · ${d.name || d.file}`, html);    // eslint-disable-line no-undef
    }

    function render(){ renderTop(); renderFiles(); renderDocs(); if(cfg.onDocs) cfg.onDocs(S); }
    let rz = null;
    window.addEventListener('resize', () => { clearTimeout(rz); rz = setTimeout(() => {
      if(S.docs.length && S.compact !== (window.innerWidth < 1000)) renderDocs(); }, 200); });

    return {
      S, render, load, packagesOptions,
      enter(){
        if(!el('top')) return;
        if(!el('top').innerHTML) renderTop();
        if(!S.pid && UI[cfg.uiKey]){ S.pid = UI[cfg.uiKey]; load(); }
        else { packagesOptions(); renderFiles(); renderDocs(); }
      },
      relabel(){ renderTop(); renderFiles(); renderDocs(); }
    };
  }

  // ---------------------------------------------------------------- ретро: «Загрузить пакет документов»
  const BULK = (() => {
    let pack = null;
    function build(){
      const card = $('c-bulk');
      if(!card) return null;
      card.innerHTML = `<h2><span class="step">⇪</span> <span>${h(t('bulk_t'))}</span></h2>
        <p class="muted" style="margin-bottom:6px">${h(t('bulk_hint'))}</p><p class="typesline muted" style="margin-bottom:10px"></p>
        <div id="bk-top"></div><div id="bk-files"></div><div id="bk-tools" style="margin-top:10px"></div><div id="bk-docs"></div>`;
      return makePack({mode: 'retro', uiKey: 'retroPkg', retroBtn: true,
                       ids: {top: 'bk-top', files: 'bk-files', docs: 'bk-docs', tools: 'bk-tools'}});
    }
    return {
      enter(){ if(!pack) pack = build(); if(pack) pack.enter(); },
      relabel(){
        if(!pack) return;
        const c = $('c-bulk');
        c.querySelector('h2 span:last-child').textContent = t('bulk_t');
        c.querySelector('p.muted').textContent = t('bulk_hint');
        renderTypesLines();
        pack.relabel();
      }
    };
  })();

  // ---------------------------------------------------------------- реальное время
  const RT = (() => {
    let pack = null, res = null, filter = '', q = '', compact = false, src = {decls: null, draft: 'auto', system: 'auto', references: true, database: false};
    function build(){
      const box = $('scr-rt');
      box.innerHTML = `<div class="screen-title" data-k="rt_title"></div><p class="screen-sub" style="margin-bottom:4px" data-k="rt_sub"></p>
        <p class="typesline muted" style="margin-bottom:14px"></p>
        <div class="card" id="rt-pack"><h2><span class="step">1</span> <span data-k="rt_t1"></span></h2>
          <div id="rt-top"></div><div id="rt-files"></div></div>
        <div class="card" id="rt-docs"><h2><span class="step">2</span> <span data-k="rt_t2"></span></h2>
          <div id="rt-tools"></div><div id="rt-list"></div></div>
        <div class="card" id="rt-src"><h2><span class="step">3</span> <span data-k="rt_t3"></span></h2><div id="rt-src-body"></div></div>
        <div class="card" id="rt-res"><h2><span class="step">4</span> <span data-k="rt_t4"></span></h2><div id="rt-res-body"></div></div>`;
      labels();
      return makePack({mode: 'realtime', uiKey: 'rtPkg', rtRetroBtn: true,
                       ids: {top: 'rt-top', files: 'rt-files', docs: 'rt-list', tools: 'rt-tools'},
                       onPid: () => { res = null; src.decls = null; renderRes(); },
                       onDocs: () => renderSrc(), onLoad: () => loadResult()});
    }
    function labels(){ document.querySelectorAll('#scr-rt [data-k]').forEach(e => { e.textContent = t(e.dataset.k); }); renderTypesLines(); }

    function docs(){ return pack ? pack.S.docs : []; }
    const isScan = d => ['pdf', 'image'].includes(d.format);

    function renderSrc(){
      const body = $('rt-src-body');
      if(!body) return;
      const ds = docs();
      const decls = ds.filter(d => d.type === 'ru_dt' || d.type === 'cn_dec');
      const drafts = ds.filter(d => d.type === 'draft'), sys = ds.filter(d => d.type === 'system_export');
      if(src.decls === null || src.decls.some(id => !decls.find(d => d.id === id))){
        const scans = decls.filter(isScan);
        src.decls = (scans.length ? scans : decls).map(d => d.id);
      }
      const done = pack && pack.S.meta && pack.S.meta.status === 'done';
      const opt = (v, lab, cur) => `<option value="${h(v)}" ${String(cur) === String(v) ? 'selected' : ''}>${h(lab)}</option>`;
      const docLab = d => `${d.id} · ${d.doc_number || d.name || d.file}`;
      body.innerHTML = !ds.length ? `<p class="muted">${h(t('d_none'))}</p>` : `<div class="srcgrid">
        <div class="srcbox"><h4>${h(t('src_decl'))}</h4>${decls.length ? decls.map(d => `<label><input type="checkbox" data-decl="${h(d.id)}" ${src.decls.includes(d.id) ? 'checked' : ''}>
            <span>${h(typeLabel(d.type))} ${h(d.doc_number || d.name)}${isScan(d) ? ` <span class="badge b-kind">${h(t('scan_mark'))}</span>` : ''}<br><span class="muted">${h(d.name || d.file)}</span></span></label>`).join('')
          : `<p class="muted">${h(t('no_decl'))}</p>`}</div>
        <div class="srcbox"><h4>${h(t('src_draft'))}</h4><select id="rt-draft">${opt('auto', t('auto'), src.draft)}${opt('none', t('none_src'), src.draft)}
          ${drafts.map(d => opt(d.id, docLab(d), src.draft)).join('')}</select></div>
        <div class="srcbox"><h4>${h(t('src_sys'))}</h4><select id="rt-sys">${opt('auto', t('auto'), src.system)}${opt('package', t('sys_pkg'), src.system)}
          ${opt('retro', t('sys_retro'), src.system)}${opt('none', t('none_src'), src.system)}${sys.map(d => opt(d.id, docLab(d), src.system)).join('')}</select></div>
        <div class="srcbox"><h4>${h(t('src_more'))}</h4>
          <label><input type="checkbox" id="rt-ref" ${src.references ? 'checked' : ''}> ${h(t('ref_l'))}</label>
          <label><input type="checkbox" id="rt-db" ${src.database ? 'checked' : ''}> ${h(t('db_l'))}</label></div>
      </div>
      <div class="row" style="margin-top:12px"><button class="btn" id="rt-check" ${done && decls.length ? '' : 'disabled'}>${h(t('check'))}</button>
        <span class="muted" id="rt-check-info"></span></div>`;
      body.querySelectorAll('[data-decl]').forEach(c => c.onchange = () => {
        src.decls = [...body.querySelectorAll('[data-decl]:checked')].map(x => x.dataset.decl); });
      const sd = $('rt-draft'), ss = $('rt-sys');
      if(sd) sd.onchange = () => { src.draft = sd.value; };
      if(ss) ss.onchange = () => { src.system = ss.value; };
      const rf = $('rt-ref'), db = $('rt-db');
      if(rf) rf.onchange = () => { src.references = rf.checked; };
      if(db) db.onchange = () => { src.database = db.checked; };
      const btn = $('rt-check'); if(btn) btn.onclick = check;
    }

    async function check(){
      if(!src.decls || !src.decls.length){ say(t('pick_decl')); return; }
      const btn = $('rt-check'); btn.disabled = true; btn.textContent = t('checking');
      const sel = v => (v === 'auto' || v === 'none' || v === 'package' || v === 'retro') ? v : [v];
      const body = {declarations: src.decls, draft: sel(src.draft), system: sel(src.system),
                    references: src.references, database: src.database};
      try {
        res = await call(`/api/realtime/${encodeURIComponent(pack.S.pid)}/check`,
                         {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
        filter = '';
        renderRes();
        const c = res.counts || {};
        say(`OK ${c.OK || 0} · REVIEW ${c.REVIEW || 0} · ISSUE ${c.ISSUE || 0}`);
        $('rt-res').scrollIntoView({behavior: 'smooth', block: 'start'});
        again('renderJournal');
      } catch(e){ say('⚠ ' + e.message); }
      btn.disabled = false; btn.textContent = t('check');
    }

    async function loadResult(){
      res = null;
      if(pack && pack.S.pid){ try { res = await call(`/api/realtime/${encodeURIComponent(pack.S.pid)}/result`); } catch(e){ res = null; } }
      renderRes();
    }

    function renderRes(){
      const body = $('rt-res-body');
      if(!body) return;
      if(!res){ body.innerHTML = `<p class="muted">${h(t('r_empty'))}</p>`; return; }
      const zh = lang() === 'zh';
      const rows = (res.declarations || []).flatMap(d => (d.rows || []).map(r => Object.assign({_side: d.side}, r)));
      const c = res.counts || {};
      const btn = (f, lab, n) => `<button class="fbtn ${f ? 'f-' + f : ''} ${filter === f ? 'active' : ''}" data-f="${f}">${h(lab)} · ${n ?? 0}</button>`;
      const shown = rows.filter(r => (!filter || r.status === filter) && (!q || JSON.stringify(r).toLowerCase().includes(q)));
      // колонки: статус, где (и декларация), поле, значения источников (пустые по всем строкам — не показываются), причина
      const cols = [['status', 'r_status'], ['level', 'r_level'], ['field', 'r_field'], ['scan', 'r_scan'], ['draft', 'r_draft'],
                    ['system', 'r_sys'], ['reference', 'r_ref'], ['database', 'r_db'], ['reason', 'r_reason']]
        .filter(c => !['reference', 'database', 'draft', 'system'].includes(c[0]) || rows.some(r => r[c[0]] !== '' && r[c[0]] != null));
      const multi = (res.declarations || []).length > 1;
      // планшет (уже 1000 px): значения источников — одной колонкой, чтобы таблица помещалась без прокрутки вбок
      compact = window.innerWidth < 1000;
      const srcCols = cols.filter(c => ['scan', 'draft', 'system', 'reference', 'database'].includes(c[0]));
      if(compact) cols.splice(0, cols.length, ['status', 'r_status'], ['where', 'r_where'], ['values', 'r_vals'], ['reason', 'r_reason']);
      const cell = (r, k) => {
        if(k === 'status') return `<span class="st st-${h(r.status)}">${h(r.status)}</span>`;
        if(k === 'level') return h(r.level) + (multi ? `<span class="sub">${h(r.declaration)}</span>` : '');
        if(k === 'field') return h(zh ? (r.label_zh || r.label) : r.label) + (r.key ? ' <b title="key">•</b>' : '');
        if(k === 'scan') return h(r.scan) + (r.scan_conf !== undefined && r.scan_conf !== null && r.scan_conf < 0.75
          ? ` <span class="conf conf-m">${Math.round(r.scan_conf * 100)} %</span>` : '');
        if(k === 'where') return `${h(r.level)}<span class="sub">${cell(r, 'field')}</span>` + (multi ? `<span class="sub">${h(r.declaration)}</span>` : '');
        if(k === 'values') return srcCols.filter(c => r[c[0]] !== '' && r[c[0]] != null)
          .map(c => `<span class="sub">${h(t(c[1]))}</span>${cell(r, c[0])}`).join('');
        if(k === 'reason') return h(zh ? (r.reason_zh || r.reason) : r.reason) + `<span class="zh">${h(zh ? r.reason : r.reason_zh)}</span>`;
        return h(r[k]);
      };
      const base = v => String(v || '').split('/').pop();
      const sysLab = v => ({package: t('sys_pkg'), retro: t('sys_retro'), none: t('none_src')})[v] || v;
      const dsum = (res.declarations || []).map(d => `<div class="dcard"><b>${h(typeLabel(d.type))} ${h(d.number || '')}</b>
          <span class="muted"> · ${h(t('ds_draft'))}: ${h((d.draft && (base(d.draft.file) || d.draft.reason)) || '—')}
          · ${h(t('ds_sys'))}: ${h((d.system && (sysLab(d.system.source) || d.system.reason)) || '—')}${d.system && d.system.lines ? ' (' + d.system.lines + ')' : ''}</span>
          <br><span class="st st-ISSUE">ISSUE ${(d.counts || {}).ISSUE || 0}</span> <span class="st st-REVIEW">REVIEW ${(d.counts || {}).REVIEW || 0}</span> <span class="st st-OK">OK ${(d.counts || {}).OK || 0}</span></div>`).join('');
      body.innerHTML = `<div class="row" style="margin-bottom:8px">
          <div class="fbtns">${btn('', t('r_all'), c.total)}${btn('ISSUE', 'ISSUE', c.ISSUE)}${btn('REVIEW', 'REVIEW', c.REVIEW)}${btn('OK', 'OK', c.OK)}</div>
          <input class="jsearch" id="rt-q" value="${h(q)}" placeholder="${h(t('r_search'))}">
          <a class="btn ghost sm" href="${h(res.excel)}">${h(t('xls_check'))}</a></div>
        <div class="declsum">${dsum}</div>
        <div class="tbl-wrap" style="max-height:560px"><table class="rtres"><tr>${cols.map(c => `<th>${h(t(c[1]))}</th>`).join('')}</tr>
          ${shown.length ? shown.slice(0, 1000).map(r => '<tr>' + cols.map(c => `<td${c[0] === 'reason' ? ' class="rs"' : ''}>${cell(r, c[0])}</td>`).join('') + '</tr>').join('')
            : `<tr><td colspan="${cols.length}" class="muted">${h(t('r_none'))}</td></tr>`}
          ${shown.length > 1000 ? `<tr><td colspan="${cols.length}" class="muted">${h(t('r_first'))}</td></tr>` : ''}</table></div>`;
      body.querySelectorAll('[data-f]').forEach(b => b.onclick = () => { filter = b.dataset.f; renderRes(); });
      const qi = $('rt-q');
      qi.oninput = () => { q = qi.value.toLowerCase(); const pos = qi.selectionStart; renderRes();
        const n = $('rt-q'); n.focus(); try { n.setSelectionRange(pos, pos); } catch(e){} };
    }

    let rz = null;
    window.addEventListener('resize', () => { clearTimeout(rz); rz = setTimeout(() => {
      if(CUR === 'realtime' && res && compact !== (window.innerWidth < 1000)) renderRes(); }, 200); });
    return {
      enter(){ if(!pack) pack = build(); pack.enter(); renderSrc(); renderRes(); },
      relabel(){ if(!pack) return; labels(); pack.relabel(); renderSrc(); renderRes(); }
    };
  })();

  // ---------------------------------------------------------------- запуск и язык
  function relabel(){
    renderHeader();
    if(CUR === 'choose') renderChooser();
    BULK.relabel();
    RT.relabel();
    if(CUR === 'retro') afterSlots();
  }

  async function init(){
    try { const me = await call('/api/auth/me'); if(me && !me.auth_disabled && me.login) USER = me.login; } catch(e){}
    loadUI();
    try { INFO = await call('/api/intake/info'); } catch(e){ INFO = {types: []}; }
    document.querySelectorAll('#modeSeg button').forEach(b => b.onclick = () => show(b.dataset.mode));
    const home = $('modeHome'); if(home) home.onclick = () => show('choose');
    window.addEventListener('resize', setHdr);
    let want = '';
    try { want = new URLSearchParams(location.search).get('mode') || ''; } catch(e){}
    show(['retro', 'realtime', 'choose'].includes(want) ? want : 'choose');
  }

  window.sd3Visible = slotVisible;
  window.sd3AfterSlots = afterSlots;
  window.SD3 = {show, state: UI, types: TYPES, typeSlots: TYPE_SLOTS};
  if(typeof window.setLang === 'function'){
    const orig = window.setLang;
    window.setLang = function(){ const r = orig.apply(this, arguments); relabel(); return r; };
  }
  if(document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
