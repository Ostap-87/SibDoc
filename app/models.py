from __future__ import annotations
# -*- coding: utf-8 -*-
"""Общие структуры данных движка проверок."""
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
import pandas as pd


class Status(str, Enum):
    OK = "OK"
    ISSUE = "ISSUE"
    REVIEW = "REVIEW"
    SKIPPED = "SKIPPED"


@dataclass
class Source:
    """Загруженный источник данных (один файл/лист)."""
    slot: str            # идентификатор слота: wide_table, purchase, ...
    name: str            # имя файла
    df: pd.DataFrame = field(default_factory=pd.DataFrame)

    @property
    def rows(self) -> int:
        return len(self.df)


@dataclass
class CheckOutput:
    """Результат одной проверки."""
    check_id: str
    title: str
    df: pd.DataFrame          # строки с колонками status/detail
    meta: dict = field(default_factory=dict)

    def counts(self) -> dict:
        if "status" not in self.df.columns or self.df.empty:
            return {"total": 0, "OK": 0, "ISSUE": 0, "REVIEW": 0, "SKIPPED": 0}
        vc = self.df["status"].value_counts().to_dict()
        return {
            "total": len(self.df),
            "OK": int(vc.get(Status.OK.value, 0)),
            "ISSUE": int(vc.get(Status.ISSUE.value, 0)),
            "REVIEW": int(vc.get(Status.REVIEW.value, 0)),
            "SKIPPED": int(vc.get(Status.SKIPPED.value, 0)),
        }


@dataclass
class Finding:
    """Одна находка data-quality детекции."""
    severity: str          # ERROR | WARNING | INFO
    source: str            # слот источника
    section: str           # секция/блок данных
    column: str            # колонка (может быть "")
    issue: str             # что не так
    affected_rows: int = 0
    examples: str = ""     # примеры значений/строк
    impact: str = ""       # на какую проверку влияет

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()
