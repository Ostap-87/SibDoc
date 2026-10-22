# -*- coding: utf-8 -*-
"""ЭТРАН (АС ЭТРАН ОАО «РЖД») — перевозочные документы по железной дороге (задел).

Доступ к веб-сервису ЭТРАН даётся по договору с РЖД; запросы подписываются ЭЦП
организации (ГОСТ, КриптоПро CSP). Формат XML-сообщений и порядок подписи —
уточнить у владельца системы (РЖД) по документации веб-сервиса; здесь только каркас.

  connectors:
    etran:
      type: etran
      enabled: false
      label: ЭТРАН (РЖД)
      service_url: ""              # адрес веб-сервиса — выдаёт РЖД по договору
      login: ""
      password_env: SIBDOC_ETRAN_PASSWORD
      cert_thumbprint: ""          # отпечаток сертификата ЭЦП в хранилище КриптоПро
      org_code: ""                 # код организации/плательщика в ЭТРАН
      # export_dir: /data/incoming/etran   # пока нет доступа: выгрузки из АРМ ЭТРАН
"""
from __future__ import annotations

from .base import P
from .stub import EXPORT_PARAMS, StubConnector


class EtranConnector(StubConnector):
    type = "etran"
    title = "ЭТРАН (РЖД): ж/д накладные и контейнеры"
    access_hint = ("договор с ОАО «РЖД» на доступ к веб-сервису АС ЭТРАН (через ТЦФТО): адрес "
                   "сервиса, логин и пароль пользователя ЭТРАН, сертификат ЭЦП организации "
                   "(ГОСТ, КриптоПро CSP) на сервере SibDoc; запрашивает отдел логистики или "
                   "ж/д экспедитор")
    protocol_note = ("формат XML-запросов ЭТРАН (накладные, состояние вагонов/контейнеров) и "
                     "порядок подписи ЭЦП уточнить у владельца системы (РЖД) по документации "
                     "веб-сервиса; пока можно указать export_dir — папку с выгрузками из АРМ ЭТРАН")
    params = [
        P("service_url", "Адрес веб-сервиса ЭТРАН (по договору с РЖД)", required=True,
          example="уточнить у РЖД"),
        P("login", "Пользователь ЭТРАН", required=True),
        P("password", "Пароль ЭТРАН", required=True, secret=True,
          example="password_env: SIBDOC_ETRAN_PASSWORD"),
        P("cert_thumbprint", "Отпечаток сертификата ЭЦП (КриптоПро)", required=True,
          example="3F2A...C9"),
        P("org_code", "Код организации/плательщика в ЭТРАН"),
    ] + EXPORT_PARAMS
