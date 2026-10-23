"""Инструменты агентов: единый интерфейс и реестр.

Инструмент — это функция, которая для конкретного отеля достаёт значение
характеристики из определённого источника и честно говорит, насколько она в нём
уверена и почему. Агент-сборщик выбирает инструменты по рецепту характеристики
и по их применимости к отелю (проверка `applicable`).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Callable

from ..models import utcnow


@dataclass
class ToolResult:
    value: dict | None
    summary: str
    confidence: float
    source: str
    source_detail: str
    evidence: dict = field(default_factory=dict)
    ttl_days: int | None = None
    status: str = "ok"  # ok | no_data | not_applicable | asked
    note: str = ""
    observed_at: dt.datetime | None = None

    def __post_init__(self):
        self.confidence = round(max(0.0, min(0.99, float(self.confidence))), 3)
        if self.observed_at is None:
            self.observed_at = utcnow()


@dataclass
class Tool:
    name: str
    title: str
    agent: str
    sources: list[str]
    description: str
    fn: Callable[..., ToolResult]
    applicable: Callable[..., tuple[bool, str]] | None = None
    params_schema: dict = field(default_factory=dict)
    generic: bool = False  # можно использовать в пользовательских характеристиках
    cost: str = "low"

    def check(self, ctx, params: dict) -> tuple[bool, str]:
        if self.applicable is None:
            return True, "инструмент применим ко всем отелям"
        return self.applicable(ctx, params)

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name, "title": self.title, "agent": self.agent, "sources": self.sources,
            "description": self.description, "params": self.params_schema, "generic": self.generic,
            "cost": self.cost,
        }


def cap(text: str) -> str:
    """Первая буква заглавная, остальные как есть (str.capitalize портит имена собственные)."""
    return text[:1].upper() + text[1:] if text else text


TOOLS: dict[str, Tool] = {}


def tool(name: str, title: str, agent: str, sources: list[str], description: str,
         applicable: Callable[..., tuple[bool, str]] | None = None, params_schema: dict | None = None,
         generic: bool = False, cost: str = "low"):
    def deco(fn):
        TOOLS[name] = Tool(name=name, title=title, agent=agent, sources=sources, description=description, fn=fn,
                           applicable=applicable, params_schema=params_schema or {}, generic=generic, cost=cost)
        return fn

    return deco


def get_tool(name: str) -> Tool:
    if not TOOLS:
        load_all()
    if name not in TOOLS:
        raise KeyError(f"Неизвестный инструмент {name}")
    return TOOLS[name]


def load_all() -> dict[str, Tool]:
    from . import climate_tools, geo_tools, hotelier_tools, osm_tools  # noqa: F401

    return TOOLS
