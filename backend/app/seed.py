"""Начальная загрузка: отели региона и заранее собранное агентами знание (data/seed).

Если файла data/seed/seed.json.gz нет, агенты соберут всё сами в фоне при первом старте
(для 167 отелей это ~10 минут); с файлом демо готово сразу.
"""

from __future__ import annotations

import datetime as dt
import gzip
import json
import logging

from sqlalchemy import func, inspect, select, text
from sqlalchemy.orm import Session

from .config import settings
from .db import engine
from .geo.views import building_facades
from .knowledge.registry import invalidate, sync_builtin
from .models import AgentRun, AgentStep, Change, Characteristic, Conflict, Fact, Hotel, Question, ResolvedValue, SearchLog, utcnow
from .region import get_region

log = logging.getLogger(__name__)

SEED_TABLES = [
    ("characteristics_custom", Characteristic),
    ("facts", Fact),
    ("resolved_values", ResolvedValue),
    ("questions", Question),
    ("conflicts", Conflict),
    ("agent_runs", AgentRun),
    ("agent_steps", AgentStep),
    ("changes", Change),
    ("search_log", SearchLog),
]
DATE_FIELDS = {"observed_at", "valid_until", "created_at", "updated_at", "answered_at", "resolved_at", "started_at",
               "finished_at", "ts"}


def load_hotels(session: Session) -> int:
    rd = get_region()
    n = 0
    for h in rd.hotels:
        if session.get(Hotel, h["id"]) is not None:
            continue
        b_idx = h.get("building")
        building = {"idx": b_idx, "x": h["x"], "y": h["y"]}
        if b_idx is not None:
            b = rd.buildings[b_idx]
            building.update({"osm": b.osm, "levels": b.levels, "levels_src": b.levels_src, "height": b.height,
                             "height_src": b.height_src,
                             "facades": [f.as_dict() for f in building_facades(b.poly)]})
        tags = dict(h.get("tags") or {})
        tags["_osm"] = h["osm"]
        session.add(Hotel(id=h["id"], name=h["name"], kind=h["kind"], stars=h.get("stars"), lat=h["lat"], lon=h["lon"],
                          address=h.get("address"), website=h.get("website"), phone=h.get("phone"), osm_tags=tags,
                          building=building))
        n += 1
    session.flush()
    return n


def _row_to_dict(obj) -> dict:
    out = {}
    for c in inspect(obj).mapper.column_attrs:
        v = getattr(obj, c.key)
        if isinstance(v, dt.datetime):
            v = v.isoformat()
        out[c.key] = v
    return out


def dump_seed(session: Session, path) -> dict:
    doc = {"version": 1, "built_at": utcnow().isoformat()}
    for name, model in SEED_TABLES:
        stmt = select(model)
        if model is Characteristic:
            stmt = stmt.where(Characteristic.origin == "custom")
        doc[name] = [_row_to_dict(o) for o in session.scalars(stmt)]
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
    return {k: len(v) for k, v in doc.items() if isinstance(v, list)}


def _parse(row: dict) -> dict:
    out = {}
    for k, v in row.items():
        if k in DATE_FIELDS and isinstance(v, str):
            v = dt.datetime.fromisoformat(v)
        out[k] = v
    return out


def load_seed(session: Session, path, shift_to_now: bool = True) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        doc = json.load(f)
    # Сдвигаем даты так, будто сбор был только что: иначе демо через год «протухнет» целиком.
    shift = dt.timedelta(0)
    if shift_to_now and doc.get("built_at"):
        shift = utcnow() - dt.datetime.fromisoformat(doc["built_at"])
    counts = {}
    for name, model in SEED_TABLES:
        rows = doc.get(name, [])
        objs = []
        for r in rows:
            r = _parse(r)
            for k in DATE_FIELDS:
                if isinstance(r.get(k), dt.datetime):
                    r[k] = r[k] + shift
            objs.append(model(**r))
        session.add_all(objs)
        session.flush()
        counts[name] = len(objs)
    if engine.dialect.name == "postgresql":
        for table in ["facts", "questions", "conflicts", "agent_runs", "agent_steps", "changes", "search_log"]:
            session.execute(text(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                                 f"COALESCE((SELECT MAX(id) FROM {table}), 1))"))
    invalidate()
    return counts


def bootstrap(session: Session) -> str:
    sync_builtin(session)
    n_hotels = session.scalar(select(func.count(Hotel.id))) or 0
    if n_hotels == 0:
        added = load_hotels(session)
        log.info("Загружено отелей: %d", added)
    has_facts = (session.scalar(select(func.count(Fact.id))) or 0) > 0
    if has_facts:
        return "existing"
    seed_path = settings.data_dir / "seed" / "seed.json.gz"
    if settings.seed_on_start and seed_path.exists():
        counts = load_seed(session, seed_path)
        log.info("Загружено знание агентов из %s: %s", seed_path, counts)
        return "seed"
    from .agents.orchestrator import create_run

    create_run(session, "collect_all", trigger="bootstrap", params={"note": "первичный сбор без готового seed"})
    log.info("Готового seed нет — агенты начнут сбор в фоне")
    return "bootstrap"
