"""Агент-арбитр: сводит факты из разных источников в текущее знание.

Правила:
  * у каждой характеристики есть порядок авторитетности источников (conflict.authority);
  * устаревший факт (valid_until в прошлом) теряет 40 % уверенности и помечается stale;
  * согласованные независимые источники повышают уверенность (noisy-OR);
  * расхождение по сравниваемым полям (conflict.fields) — это противоречие: создаётся
    запись Conflict, уверенность снижается, а интервьюер задаёт уточняющий вопрос.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Change, Conflict, Fact, Question, ResolvedValue, utcnow

SOURCE_RU = {
    "geo": "расчёт по геоданным", "osm": "OpenStreetMap", "osm_estimate": "оценка по соседним зданиям",
    "default": "предположение по типу здания", "climate": "климатические данные NOAA", "hotelier": "отельер",
    "hotelier_confirmed": "подтверждено отельером", "moderator": "модератор",
}
STATUS_RU = {"ok": "готово", "conflict": "противоречие", "stale": "устарело", "unknown": "нет данных", "asked": "ждём отельера"}
STALE_FACTOR = 0.6


def _get(value: dict | None, path: str):
    cur = value
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _differs(a, b, fld: dict) -> bool:
    typ = fld.get("type", "enum")
    if typ == "bool":
        return bool(a) != bool(b)
    if typ == "number":
        try:
            return abs(float(a) - float(b)) > float(fld.get("tolerance", 0))
        except (TypeError, ValueError):
            return False
    return str(a) != str(b)


def _fmt(v) -> str:
    if isinstance(v, bool):
        return "да" if v else "нет"
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def effective_confidence(f: Fact, now: dt.datetime) -> float:
    return f.confidence * (STALE_FACTOR if f.valid_until and f.valid_until < now else 1.0)


def resolve(session: Session, hotel_id: str, spec: dict, run_id: int | None = None, logger=None,
            reason: str = "") -> ResolvedValue:
    key = spec["key"]
    now = utcnow()
    facts = list(session.scalars(select(Fact).where(Fact.hotel_id == hotel_id, Fact.char_key == key,
                                                    Fact.status == "active")))
    rv = session.get(ResolvedValue, (hotel_id, key))
    old_summary = rv.summary if rv is not None else ""
    old_had_value = rv is not None and rv.value is not None
    if rv is None:
        rv = ResolvedValue(hotel_id=hotel_id, char_key=key)
        session.add(rv)

    open_q = session.scalars(select(Question).where(Question.hotel_id == hotel_id, Question.status == "open")).all()
    asked = any(key in (q.char_keys or []) for q in open_q)

    if not facts:
        rv.value = None
        rv.summary = ""
        rv.confidence = 0.0
        rv.status = "asked" if asked else "unknown"
        rv.fact_ids = []
        rv.primary_fact_id = None
        rv.sources = []
        rv.explanation = "Данных пока нет" + (" — вопрос отправлен отельеру" if asked else "")
        rv.valid_until = None
        rv.updated_at = now
        session.flush()
        return rv

    authority = spec.get("conflict", {}).get("authority", [])

    def rank(src: str) -> int:
        return authority.index(src) if src in authority else len(authority)

    ordered = sorted(facts, key=lambda f: (rank(f.source), -effective_confidence(f, now), -f.observed_at.timestamp()))
    primary = ordered[0]
    fields = spec.get("conflict", {}).get("fields", [])
    disagreements: list[tuple[Fact, dict, object, object]] = []
    agreeing: list[Fact] = []
    for other in ordered[1:]:
        if other.value is None:
            continue
        diff = False
        overlap = False
        for fld in fields:
            a, b = _get(primary.value, fld["field"]), _get(other.value, fld["field"])
            if a is None or b is None:
                continue
            overlap = True
            if _differs(a, b, fld):
                diff = True
                disagreements.append((other, fld, a, b))
        if overlap and not diff:
            agreeing.append(other)

    p_conf = effective_confidence(primary, now)
    conf = 1.0
    for f in [primary] + agreeing:
        conf *= 1 - effective_confidence(f, now)
    conf = min(0.97, 1 - conf)
    status = "ok"
    expl = [f"Основной источник: {SOURCE_RU.get(primary.source, primary.source)} (уверенность {p_conf:.2f})"]
    if agreeing:
        expl.append("согласуется: " + ", ".join(SOURCE_RU.get(f.source, f.source) for f in agreeing))

    open_conflicts = 0
    active_ids = {f.id for f in facts}
    # противоречия, которые больше не актуальны (факт заменён новым), закрываем
    for c in session.scalars(select(Conflict).where(Conflict.hotel_id == hotel_id, Conflict.char_key == key,
                                                    Conflict.status == "open")):
        still = any(sorted(c.fact_ids) == sorted([primary.id, d[0].id]) and c.field == d[1]["field"] for d in disagreements)
        if not still:
            c.status = "resolved"
            c.resolution = ("источники согласованы после обновления данных" if set(c.fact_ids) <= active_ids
                            else "один из фактов заменён более свежим — противоречие снято")
            c.resolved_by = "arbiter"
            c.resolved_at = now
    for other, fld, a, b in disagreements:
        title = spec.get("value_fields", {}).get(fld["field"], {}).get("title", fld["field"])
        desc = (f"{title}: {SOURCE_RU.get(primary.source, primary.source)} — {_fmt(a)}, "
                f"{SOURCE_RU.get(other.source, other.source)} — {_fmt(b)}")
        pair = sorted([primary.id, other.id])
        existing = session.scalars(select(Conflict).where(Conflict.hotel_id == hotel_id, Conflict.char_key == key,
                                                          Conflict.field == fld["field"])).all()
        prev = next((c for c in existing if sorted(c.fact_ids) == pair), None)
        if prev is not None:
            open_conflicts += prev.status == "open"
            continue
        c = Conflict(hotel_id=hotel_id, char_key=key, fact_ids=pair, field=fld["field"], description=desc)
        session.add(c)
        session.flush()
        hotelier_primary = primary.source in {"hotelier", "hotelier_confirmed"}
        if hotelier_primary:
            # отельер — главный источник для этой характеристики: принимаем его ответ, фиксируем разбор
            c.status = "resolved"
            c.resolved_by = "arbiter"
            c.resolved_at = now
            c.resolution = f"по приоритету источников принят ответ отельера; {SOURCE_RU.get(other.source, other.source)} расходится"
            if logger:
                logger.step("Арбитр", "conflict", f"Расхождение ({desc}) — принят ответ отельера как более авторитетный",
                            char_key=key, payload={"conflict_id": c.id, "facts": pair})
            continue
        open_conflicts += 1
        if logger:
            logger.step("Арбитр", "conflict", f"Противоречие источников — {desc}", char_key=key,
                        payload={"conflict_id": c.id, "facts": pair})
        from .interviewer import ask_clarification

        q = ask_clarification(session, hotel_id, spec, primary, other, fld, a, b, run_id=run_id)
        if q is not None:
            c.question_id = q.id
            if logger:
                logger.step("Интервьюер", "question", f"Уточняющий вопрос отельеру: «{q.text}»", char_key=key,
                            payload={"question_id": q.id})
    if open_conflicts:
        conf = p_conf * 0.75
        status = "conflict"
        policy = spec.get("conflict", {}).get("policy", "priority")
        policy_ru = ("расчёт по геоданным важнее самоотчёта, пока он не опровергнут" if policy == "computed_wins"
                     else "побеждает более авторитетный и свежий источник")
        expl.append(f"есть противоречие ({policy_ru}); уверенность снижена до выяснения")
    elif disagreements:
        expl.append("расхождение с менее авторитетным источником разобрано автоматически")

    if primary.valid_until and primary.valid_until < now and status == "ok":
        status = "stale"
        expl.append("данные устарели — запланирована перепроверка")

    # дополняем значение полями из менее приоритетных источников (например, ответ отельера о своей парковке)
    merged = {}
    extra_summaries = []
    for f in reversed(ordered):
        if f.value:
            merged.update({k: v for k, v in f.value.items() if v is not None})
    merged.update({k: v for k, v in (primary.value or {}).items() if v is not None})
    disagreeing_ids = {d[0].id for d in disagreements}
    for f in ordered[1:]:
        if (f.source in {"hotelier", "hotelier_confirmed"} and f.id not in disagreeing_ids and f.summary
                and f.summary != primary.summary):
            extra_summaries.append(f.summary)
    summary = primary.summary
    if extra_summaries:
        summary += ". " + " ".join(extra_summaries[:1])

    rv.value = merged
    rv.summary = summary
    rv.confidence = round(conf, 3)
    rv.status = status
    rv.fact_ids = [f.id for f in ordered]
    rv.primary_fact_id = primary.id
    rv.sources = list(dict.fromkeys(f.source for f in ordered))
    rv.explanation = "; ".join(expl)
    rv.valid_until = primary.valid_until
    rv.updated_at = now
    session.flush()

    if old_had_value and old_summary and old_summary != rv.summary and reason:
        session.add(Change(hotel_id=hotel_id, char_key=key, old_summary=old_summary, new_summary=rv.summary,
                           reason=reason, run_id=run_id))
    return rv


def supersede_previous(session: Session, hotel_id: str, key: str, source: str, method: str, keep_id: int) -> None:
    for f in session.scalars(select(Fact).where(Fact.hotel_id == hotel_id, Fact.char_key == key, Fact.status == "active",
                                                Fact.source == source, Fact.method == method, Fact.id != keep_id)):
        f.status = "superseded"
    session.flush()  # autoflush выключен: без этого арбитр увидит старый факт активным


def resolve_conflict(session: Session, conflict: Conflict, keep_fact_id: int, who: str, note: str, spec: dict) -> None:
    now = utcnow()
    for fid in conflict.fact_ids:
        if fid != keep_fact_id:
            f = session.get(Fact, fid)
            if f is not None and f.status == "active":
                f.status = "rejected"
    conflict.status = "resolved"
    conflict.resolution = note
    conflict.resolved_by = who
    conflict.resolved_at = now
    session.flush()
    resolve(session, conflict.hotel_id, spec, reason="conflict_resolved")
