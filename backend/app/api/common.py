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
from ..agents.interviewer import fields_text
from ..config import settings
from ..knowledge.registry import GROUPS, all_specs
from ..models import AgentRun, AgentStep, Conflict, Fact, Hotel, Question, ResolvedValue, utcnow
from ..nlp.ru import dec


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
# доказательства факта для человека: подписи по-русски, числа с запятой, без служебных полей
EVIDENCE_RU = {
    "method": "метод",
    "note": "примечание",
    "model": "модель",
    "algorithm": "алгоритм",
    "rule": "правило",
    "confidence_factors": "из чего уверенность",
    "floors_checked": "проверено этажей",
    "months": "месяцы",
    "start_offset_m": "до ближайшего узла пешеходного графа",
    "answer": "ответ отельера",
    "question": "вопрос",
    "parse_method": "как разобран ответ",
    "missing": "не удалось извлечь",
    "validation": "проверка метода",
    "score_formula": "формула",
    "hours_known": "с известными часами работы",
    "places_checked": "проверено объектов",
    "tags": "теги OSM",
    "receiver_height_m": "высота точки у окна",
}
EVIDENCE_HIDDEN = {
    "fact_id",
    "question_id",
    "sunset_azimuths",
    "drive_speeds",
    "landmarks_checked",
    "peaks_checked",
    "target",
    "confirmed_fact_id",
    "answered_by",
    "building",
}
CONFIDENCE_RU = {
    "base_model": "базовая уверенность модели",
    "levels": "этажность отеля",
    "levels_source": "откуда этажность",
    "levels_factor": "поправка за этажность",
    "footprint_known": "контур здания",
    "footprint_factor": "поправка за контур",
    "neighbours_known_height_share": "соседи с известной высотой",
    "neighbours_300m": "зданий в 300 м",
    "obstacles_factor": "поправка за застройку",
}
LEVELS_SRC_RU = {
    "osm": "тег OSM",
    "osm_estimate": "оценка по соседям",
    "osm_height": "тег высоты OSM",
    "osm_parts": "3D-части здания в OSM",
    "neighbors": "оценка по соседям",
    "default": "по типу здания",
    "hotelier": "отельер",
    "none": "неизвестна",
}


def _num(x) -> str:
    return str(int(x)) if float(x).is_integer() else dec(float(x), 2).rstrip("0").rstrip(",")


def _confidence_part(k: str, v) -> str:
    if k == "footprint_known":
        return "известен" if v else "неизвестен"
    if k == "levels":
        return f"{v} эт." if v else "неизвестна"
    if k == "levels_source":
        return LEVELS_SRC_RU.get(v, str(v))
    if k == "neighbours_known_height_share":
        return f"{round(float(v) * 100)}%"
    return _num(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else str(v)


def _evidence_text(k: str, v, fields: dict) -> str | None:
    if v is None or v == [] or v == {} or v == "":
        return None
    if k == "confidence_factors" and isinstance(v, dict):
        return "; ".join(f"{CONFIDENCE_RU.get(a, a)} — {_confidence_part(a, b)}" for a, b in v.items())
    if k == "parse_method":
        return {"rules": "правилами", "llm": "языковой моделью"}.get(v, str(v))
    if k == "missing" and isinstance(v, list):
        return ", ".join((fields.get(x) or {}).get("title", x) for x in v)
    if k == "validation" and isinstance(v, dict) and "mae_neighbors" in v:
        n = f"{v['validated_on']:,}".replace(",", "\u00a0")
        return f"кросс-валидация на {n} зданиях: ошибка {dec(v['mae_neighbors'], 2)} эт., ±1 этаж — {round(v['within1_neighbors'] * 100)}%"
    if k == "tags" and isinstance(v, dict):
        return ", ".join(f"{a}={b}" for a, b in v.items())
    if k in ("start_offset_m", "receiver_height_m"):
        return f"{_num(v)} м"
    if isinstance(v, bool):
        return "да" if v else "нет"
    if isinstance(v, (int, float)):
        return _num(v)
    if isinstance(v, list):
        return ", ".join(str(x) for x in v)
    if isinstance(v, dict):
        return "; ".join(f"{a}: {b}" for a, b in v.items())
    return str(v)


def evidence_view(ev: dict, spec: dict | None = None) -> list[dict]:
    fields = {**(spec or {}).get("value_fields", {}), **(spec or {}).get("extra_fields", {})}
    out = []
    for k, v in (ev or {}).items():
        if k in EVIDENCE_HIDDEN:
            continue
        text = _evidence_text(k, v, fields)
        if text:
            out.append({"label": EVIDENCE_RU.get(k, k), "text": text})
    return out


def fact_dict(f: Fact, specs: dict | None = None) -> dict:
    ev = dict(f.evidence or {})
    return {
        "id": f.id,
        "char_key": f.char_key,
        "source": f.source,
        "source_ru": SOURCE_RU.get(f.source, f.source),
        "source_detail": f.source_detail,
        "method": f.method,
        "summary": f.summary,
        "confidence": f.confidence,
        "observed_at": iso(f.observed_at),
        "valid_until": iso(f.valid_until),
        "stale": bool(f.valid_until and f.valid_until < utcnow()),
        "status": f.status,
        "evidence": ev,
        "evidence_view": evidence_view(ev, (specs or {}).get(f.char_key)),
        "value": _trim_value(f.value),
        "run_id": f.run_id,
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
    base = {
        "key": spec["key"],
        "title": spec["title"],
        "group": spec["group"],
        "scope": spec["scope"],
        "icon": spec.get("icon"),
        "description": spec.get("description", ""),
        "origin": spec.get("_origin", "builtin"),
    }
    if rv is None:
        return {
            **base,
            "status": "unknown",
            "summary": "",
            "confidence": 0.0,
            "sources": [],
            "value": None,
            "explanation": "Агент ещё не собирал эту характеристику",
            "updated_at": None,
            "valid_until": None,
            "stale": False,
        }
    return {
        **base,
        "status": rv.status,
        "summary": rv.summary,
        "confidence": rv.confidence,
        "sources": rv.sources or [],
        "sources_ru": [SOURCE_RU.get(s, s) for s in rv.sources or []],
        "value": _trim_value(rv.value),
        "explanation": rv.explanation,
        "updated_at": iso(rv.updated_at),
        "valid_until": iso(rv.valid_until),
        "stale": bool(rv.valid_until and rv.valid_until < utcnow()),
        "fact_ids": rv.fact_ids,
    }


def question_dict(q: Question, session: Session | None = None) -> dict:
    d = {
        "id": q.id,
        "hotel_id": q.hotel_id,
        "char_keys": q.char_keys,
        "text": q.text,
        "reason": q.reason,
        "status": q.status,
        "answer_text": q.answer_text,
        "parsed": q.parsed,
        "parse_method": q.parse_method,
        "answered_by": q.answered_by,
        "created_at": iso(q.created_at),
        "answered_at": iso(q.answered_at),
        "context": q.context,
    }
    if session is not None:
        h = session.get(Hotel, q.hotel_id)
        d["hotel_name"] = h.label if h else q.hotel_id
        specs = all_specs(session, enabled_only=False)
        d["titles"] = [specs[k]["title"] for k in q.char_keys or [] if k in specs]
        d["parsed_view"] = [
            {
                "title": specs[k]["title"],
                "method": "языковой моделью" if (v or {}).get("method") == "llm" else "правилами",
                "text": fields_text(specs[k], (v or {}).get("fields") or {}) or "ничего не извлечено",
            }
            for k, v in (q.parsed or {}).items()
            if k in specs
        ]
    return d


def conflict_dict(c: Conflict, session: Session) -> dict:
    facts = [session.get(Fact, i) for i in c.fact_ids or []]
    h = session.get(Hotel, c.hotel_id)
    specs = all_specs(session, enabled_only=False)
    return {
        "id": c.id,
        "hotel_id": c.hotel_id,
        "hotel_name": h.label if h else "",
        "char_key": c.char_key,
        "title": specs.get(c.char_key, {}).get("title", c.char_key),
        "field": c.field,
        "description": c.description,
        "status": c.status,
        "resolution": c.resolution,
        "resolved_by": c.resolved_by,
        "question_id": c.question_id,
        "created_at": iso(c.created_at),
        "resolved_at": iso(c.resolved_at),
        "facts": [fact_dict(f, specs) for f in facts if f is not None],
    }


def run_dict(r: AgentRun, steps: list[AgentStep] | None = None, session: Session | None = None) -> dict:
    d = {
        "id": r.id,
        "kind": r.kind,
        "trigger": r.trigger,
        "hotel_id": r.hotel_id,
        "char_keys": r.char_keys,
        "status": r.status,
        "summary": r.summary,
        "error": r.error,
        "params": r.params,
        "created_at": iso(r.created_at),
        "started_at": iso(r.started_at),
        "finished_at": iso(r.finished_at),
    }
    if session is not None and r.hotel_id:
        h = session.get(Hotel, r.hotel_id)
        d["hotel_name"] = h.label if h else None
    if steps is not None:
        d["steps"] = [
            {
                "idx": s.idx,
                "ts": iso(s.ts),
                "agent": s.agent,
                "kind": s.kind,
                "char_key": s.char_key,
                "message": s.message,
                "payload": s.payload,
                "duration_ms": s.duration_ms,
            }
            for s in steps
        ]
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
        siblings = [
            {"id": o.id, "label": o.label, "corpus": (o.building or {}).get("corpus")}
            for o in session.scalars(select(Hotel).where(Hotel.id != h.id))
            if (o.building or {}).get("complex_id") == b["complex_id"]
        ]
    return {
        "id": h.id,
        "name": h.label,
        "osm_name": h.name,
        "kind": h.kind,
        "stars": h.stars,
        "lat": h.lat,
        "lon": h.lon,
        "address": h.address,
        "website": h.website,
        "phone": h.phone,
        "osm": (h.osm_tags or {}).get("_osm"),
        "merged_from": (h.osm_tags or {}).get("_merged_from") or [],
        "complex": {"id": b["complex_id"], "corpus": b.get("corpus"), "siblings": siblings} if b.get("complex_id") else None,
        "building": {k: b.get(k) for k in ("levels", "levels_src", "height", "osm")},
        "groups": groups,
        "coverage": round(known / max(1, len(specs)), 2),
        "known": known,
        "total": len(specs),
    }
