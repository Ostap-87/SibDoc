# -*- coding: utf-8 -*-
"""1С:Предприятие через стандартный интерфейс OData (обмен реализован, проверен на эмуляторе)
и через выгрузки EnterpriseData в папку (Коннекторы/банк).

Запрос: GET <base_url>/odata/standard.odata/<Entity>?$format=json, basic-авторизация
пользователем 1С, страницы через $top/$skip, записи в "value".

Что нужно от администратора 1С:
  1) опубликовать базу на веб-сервере (Конфигуратор: Администрирование ->
     Публикация на веб-сервере) с флажком «Публиковать стандартный интерфейс OData»;
  2) включить нужные объекты в состав OData (обработка «Настройка стандартного
     интерфейса OData» / метод УстановитьСоставСтандартногоИнтерфейсаOData);
  3) завести пользователя 1С с правом чтения этих объектов.

  connectors:
    onec:
      type: onec_odata
      enabled: false
      label: 1С (OData)
      base_url: "https://1c.company.ru/trade"            # адрес публикации базы
      entity: "Document_ПоступлениеТоваровУслуг"          # или Catalog_Номенклатура ...
      username: sibdoc
      password_env: SIBDOC_1C_PASSWORD
      filter: "Posted eq true"                            # $filter (необязательно)
      select: "Number,Date,СуммаДокумента"                # $select (необязательно)
      page_size: 1000
      slot: purchase                                      # -> purchase.csv
      # export_dir: /data/incoming/1c                     # пока нет OData: файлы EnterpriseData

Ошибки OData 1С приходят JSON {"odata.error": {"code": "…", "message": {"value": "…"}}} —
текст показывается как есть. Без доступа к OData работает мост export_dir: файлы обмена
EnterpriseData (Документ.ПоступлениеТоваровУслуг, Документ.ГТДИмпорт, Справочник.Договоры и
Справочник.Контрагенты) раскладываются по слотам purchase / ru_dt / contract_register
(config/enterprise_data.yaml); выписки «Клиент-банка» (kl_to_1c.txt) — в слот bank.
"""
from __future__ import annotations

from urllib.parse import quote

import json

from .base import Connector, ConnectorError, P, STAGE_NEEDS_ACCESS, TLS_PARAMS
from .rest_api import RestConnector
from .stub import EXPORT_PARAMS, ExportBridge

ONEC_MASKS = ["*.xml", "*.zip", "*.txt", "*.xlsx", "*.csv"]


class OneCODataConnector(ExportBridge, RestConnector):
    type = "onec_odata"
    title = "1С:Предприятие (OData, выгрузки EnterpriseData)"
    stage = STAGE_NEEDS_ACCESS
    verified = "emulator"
    need = ("публикация базы 1С на веб-сервере со «стандартным интерфейсом OData», объекты в составе OData, "
            "пользователь 1С с правом чтения; без этого — папка export_dir с выгрузками EnterpriseData")
    need_from = "у администратора 1С (публикация, состав OData, пользователь) или у бухгалтерии (выгрузки)"
    exports_hint = ("EnterpriseData XML (поступление товаров, ГТД по импорту, договоры, контрагенты) -> purchase / "
                    "ru_dt / contract_register; kl_to_1c.txt «Клиент-банка» -> bank")
    export_defaults = {"pattern": ONEC_MASKS, "zip_masks": ONEC_MASKS,
                       "slot_exts": [".xml", ".txt", ".xlsx", ".csv"]}
    content_slots = ("purchase", "ru_dt", "contract_register", "bank")
    access_hint = ("у администратора 1С: опубликовать базу на веб-сервере с флажком «Публиковать "
                   "стандартный интерфейс OData», включить нужные объекты в состав OData, выдать "
                   "пользователя 1С с правом чтения; заполнить base_url, entity, username, "
                   "password_env и slot")
    params = [
        P("base_url", "Адрес публикации базы 1С", required=True, example="https://1c.company.ru/trade"),
        P("entity", "Объект OData", required=True, example="Document_ПоступлениеТоваровУслуг"),
        P("username", "Пользователь 1С", required=True, example="sibdoc"),
        P("password", "Пароль пользователя 1С", required=True, secret=True,
          example="password_env: SIBDOC_1C_PASSWORD"),
        P("filter", "Фильтр ($filter)", example="Posted eq true"),
        P("select", "Поля ($select)", example="Number,Date,СуммаДокумента"),
        P("page_size", "Строк за запрос ($top)", example="1000"),
        P("slot", "Слот (файл <slot>.csv)", required=True, example="purchase"),
    ] + TLS_PARAMS + EXPORT_PARAMS
    defaults = {"method": "GET", "auth": "basic", "records_path": "value",
                "pagination": "offset", "offset_param": "$skip", "limit_param": "$top",
                "page_size": 1000}

    def opt(self, key: str, default=None):
        if key == "params":
            q = {"$format": "json"}
            if super().opt("filter"):
                q["$filter"] = super().opt("filter")
            if super().opt("select"):
                q["$select"] = super().opt("select")
            q.update(Connector.opt(self, "params") or {})
            return q
        return super().opt(key, default)

    def _root(self) -> str:
        return str(self.opt("base_url", "")).rstrip("/") + "/odata/standard.odata"

    def _base_url(self) -> str:
        return f"{self._root()}/{quote(str(self.opt('entity', '')), safe='_()')}"

    def _test(self) -> dict:
        if not self._direct() and self._export_mode():
            return self._export_test()
        s = self._session()
        data = self._request(s, "GET", self._root() + "/", {"$format": "json"})
        names = [str(x.get("name") or x.get("url") or "") for x in (data.get("value") or [])
                 if isinstance(x, dict)] if isinstance(data, dict) else []
        entity = str(self.opt("entity", ""))
        has = entity in names
        return {"ok": has,
                "detail": f"OData 1С доступен, объектов в составе: {len(names)}; «{entity}» "
                          + ("есть в составе (проверено на эмуляторе; на реальной базе — после выдачи доступа)"
                             if has else "НЕ включён в состав OData — попросите администратора 1С добавить")}

    # ---------------------------------------------------------------- Коннекторы/банк
    def _gate(self) -> str | None:
        if self._export_mode() and self.missing():
            return None                      # OData не настроен — работаем через папку выгрузок
        return super()._gate()

    def _direct(self) -> bool:
        return not self.missing()

    def _request(self, s, method: str, url: str, params=None, body=None):
        try:
            return super()._request(s, method, url, params, body)
        except ConnectorError as exc:
            raise ConnectorError(_odata_error(str(exc))) from exc

    def _pull(self, dest_dir: str) -> list[dict]:
        if not self._direct() and self._export_mode():
            return self._export_pull(dest_dir)
        return super()._pull(dest_dir)


def _odata_error(text: str) -> str:
    """«HTTP 400: {"odata.error": {...}}» -> «HTTP 400: 1С: <текст ошибки>»."""
    head, _, rest = text.partition(": ")
    try:
        data = json.loads(rest)
    except ValueError:
        return text
    err = (data or {}).get("odata.error") or (data or {}).get("error") if isinstance(data, dict) else None
    if not isinstance(err, dict):
        return text
    msg = err.get("message")
    msg = msg.get("value") if isinstance(msg, dict) else msg
    return f"{head}: 1С: {msg or err.get('code') or rest}"
