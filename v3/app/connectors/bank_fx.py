# -*- coding: utf-8 -*-
"""Банк: выписки и валютный контроль (Коннекторы/банк).

Работает через выгрузки (export_dir): бухгалтер или 1С:ДиректБанк / «Клиент-банк» / интернет-банк
сохраняет выписки в папку, коннектор забирает их в слот bank и сам определяет формат
(app/services/bank_statements.py):
  * 1CClientBankExchange (kl_to_1c.txt — выгрузка «Клиент-банка» для 1С, Windows-1251/DOS);
  * ISO 20022 camt.053 / camt.052 / camt.054 (XML);
  * SWIFT MT940 (*.sta, *.940, *.mt940, *.txt);
  * CSV / Excel интернет-банков КНР (交易日期, 借方发生额, 贷方发生额, 对方户名, 摘要 …) и РФ.
Дальше платежи сверяются с контрактами проверкой «8. Оплаты».

Прямой API банка для юрлиц (УНК, ведомость банковского контроля, справки о подтверждающих
документах) у каждого банка свой — договор с банком, client_id / client_secret, сертификат:
обмен не реализован, остаётся заделом («нужен доступ»).

  connectors:
    bank_fx:
      type: bank_fx_control
      enabled: false
      label: Банк — выписки и валютный контроль
      export_dir: /data/incoming/bank   # папка выписок (1С:ДиректБанк, «Клиент-банк», интернет-банк)
      # pattern / slot_rules / zip_masks — по умолчанию все форматы выписок -> слот bank
      # прямой API банка (задел):
      bank: ""
      api_url: ""
      client_id: ""
      client_secret_env: SIBDOC_BANK_CLIENT_SECRET
      cert_file: ""
      cert_password_env: SIBDOC_BANK_CERT_PASSWORD
      unk: []
"""
from __future__ import annotations

from .base import P
from .stub import EXPORT_PARAMS, StubConnector

BANK_MASKS = ["*.txt", "*.xml", "*.sta", "*.940", "*.mt940", "*.csv", "*.xlsx", "*.xls", "*.zip"]


class BankFxControlConnector(StubConnector):
    type = "bank_fx_control"
    title = "Банк: выписки и валютный контроль (1С «Клиент-банк», camt.053, MT940, банки КНР)"
    access_hint = ("в банке (валютный контроль / поддержка API для юрлиц): договор на API, "
                   "client_id и client_secret, сертификат для TLS/подписи и список УНК по "
                   "контрактам; либо в 1С:ДиректБанк настроить выгрузку ведомостей банковского "
                   "контроля и выписок в папку и указать её в export_dir")
    protocol_note = ("формат API конкретного банка (ВБК, справки о подтверждающих документах, "
                     "поступления валюты) уточнить у владельца системы (банка); выписки уже "
                     "забираются из export_dir — папки выгрузок 1С:ДиректБанк / «Клиент-банка»")
    need = ("для выписок — только папка export_dir; для API банка — договор на API, client_id/client_secret "
            "и сертификат (TLS/подпись), список УНК по контрактам")
    need_from = "выписки — у бухгалтерии (папка выгрузки), API — у банка (валютный контроль, поддержка API)"
    exports_hint = ("1CClientBankExchange (kl_to_1c.txt), ISO 20022 camt.053/052/054, SWIFT MT940, "
                    "CSV/Excel интернет-банков КНР и РФ — слот «Банк: выписки»")
    export_defaults = {
        "pattern": BANK_MASKS,
        "zip_masks": BANK_MASKS,
        "slot_exts": [".txt", ".xml", ".sta", ".940", ".mt940", ".csv", ".xlsx", ".xls"],
        "slot_rules": [{"pattern": r"(?i)\.(txt|xml|sta|940|mt940|csv|xlsx|xls)$", "slot": "bank"}],
    }
    content_slots = ("bank",)
    params = [
        P("bank", "Банк", required=True, example="название банка"),
        P("api_url", "Адрес API банка", required=True, example="из документации банка"),
        P("client_id", "client_id", required=True),
        P("client_secret", "client_secret", required=True, secret=True,
          example="client_secret_env: SIBDOC_BANK_CLIENT_SECRET"),
        P("cert_file", "Сертификат (TLS/подпись)", example="/run/secrets/bank_cert.pfx"),
        P("cert_password", "Пароль сертификата", secret=True,
          example="cert_password_env: SIBDOC_BANK_CERT_PASSWORD"),
        P("unk", "УНК контрактов", example='["26100001/1481/0000/2/1"]'),
    ] + EXPORT_PARAMS
