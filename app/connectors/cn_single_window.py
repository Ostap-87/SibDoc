# -*- coding: utf-8 -*-
"""Единое окно КНР (中国国际贸易单一窗口, www.singlewindow.cn) — китайские
декларации 报关单 (задел).

Работа в 单一窗口 идёт под ключом-носителем UKey (卡介质 / IC-карта 中国电子口岸)
импортёра или таможенного брокера; открытого API для забора 报关单 нет.
Варианты автоматизации (ПО брокера 报关软件 с API, клиент 中国电子口岸) —
уточнить у владельца системы/брокера; здесь только каркас.

Пока доступа нет: брокер присылает 报关单 (Excel/XML) почтой или на SFTP —
их забирают коннекторы imap/sftp (slot_rules -> cn_dec), либо кладёт в export_dir.

  connectors:
    cn_single_window:
      type: cn_single_window
      enabled: false
      label: 单一窗口 (Единое окно КНР)
      role: broker                 # importer | broker
      company_code: ""             # 海关10位编码 или 统一社会信用代码
      ukey_holder: ""              # у кого UKey: импортёр / брокер (название)
      service_url: ""              # API ПО брокера, если оно есть
      # export_dir: /data/incoming/cn_dec
"""
from __future__ import annotations

from .base import P
from .stub import EXPORT_PARAMS, StubConnector


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
    params = [
        P("role", "Чей доступ", example="importer | broker"),
        P("company_code", "Код компании (海关10位编码 / 统一社会信用代码)", required=True,
          example="3302960000"),
        P("ukey_holder", "У кого UKey (卡介质)", required=True, example="брокер ООО «...»"),
        P("service_url", "API ПО брокера (если есть)", required=True,
          example="уточнить у брокера"),
        P("token", "Ключ API ПО брокера", secret=True, example="token_env: SIBDOC_CN_SW_TOKEN"),
    ] + EXPORT_PARAMS
