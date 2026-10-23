"""Общие функции API: сериализация, авторизация кабинета отельера."""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import time

from fastapi import Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..agents.arbiter import SOURCE_RU
from ..config import settings
from ..knowledge.registry import GROUPS, all_specs
from ..models import AgentRun, AgentStep, Conflict, Fact, Hotel, Question, ResolvedValue, utcnow


def iso(d: dt.datetime | None) -> str | None:
    return d.isoformat() + "Z" if d else None


# ---------------------------------------------------------------- авторизация (демо)
def make_token(login: str) -> str:
    ts = str(int(time.time()))
    msg = f"{login}:{ts}"
    sig = hmac.new(settings.secret_key.encode(), msg.encode(), hashlib.sha256).hexdigest()[:32]
    return base64.urlsafe_b64encode(f"{msg}:{sig}".encode()).decode()


def check_token(authorization: str | None = Header(default=None)) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Нужен вход в кабинет отельера")
    try:
        raw = base64.urlsafe_b64decode(authorization.split(" ", 1)[1].encode()).decode()
        login, ts, sig = raw.rsplit(":", 2)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(401, "Неверный токен") from exc
    good = hmac.new(settings.secret_key.encode(), f"{login}:{ts}".encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(good, sig) or time.time() - int(ts) > 7 * 24 * 3600:
        raise HTTPException(401, "Сессия истекла, войдите снова")
    return login


# ---------------------------------------------------------------- сериализация
def fact_dict(f: Fact) -> dict:
    ev = dict(f.evidence or {})
    return {
        "id": f.id, "char_key": f.char_key, "source": f.source, "source_ru": SOURCE_RU.get(f.source, f.source),
        "source_detail": f.source_detail, "method": f.method, "summary": f.summary, "confidence": f.confidence,
        "observed_at": iso(f.observed_at), "valid_until": iso(f.valid_until),
        "stale": bool(f.valid_until and f.valid_until < utcnow()), "status": f.status, "evidence": ev,
        "value": _trim_value(f.value), "run_id": f.run_id,
    }


def _trim_value(v):
    """Убираем из значения тяжёлые массивы (маршруты, профили) — они отдаются отдельным запросом."""
    if not isinstance(v, dict):
        return v
    out = {}
    for k, x in v.items():
        if k in {"line", "profile"}:
            continue
        out[k] = x
    return out


def rv_dict(rv: ResolvedValue | None, spec: dict) -> dict:
    base = {"key": spec["key"], "title": spec["title"], "group": spec["group"], "scope": spec["scope"],
            "icon": spec.get("icon"), "description": spec.get("description", ""), "origin": spec.get("_origin", "builtin")}
    if rv is None:
        return {**base, "status": "unknown", "summary": "", "confidence": 0.0, "sources": [], "value": None,
                "explanation": "Агент ещё не собирал эту характеристику", "updated_at": None, "valid_until": None,
                "stale": False}
    return {**base, "status": rv.status, "summary": rv.summary, "confidence": rv.confidence,
            "sources": rv.sources or [], "sources_ru": [SOURCE_RU.get(s, s) for s in rv.sources or []],
            "value": _trim_value(rv.value), "explanation": rv.explanation, "updated_at": iso(rv.updated_at),
            "valid_until": iso(rv.valid_until), "stale": bool(rv.valid_until and rv.valid_until < utcnow()),
            "fact_ids": rv.fact_ids}


def question_dict(q: Question, session: Session | None = None) -> dict:
    d = {"id": q.id, "hotel_id": q.hotel_id, "char_keys": q.char_keys, "text": q.text, "reason": q.reason,
         "status": q.status, "answer_text": q.answer_text, "parsed": q.parsed, "parse_method": q.parse_method,
         "answered_by": q.answered_by, "created_at": iso(q.created_at), "answered_at": iso(q.answered_at),
         "context": q.context}
    if session is not None:
        h = session.get(Hotel, q.hotel_id)
        d["hotel_name"] = h.label if h else q.hotel_id
        specs = all_specs(session, enabled_only=False)
        d["titles"] = [specs[k]["title"] for k in q.char_keys or [] if k in specs]
    return d


def conflict_dict(c: Conflict, session: Session) -> dict:
    facts = [session.get(Fact, i) for i in c.fact_ids or []]
    h = session.get(Hotel, c.hotel_id)
    specs = all_specs(session, enabled_only=False)
    return {"id": c.id, "hotel_id": c.hotel_id, "hotel_name": h.label if h else "", "char_key": c.char_key,
            "title": specs.get(c.char_key, {}).get("title", c.char_key), "field": c.field, "description": c.description,
            "status": c.status, "resolution": c.resolution, "resolved_by": c.resolved_by,
            "question_id": c.question_id, "created_at": iso(c.created_at), "resolved_at": iso(c.resolved_at),
            "facts": [fact_dict(f) for f in facts if f is not None]}


def run_dict(r: AgentRun, steps: list[AgentStep] | None = None, session: Session | None = None) -> dict:
    d = {"id": r.id, "kind": r.kind, "trigger": r.trigger, "hotel_id": r.hotel_id, "char_keys": r.char_keys,
         "status": r.status, "summary": r.summary, "error": r.error, "params": r.params,
         "created_at": iso(r.created_at), "started_at": iso(r.started_at), "finished_at": iso(r.finished_at)}
    if session is not None and r.hotel_id:
        h = session.get(Hotel, r.hotel_id)
        d["hotel_name"] = h.label if h else None
    if steps is not None:
        d["steps"] = [{"idx": s.idx, "ts": iso(s.ts), "agent": s.agent, "kind": s.kind, "char_key": s.char_key,
                       "message": s.message, "payload": s.payload, "duration_ms": s.duration_ms} for s in steps]
    return d


def hotel_card(session: Session, h: Hotel) -> dict:
    specs = all_specs(session)
    rvs = {rv.char_key: rv for rv in session.scalars(select(ResolvedValue).where(ResolvedValue.hotel_id == h.id))}
    groups = []
    for g in GROUPS:
        items = [rv_dict(rvs.get(k), s) for k, s in specs.items() if s["group"] == g["key"]]
        if items:
            groups.append({"key": g["key"], "title": g["title"], "items": items})
    b = h.building or {}
    known = sum(1 for k in specs if k in rvs and rvs[k].status in {"ok", "conflict", "stale"})
    siblings = []
    if b.get("complex_id"):
        siblings = [{"id": o.id, "label": o.label, "corpus": (o.building or {}).get("corpus")}
                    for o in session.scalars(select(Hotel).where(Hotel.id != h.id)) if (o.building or {}).get("complex_id") == b["complex_id"]]
    return {
        "id": h.id, "name": h.label, "osm_name": h.name, "kind": h.kind, "stars": h.stars, "lat": h.lat, "lon": h.lon,
        "address": h.address, "website": h.website, "phone": h.phone, "osm": (h.osm_tags or {}).get("_osm"),
        "merged_from": (h.osm_tags or {}).get("_merged_from") or [],
        "complex": {"id": b["complex_id"], "corpus": b.get("corpus"), "siblings": siblings} if b.get("complex_id") else None,
        "building": {k: b.get(k) for k in ("levels", "levels_src", "height", "osm")},
        "groups": groups, "coverage": round(known / max(1, len(specs)), 2), "known": known, "total": len(specs),
    }
