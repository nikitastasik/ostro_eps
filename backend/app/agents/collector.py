"""Агент-сборщик: выполняет рецепт характеристики для отеля.

Для каждого шага рецепта решает, применим ли инструмент к этому отелю, вызывает
его, сохраняет факт с источником, уверенностью и сроком годности, а когда данных
нет или уверенность ниже порога — передаёт задачу интервьюеру (вопрос отельеру).
Все решения пишутся в журнал запуска.
"""

from __future__ import annotations

import datetime as dt
import time

from sqlalchemy import select

from ..models import Fact, Question, utcnow
from ..nlp.ru import dec
from ..tools.base import get_tool
from . import arbiter
from .context import HotelContext


def _hotelier_known(ctx: HotelContext, key: str) -> tuple[bool, bool]:
    s = ctx.session
    has_fact = s.scalars(select(Fact.id).where(Fact.hotel_id == ctx.hotel.id, Fact.char_key == key,
                                               Fact.status == "active",
                                               Fact.source.in_(["hotelier", "hotelier_confirmed"]))).first() is not None
    has_open_q = any(key in (q.char_keys or []) for q in s.scalars(
        select(Question).where(Question.hotel_id == ctx.hotel.id, Question.status == "open")))
    return has_fact, has_open_q


def save_fact(ctx: HotelContext, spec: dict, tool_name: str, res, run_id: int | None) -> Fact:
    ttl = res.ttl_days or int(spec.get("ttl_days", 180))
    now = res.observed_at or utcnow()
    fact = Fact(hotel_id=ctx.hotel.id, char_key=spec["key"], value=res.value, summary=res.summary, source=res.source,
                source_detail=res.source_detail, method=tool_name, confidence=res.confidence, observed_at=now,
                valid_until=now + dt.timedelta(days=ttl), evidence=res.evidence, run_id=run_id)
    ctx.session.add(fact)
    ctx.session.flush()
    arbiter.supersede_previous(ctx.session, ctx.hotel.id, spec["key"], res.source, tool_name, fact.id)
    return fact


def collect(ctx: HotelContext, spec: dict, run_id: int | None, logger, reason: str = "collect",
            allow_questions: bool = True):
    key = spec["key"]
    recipe = spec.get("recipe", [])
    threshold = float(spec.get("confidence_threshold", 0.4))
    tools_txt = " → ".join(step["tool"] for step in recipe) or "нет"
    logger.step("Оркестратор", "plan", f"«{spec['title']}»: план {tools_txt}; порог уверенности {dec(threshold, 2)}",
                char_key=key)
    got: list = []
    for step in recipe:
        name = step["tool"]
        params = dict(step.get("params") or {})
        if name == "hotelier.ask":
            when = step.get("when", "missing")
            has_fact, has_q = _hotelier_known(ctx, key)
            best = max((r.confidence for r in got), default=0.0)
            need = {"always_once": not has_fact, "missing": not got and not has_fact,
                    "low_confidence": best < threshold and not has_fact,
                    "verify": bool(got) and not has_fact}.get(when, False)
            if not need:
                why = ("ответ отельера уже есть" if has_fact else
                       f"уверенность {dec(best, 2)} ≥ порога {dec(threshold, 2)}" if when == "low_confidence" else
                       "данные получены из открытых источников")
                logger.step("Интервьюер", "decision", f"Вопрос отельеру не нужен: {why}", char_key=key)
                continue
            if has_q:
                logger.step("Интервьюер", "decision", "Вопрос отельеру уже отправлен и ждёт ответа", char_key=key)
                continue
            if not allow_questions:
                logger.step("Интервьюер", "decision", "Вопросы в этом запуске отключены", char_key=key)
                continue
            reason_q = {"always_once": "missing", "missing": "missing", "low_confidence": "low_confidence",
                        "verify": "verify"}[when]
            hyp = got[-1].summary if got and when in {"low_confidence", "verify"} else None
            why = {"always_once": "эту характеристику знает только отельер",
                   "missing": "в открытых данных ответа нет",
                   "low_confidence": f"уверенность расчёта {dec(best, 2)} ниже порога {dec(threshold, 2)}",
                   "verify": "расчёт — ключевое преимущество отеля, просим отельера подтвердить"}[when]
            res = get_tool(name).fn(ctx, {"spec": spec, "reason": reason_q, "hypothesis": hyp, "run_id": run_id})
            logger.step("Интервьюер", "question", f"Спрашиваю отельера ({why}): «{res.summary.split('«', 1)[-1].rstrip('»')}»",
                        char_key=key, payload=res.evidence)
            continue
        tool = get_tool(name)
        ok, why = tool.check(ctx, params)
        if not ok:
            logger.step(tool.agent, "decision", f"{tool.title}: не применяю — {why}", char_key=key)
            continue
        logger.step(tool.agent, "tool", f"{tool.title} [{name}]: {why}. Источники: {', '.join(tool.sources)}",
                    char_key=key)
        t0 = time.perf_counter()
        try:
            res = tool.fn(ctx, params)
        except Exception as exc:  # noqa: BLE001
            logger.step(tool.agent, "error", f"{tool.title}: ошибка {exc.__class__.__name__}: {exc}", char_key=key)
            continue
        ms = int((time.perf_counter() - t0) * 1000)
        if res.status != "ok" or res.value is None:
            logger.step(tool.agent, "result", f"{tool.title}: нет данных — {res.summary}", char_key=key, duration_ms=ms)
            continue
        fact = save_fact(ctx, spec, name, res, run_id)
        got.append(res)
        logger.step(tool.agent, "result", f"{res.summary} (уверенность {dec(res.confidence, 2)})", char_key=key,
                    duration_ms=ms, payload={"fact_id": fact.id, "source": res.source, "confidence": res.confidence,
                                             "evidence": res.evidence})
        if key == "building_levels":
            ctx.reset_geo()
    rv = arbiter.resolve(ctx.session, ctx.hotel.id, spec, run_id=run_id, logger=logger, reason=reason)
    if key == "building_levels":
        ctx.reset_geo()
    status_ru = arbiter.STATUS_RU.get(rv.status, rv.status)
    logger.step("Арбитр", "result", f"Итог «{spec['title']}»: {rv.summary or '—'} [{status_ru}, уверенность {dec(rv.confidence, 2)}]",
                char_key=key, payload={"status": rv.status, "confidence": rv.confidence, "sources": rv.sources})
    return rv
