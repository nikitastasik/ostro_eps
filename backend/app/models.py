"""Схема хранения знаний об отелях.

Ключевая идея: значение характеристики никогда не хранится «просто так».
Каждый факт (Fact) — это утверждение конкретного источника с уверенностью,
датой получения, сроком годности и доказательствами (evidence). Текущее знание
(ResolvedValue) вычисляет агент-арбитр из всех активных фактов и помнит,
из каких фактов оно собрано и были ли между ними противоречия (Conflict).
"""

from __future__ import annotations

import datetime as dt
import re

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, JSONType


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


class Hotel(Base):
    __tablename__ = "hotels"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(300))
    kind: Mapped[str] = mapped_column(String(40))
    stars: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    address: Mapped[str | None] = mapped_column(String(400), nullable=True)
    district: Mapped[str | None] = mapped_column(String(120), nullable=True)
    website: Mapped[str | None] = mapped_column(String(400), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(120), nullable=True)
    osm_tags: Mapped[dict] = mapped_column(JSONType, default=dict)
    building: Mapped[dict] = mapped_column(JSONType, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    @property
    def label(self) -> str:
        """Отображаемое имя: корпус комплекса или подпись тёзки («Grand Way · Северная ул.»); "кавычки" из OSM — «ёлочки»."""
        name = re.sub(r'"([^"]*)"', r"«\1»", self.name)
        extra = (self.building or {}).get("corpus") or self.district
        return f"{name} · {extra}" if extra else name


class Characteristic(Base):
    __tablename__ = "characteristics"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    group: Mapped[str] = mapped_column(String(60))
    scope: Mapped[str] = mapped_column(String(20))  # hotel | facade
    description: Mapped[str] = mapped_column(Text, default="")
    spec: Mapped[dict] = mapped_column(JSONType, default=dict)
    origin: Mapped[str] = mapped_column(String(20), default="builtin")  # builtin | custom
    created_from_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    compiled_by: Mapped[str | None] = mapped_column(String(20), nullable=True)  # llm | rules | yaml
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Fact(Base):
    """Одно утверждение одного источника о характеристике отеля."""

    __tablename__ = "facts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    hotel_id: Mapped[str] = mapped_column(ForeignKey("hotels.id", ondelete="CASCADE"), index=True)
    char_key: Mapped[str] = mapped_column(String(80), index=True)
    value: Mapped[dict] = mapped_column(JSONType)
    summary: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(40))  # geo | osm | climate | hotelier | dsm | default
    source_detail: Mapped[str] = mapped_column(Text, default="")
    method: Mapped[str] = mapped_column(String(80))  # инструмент агента
    confidence: Mapped[float] = mapped_column(Float)
    observed_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    valid_until: Mapped[dt.datetime] = mapped_column(DateTime)
    evidence: Mapped[dict] = mapped_column(JSONType, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | superseded | rejected
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (Index("ix_facts_hotel_char_status", "hotel_id", "char_key", "status"),)


class ResolvedValue(Base):
    """Текущее знание системы: результат работы агента-арбитра."""

    __tablename__ = "resolved_values"

    hotel_id: Mapped[str] = mapped_column(ForeignKey("hotels.id", ondelete="CASCADE"), primary_key=True)
    char_key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(20), default="unknown")  # ok | conflict | stale | unknown | asked
    fact_ids: Mapped[list] = mapped_column(JSONType, default=list)
    primary_fact_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sources: Mapped[list] = mapped_column(JSONType, default=list)
    explanation: Mapped[str] = mapped_column(Text, default="")
    valid_until: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Conflict(Base):
    __tablename__ = "conflicts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    hotel_id: Mapped[str] = mapped_column(ForeignKey("hotels.id", ondelete="CASCADE"), index=True)
    char_key: Mapped[str] = mapped_column(String(80))
    fact_ids: Mapped[list] = mapped_column(JSONType, default=list)
    field: Mapped[str] = mapped_column(String(80), default="")
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | resolved
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(40), nullable=True)
    question_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class Question(Base):
    """Вопрос агента-интервьюера отельеру."""

    __tablename__ = "questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    hotel_id: Mapped[str] = mapped_column(ForeignKey("hotels.id", ondelete="CASCADE"), index=True)
    char_keys: Mapped[list] = mapped_column(JSONType, default=list)
    text: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(String(30))  # missing | low_confidence | conflict | stale
    context: Mapped[dict] = mapped_column(JSONType, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | answered | closed
    answer_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    parsed: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    parse_method: Mapped[str | None] = mapped_column(String(20), nullable=True)
    answered_by: Mapped[str | None] = mapped_column(String(40), nullable=True)
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    answered_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(40))  # collect | answer | actualize | scenario | new_characteristic
    trigger: Mapped[str] = mapped_column(String(40), default="manual")
    hotel_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    char_keys: Mapped[list] = mapped_column(JSONType, default=list)
    params: Mapped[dict] = mapped_column(JSONType, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued | running | done | error
    summary: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class AgentStep(Base):
    __tablename__ = "agent_steps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True)
    idx: Mapped[int] = mapped_column(Integer)
    ts: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    agent: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(20))  # plan | tool | decision | result | question | conflict | llm | error
    char_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    message: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSONType, default=dict)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Change(Base):
    """Изменение текущего знания (для ленты актуализации)."""

    __tablename__ = "changes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    hotel_id: Mapped[str] = mapped_column(ForeignKey("hotels.id", ondelete="CASCADE"), index=True)
    char_key: Mapped[str] = mapped_column(String(80))
    old_summary: Mapped[str] = mapped_column(Text, default="")
    new_summary: Mapped[str] = mapped_column(Text, default="")
    reason: Mapped[str] = mapped_column(String(60), default="")
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class GeoOverride(Base):
    """Изменения городской среды поверх снимка OSM (сценарий «построили дом»)."""

    __tablename__ = "geo_overrides"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(20), default="building")
    label: Mapped[str] = mapped_column(String(200))
    geometry: Mapped[dict] = mapped_column(JSONType)  # {"type": "Polygon", "coordinates": [...]}
    height_m: Mapped[float] = mapped_column(Float)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class SearchLog(Base):
    __tablename__ = "search_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    query: Mapped[str] = mapped_column(Text)
    parsed: Mapped[dict] = mapped_column(JSONType, default=dict)
    parser: Mapped[str] = mapped_column(String(20))
    top_hotels: Mapped[list] = mapped_column(JSONType, default=list)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
