# -*- coding: utf-8 -*-
"""Единое окно КНР (中国国际贸易单一窗口, www.singlewindow.cn) — китайские
декларации 报关单. Работает через выгрузки (Коннекторы/банк).

Открытого API для третьих лиц у 单一窗口 нет: вход — под ключом-носителем UKey
(卡介质 / IC-карта 中国电子口岸) импортёра или таможенного брокера. Поэтому основной
режим — папка выгрузок export_dir, куда сотрудник или брокер сохраняет:
  * XML-сообщения 报关单 (DecMessage: DecHead / DecList / DecContainers) — «导出报文» из
    单一窗口 / клиента 中国电子口岸 или из ПО брокера (报关软件) -> слот cn_dec целиком;
  * Excel-список 报关单 из «Единого окна» (货物申报 → 数据查询/统计 → 报关数据查询 → 导出):
    统一编号, 海关编号, 申报日期, 境内收发货人, 合同协议号 … -> слот cn_dec (шапки деклараций);
    структура выгрузки официально не опубликована — колонки распознаются словарём полей;
  * Excel/CSV брокера с товарами 报关单 (海关编号, 商品编号, 单价, 净重 …) -> cn_dec.
Слот определяется по содержимому файла, а не только по имени. PDF-распечатки 报关单 — через блок
«Сканы деклараций» (OCR). Квитанции (回执) не разбираются.

Прямой обмен (ПО брокера с API, клиент 中国电子口岸 с UKey на сервере) — задел.

  connectors:
    cn_single_window:
      type: cn_single_window
      enabled: false
      label: 单一窗口 (Единое окно КНР)
      export_dir: /data/incoming/cn_dec   # выгрузки 单一窗口 / брокера
      role: broker                 # importer | broker
      company_code: ""             # 海关10位编码 или 统一社会信用代码
      ukey_holder: ""              # у кого UKey: импортёр / брокер (название)
      service_url: ""              # API ПО брокера, если оно есть
"""
from __future__ import annotations

from .base import P
from .stub import EXPORT_PARAMS, StubConnector

CN_MASKS = ["*.xml", "*.xlsx", "*.xls", "*.csv", "*.zip"]


class CnSingleWindowConnector(StubConnector):
    type = "cn_single_window"
    title = "Единое окно КНР (中国国际贸易单一窗口): 报关单"
    access_hint = ("ключ-носитель UKey (卡介质 / IC-карта 中国电子口岸) импортёра или "
                   "таможенного брокера и учётная запись в 单一窗口; открытого API нет — у "
                   "брокера узнать, может ли его ПО (报关软件) отдавать 报关单 по API, и получить "
                   "адрес и доступ")
    protocol_note = ("способ автоматического обмена с 单一窗口 (ПО брокера или клиент "
                     "中国电子口岸 с UKey) уточнить у владельца системы/брокера; пока 报关单 "
                     "брокер присылает почтой или на SFTP (коннекторы imap/sftp) или кладёт в "
                     "export_dir")
    need = ("открытого API нет: папка export_dir с выгрузками; для прямого обмена — UKey (卡介质) и "
            "API ПО брокера (报关软件), если оно есть")
    need_from = "выгрузки и UKey — у импортёра или таможенного брокера в КНР; API — у разработчика ПО брокера"
    exports_hint = ("XML 报关单 DecMessage (导出报文), Excel-список «报关数据查询 → 导出», Excel/CSV брокера с "
                    "товарами 报关单 — слот «报关单 КНР»")
    export_defaults = {"pattern": CN_MASKS, "zip_masks": CN_MASKS,
                       "slot_rules": [{"pattern": r"(?i)(报关单|报关数据|customs.?dec|cn_dec|DecMessage)",
                                       "slot": "cn_dec"}]}
    content_slots = ("cn_dec",)
    params = [
        P("role", "Чей доступ", example="importer | broker"),
        P("company_code", "Код компании (海关10位编码 / 统一社会信用代码)", required=True,
          example="3302960000"),
        P("ukey_holder", "У кого UKey (卡介质)", required=True, example="брокер ООО «...»"),
        P("service_url", "API ПО брокера (если есть)", required=True,
          example="уточнить у брокера"),
        P("token", "Ключ API ПО брокера", secret=True, example="token_env: SIBDOC_CN_SW_TOKEN"),
    ] + EXPORT_PARAMS
