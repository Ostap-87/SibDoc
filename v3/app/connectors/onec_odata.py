# -*- coding: utf-8 -*-
"""1С:Предприятие через стандартный интерфейс OData (задел, но рабочий при доступе).

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
"""
from __future__ import annotations

from urllib.parse import quote

from .base import Connector, P, STAGE_NEEDS_ACCESS, TLS_PARAMS
from .rest_api import RestConnector


class OneCODataConnector(RestConnector):
    type = "onec_odata"
    title = "1С:Предприятие (OData)"
    stage = STAGE_NEEDS_ACCESS
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
    ] + TLS_PARAMS
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
        s = self._session()
        data = self._request(s, "GET", self._root() + "/", {"$format": "json"})
        names = [str(x.get("name") or x.get("url") or "") for x in (data.get("value") or [])
                 if isinstance(x, dict)] if isinstance(data, dict) else []
        entity = str(self.opt("entity", ""))
        has = entity in names
        return {"ok": has,
                "detail": f"OData 1С доступен, объектов в составе: {len(names)}; «{entity}» "
                          + ("есть в составе" if has else
                             "НЕ включён в состав OData — попросите администратора 1С добавить")}
