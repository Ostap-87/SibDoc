# -*- coding: utf-8 -*-
"""Банк: валютный контроль (задел) — УНК, ведомость банковского контроля (ВБК),
справки о подтверждающих документах, поступления валюты.

Два пути:
  * API банка для юрлиц (договор, client_id/client_secret, сертификат) — формат
    у каждого банка свой: уточнить у владельца системы (банка); здесь только каркас;
  * выгрузка из 1С:ДиректБанк / клиент-банка в папку — укажите её в export_dir,
    коннектор заберёт файлы как «папка».

  connectors:
    bank_fx:
      type: bank_fx_control
      enabled: false
      label: Валютный контроль (банк)
      bank: ""                     # название банка
      api_url: ""                  # адрес API банка (из документации банка)
      client_id: ""
      client_secret_env: SIBDOC_BANK_CLIENT_SECRET
      cert_file: ""                # сертификат для TLS/подписи, если требует банк
      cert_password_env: SIBDOC_BANK_CERT_PASSWORD
      unk: []                      # УНК контрактов
      # export_dir: /data/incoming/directbank
"""
from __future__ import annotations

from .base import P
from .stub import EXPORT_PARAMS, StubConnector


class BankFxControlConnector(StubConnector):
    type = "bank_fx_control"
    title = "Банк: валютный контроль (УНК, ВБК, поступления)"
    access_hint = ("в банке (валютный контроль / поддержка API для юрлиц): договор на API, "
                   "client_id и client_secret, сертификат для TLS/подписи и список УНК по "
                   "контрактам; либо в 1С:ДиректБанк настроить выгрузку ведомостей банковского "
                   "контроля и выписок в папку и указать её в export_dir")
    protocol_note = ("формат API конкретного банка (ВБК, справки о подтверждающих документах, "
                     "поступления валюты) уточнить у владельца системы (банка); пока можно "
                     "указать export_dir — папку выгрузок 1С:ДиректБанк")
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
