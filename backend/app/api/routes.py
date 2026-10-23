"""HTTP API (FastAPI)."""

from __future__ import annotations

import datetime as dt
import math
import threading
from collections import OrderedDict

import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from .. import llm
from ..agents import arbiter, concierge, orchestrator, wish
from ..agents.compiler import compile_text
from ..agents.dedup import short_street
from ..agents.interviewer import parse_with_llm, render_summary
from ..config import settings
from ..db import get_session
from ..geo.analysis import HotelGeo
from ..geo.sun import monthly_sunsets
from ..knowledge.registry import GROUPS, all_specs, get_spec, invalidate
from ..models import (
    AgentRun,
    AgentStep,
    Change,
    Characteristic,
    Conflict,
    Fact,
    GeoOverride,
    Hotel,
    Question,
    ResolvedValue,
    SearchLog,
    utcnow,
)
from ..nlp.answer_parser import parse_answer
from ..nlp.ru import dist_text
from ..region import get_region
from ..tools.base import load_all
from .common import check_token, conflict_dict, fact_dict, hotel_card, iso, make_token, question_dict, run_dict, rv_dict

router = APIRouter()


def _enqueue(session: Session, kind: str, **kw) -> AgentRun:
    run = orchestrator.create_run(session, kind, **kw)
    session.commit()
    if not settings.worker_enabled:
        orchestrator.execute_run(run.id)
        session.expire_all()
        run = session.get(AgentRun, run.id)
    return run


def _hotel_or_404(session: Session, hotel_id: str) -> Hotel:
    h = session.get(Hotel, hotel_id)
    if h is None:
        raise HTTPException(404, "Отель не найден")
    return h


# =============================================================== служебное
@router.get("/health")
def health(session: Session = Depends(get_session)):
    session.execute(select(1))
    return {"status": "ok"}


@router.get("/api/meta")
def meta(session: Session = Depends(get_session)):
    rd = get_region()
    specs = all_specs(session)
    hotels = session.scalar(select(func.count(Hotel.id)))
    return {
        "region": {"name": rd.meta["name"], "bbox": rd.meta["hotels_bbox"], "origin": rd.meta["origin"],
                   "sources": rd.meta["sources"], "stats": rd.meta.get("stats", {}), "center": rd.meta.get("center")},
        "hotels": hotels, "characteristics": len(specs), "groups": GROUPS, "llm": llm.status(),
        "tools": [t.describe() for t in load_all().values()],
        "examples": [
            "тихий номер с видом на закат недалеко от моря",
            "с собакой, чтобы рядом был парк для выгула",
            "с детьми: детская площадка рядом и пляж без лестниц",
            "в гуще событий, бары рядом и аптека, которая работает ночью",
            "вид на горы, тихо, обязательно парковка",
            "хочу видеть закат над морем в октябре и ещё искупаться",
            "первая линия и не переходить дорогу до пляжа",
            "исторический особняк в центре",
            "выспаться после ночного перелёта: плотные шторы и тишина",
            "удалённо поработать: рабочий стол и быстрый интернет",
        ],
    }


@router.get("/api/region/sea")
def region_sea():
    import json

    path = get_region().folder / "sea.geojson"
    if not path.exists():
        raise HTTPException(404, "нет полигона моря")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# =============================================================== отели
@router.get("/api/hotels")
def hotels(q: str = "", limit: int = 400, session: Session = Depends(get_session)):
    stmt = select(Hotel).order_by(Hotel.name)
    rows = list(session.scalars(stmt))
    if q:
        ql = q.lower()
        rows = [h for h in rows if ql in h.label.lower() or ql in (h.address or "").lower()]
    rv_count = dict(session.execute(select(ResolvedValue.hotel_id, func.count()).where(
        ResolvedValue.status.in_(["ok", "conflict", "stale"])).group_by(ResolvedValue.hotel_id)).all())
    total = len(all_specs(session))
    keys = ["sea_view", "sunset_view", "quietness", "beach_access"]
    hl = {}
    for rv in session.scalars(select(ResolvedValue).where(ResolvedValue.char_key.in_(keys))):
        hl.setdefault(rv.hotel_id, {})[rv.char_key] = rv
    out = []
    for h in rows[:limit]:
        r = hl.get(h.id, {})
        sv, ss, qn, ba = r.get("sea_view"), r.get("sunset_view"), r.get("quietness"), r.get("beach_access")
        out.append({
            "id": h.id, "name": h.label, "kind": h.kind, "stars": h.stars, "lat": h.lat, "lon": h.lon, "address": h.address,
            "levels": (h.building or {}).get("levels"), "coverage": round(rv_count.get(h.id, 0) / max(1, total), 2),
            "badges": {
                "sea_view": bool(sv and sv.value and sv.value.get("available")),
                "sunset": bool(ss and ss.value and ss.value.get("available")),
                "quiet_db": (qn.value or {}).get("night_db") if qn else None,
                "beach_min": (ba.value or {}).get("minutes_to") if ba else None,
            },
        })
    return {"items": out, "total": len(rows)}


@router.get("/api/hotels/{hotel_id}")
def hotel(hotel_id: str, session: Session = Depends(get_session)):
    h = _hotel_or_404(session, hotel_id)
    card = hotel_card(session, h)
    card["questions"] = [question_dict(q) for q in session.scalars(
        select(Question).where(Question.hotel_id == h.id).order_by(desc(Question.id)).limit(30))]
    card["conflicts"] = [conflict_dict(c, session) for c in session.scalars(
        select(Conflict).where(Conflict.hotel_id == h.id).order_by(desc(Conflict.id)).limit(20))]
    card["runs"] = [run_dict(r) for r in session.scalars(
        select(AgentRun).where(AgentRun.hotel_id == h.id).order_by(desc(AgentRun.id)).limit(10))]
    card["changes"] = [{"char_key": c.char_key, "old": c.old_summary, "new": c.new_summary, "reason": c.reason,
                        "at": iso(c.created_at)} for c in session.scalars(
        select(Change).where(Change.hotel_id == h.id).order_by(desc(Change.id)).limit(20))]
    return card


@router.get("/api/hotels/{hotel_id}/facts")
def hotel_facts(hotel_id: str, char_key: str | None = None, session: Session = Depends(get_session)):
    stmt = select(Fact).where(Fact.hotel_id == hotel_id).order_by(desc(Fact.id))
    if char_key:
        stmt = stmt.where(Fact.char_key == char_key)
    return {"items": [fact_dict(f) for f in session.scalars(stmt.limit(200))]}


_geo_cache: OrderedDict[tuple, HotelGeo] = OrderedDict()
_geo_lock = threading.Lock()


def _hotel_geo(session: Session, h: Hotel) -> HotelGeo:
    rv = session.get(ResolvedValue, (h.id, "building_levels"))
    levels = int(rv.value["levels"]) if rv and rv.value and rv.value.get("levels") else (h.building or {}).get("levels")
    src = (rv.sources[0] if rv and rv.sources else (h.building or {}).get("levels_src", "none"))
    key = (h.id, levels, len(get_region()._overrides_applied))
    with _geo_lock:
        if key in _geo_cache:
            _geo_cache.move_to_end(key)
            return _geo_cache[key]
    b = h.building or {}
    g = HotelGeo(get_region(), {"x": b.get("x"), "y": b.get("y"), "building": b.get("idx")}, levels=levels, levels_src=src)
    with _geo_lock:
        _geo_cache[key] = g
        while len(_geo_cache) > 24:
            _geo_cache.popitem(last=False)
    return g


@router.get("/api/hotels/{hotel_id}/geo")
def hotel_geo(hotel_id: str, session: Session = Depends(get_session)):
    h = _hotel_or_404(session, hotel_id)
    rd = get_region()
    b = h.building or {}
    out: dict = {"id": h.id, "lat": h.lat, "lon": h.lon, "footprint": None, "facades": [], "routes": {}}
    if b.get("idx") is not None:
        ring = np.asarray(rd.buildings[b["idx"]].poly.exterior.coords)
        lon, lat = rd.lonlat(ring[:, 0], ring[:, 1])
        out["footprint"] = [[round(float(a), 6), round(float(c), 6)] for a, c in zip(lon, lat)]
    rvs = {k: session.get(ResolvedValue, (h.id, k)) for k in ["sea_view", "sunset_view", "quietness", "mountain_view",
                                                               "window_outlook", "beach_access", "center_access"]}
    facades = (b.get("facades") or [])
    for f in facades:
        lon, lat = rd.lonlat(f["x"], f["y"])
        item = {"key": f["key"], "normal_az": f["normal_az"], "lat": float(lat), "lon": float(lon)}
        for k in ["sea_view", "sunset_view", "quietness", "mountain_view", "window_outlook"]:
            rv = rvs.get(k)
            fv = ((rv.value or {}).get("by_facade") or {}).get(f["key"]) if rv else None
            if fv:
                item[k] = concierge.facade_text(k, fv, f["key"])
                if k == "quietness":
                    item["night_db"] = fv.get("night_db")
                if k == "sea_view":
                    item["sea"] = fv.get("available")
                if k == "sunset_view":
                    item["sunset"] = fv.get("available")
        out["facades"].append(item)
    for k in ["beach_access", "center_access"]:
        rv = rvs.get(k)
        if rv and rv.value and rv.value.get("line"):
            out["routes"][k] = {"line": rv.value["line"], "profile": rv.value.get("profile"),
                                "summary": rv.summary}
    return out


@router.get("/api/hotels/{hotel_id}/fit")
def hotel_fit(hotel_id: str, q: str, session: Session = Depends(get_session)):
    """Как отель подходит под запрос: статусы пожеланий, совет по номеру, доля подходящих номеров."""
    res = concierge.fit(session, hotel_id, q.strip())
    if res is None:
        raise HTTPException(404, "Отель не найден")
    return res


@router.get("/api/hotels/{hotel_id}/wish")
def hotel_wish(hotel_id: str, q: str, session: Session = Depends(get_session)):
    """Пожелание к бронированию: готовый текст, почему так и вопросы о том, чего система не знает."""
    res = wish.build(session, hotel_id, q.strip())
    if res is None:
        raise HTTPException(404, "Отель не найден")
    return res


@router.get("/api/hotels/{hotel_id}/view")
def hotel_view(hotel_id: str, facade: str | None = None, floor: int | None = None,
               session: Session = Depends(get_session)):
    """Панорама из окна: профиль горизонта, что его образует, где море и где садится солнце по месяцам."""
    h = _hotel_or_404(session, hotel_id)
    g = _hotel_geo(session, h)
    view = g.view
    if not view:
        raise HTTPException(404, "Нет фасадов для расчёта")
    fkey = facade if facade in view else next(iter(view))
    v = view[fkey]
    floors = sorted(v["floors"])
    fl = floor if floor in v["floors"] else floors[min(len(floors) - 1, 2)]
    r = v["floors"][fl]
    lon, lat = g.lonlat
    suns = monthly_sunsets(lat, lon)
    return {
        "facade": fkey, "facades": [{"key": k, "normal_az": round(x["facade"].normal_az, 1)} for k, x in view.items()],
        "normal_az": round(v["facade"].normal_az, 1), "floor": fl, "floors": floors, "window_half": 60,
        "azimuths": [round(float(a), 1) for a in v["az"]],
        "horizon": [round(float(x), 2) for x in r["horizon"]],
        "sky_cls": [int(x) for x in r["sky_cls"]],
        "sky_d": [round(float(x)) for x in r["sky_d"]],
        "sea_vis": [bool(x) for x in r["sea_vis"]],
        "suns": [{"month": s["month"], "azimuth": s["azimuth"], "time": s["local_time"],
                  "path": [[m, el, az] for m, el, az in s["path"]]} for s in suns],
        "eye_z": round(float(r["eye_z"]), 1), "ground": round(g.ground, 1),
        "classes": {"0": "рельеф", "1": "море", "2": "здания", "3": "деревья", "4": "горы"},
        **_rays_payload(session, g, fkey, fl),
    }


RAY_KIND_RU = {"sea": "море", "trees": "деревья", "land": "склоны и холмы", "mountain": "горы"}


def _rays_payload(session: Session, g: HotelGeo, fkey: str, floor: int) -> dict:
    """Лучи из окна для карты: куда упирается каждый луч, здания-заслоны с контурами и сводка по сектору окна."""
    rd = get_region()
    f = g.view[fkey]["facade"]
    res = g.rays(fkey, floor)
    wlon, wlat = rd.lonlat(f.x, f.y)
    blockers = []
    for b in res["blockers"][:8]:
        if b["deg"] < 2:
            continue
        bid = b["id"]
        if bid <= -1000:  # здание из сценария «построили дом»
            ov = session.get(GeoOverride, -1000 - bid)
            if ov is None:
                continue
            ring = [[lat, lon] for lon, lat in ov.geometry["coordinates"][0]]
            levels, height, street, new = round(ov.height_m / 3), ov.height_m, None, True
            what = f"новый дом в {dist_text(b['dist_m'])} от окна ({levels} эт.)"
        else:
            bld = rd.buildings[bid]
            lon, lat = rd.lonlat(*np.asarray(bld.poly.exterior.coords).T)
            ring = [[round(float(a), 6), round(float(o), 6)] for a, o in zip(lat, lon)]
            c = bld.poly.centroid
            street = rd.nearest_street(c.x, c.y)
            levels, height, street, new = bld.levels, bld.height, short_street(street) if street else None, False
            size = f"{levels} эт." if levels else f"≈{height:.0f} м"
            what = f"дом в {dist_text(b['dist_m'])} от окна ({size}" + (f", {street})" if street else ")")
        blockers.append({"id": bid, "deg": b["deg"], "dist_m": round(b["dist_m"]), "levels": levels,
                         "height_m": round(float(height or 0), 1), "street": street, "new": new, "label": what,
                         "polygon": ring})
    parts = [(n, f"{n}° — {RAY_KIND_RU[k]}") for k, n in res["counts"].items() if n and k != "building"]
    shown = 0
    for b in blockers[:2]:
        parts.append((b["deg"], f"{b['deg']}° — {b['label']}"))
        shown += b["deg"]
    if res["counts"]["building"] - shown > 0:
        rest = res["counts"]["building"] - shown
        parts.append((rest, f"{rest}° — " + ("другие дома" if shown else "дома")))
    caption = "Из 120° обзора: " + ", ".join(t for _, t in sorted(parts, key=lambda p: -p[0]))
    return {"window": {"lat": round(float(wlat), 6), "lon": round(float(wlon), 6)}, "rays": res["rays"],
            "blockers": blockers, "ray_counts": res["counts"], "rays_caption": caption}


class CollectBody(BaseModel):
    char_keys: list[str] | None = None


@router.post("/api/hotels/{hotel_id}/collect")
def hotel_collect(hotel_id: str, body: CollectBody, session: Session = Depends(get_session)):
    _hotel_or_404(session, hotel_id)
    run = _enqueue(session, "collect", hotel_id=hotel_id, char_keys=body.char_keys or [], trigger="manual")
    return run_dict(run)


# =============================================================== запуски агентов
@router.get("/api/runs")
def runs(hotel_id: str | None = None, kind: str | None = None, limit: int = 30, session: Session = Depends(get_session)):
    stmt = select(AgentRun).order_by(desc(AgentRun.id)).limit(limit)
    if hotel_id:
        stmt = stmt.where(AgentRun.hotel_id == hotel_id)
    if kind:
        stmt = stmt.where(AgentRun.kind == kind)
    return {"items": [run_dict(r, session=session) for r in session.scalars(stmt)]}


@router.get("/api/runs/{run_id}")
def run_detail(run_id: int, after: int = -1, session: Session = Depends(get_session)):
    r = session.get(AgentRun, run_id)
    if r is None:
        raise HTTPException(404, "Запуск не найден")
    steps = list(session.scalars(select(AgentStep).where(AgentStep.run_id == run_id, AgentStep.idx > after)
                                 .order_by(AgentStep.idx).limit(2000)))
    return run_dict(r, steps, session=session)


# =============================================================== поиск и сравнение
class SearchBody(BaseModel):
    query: str = ""
    criteria: list[dict] | None = None
    limit: int = 30


@router.post("/api/search")
def do_search(body: SearchBody, session: Session = Depends(get_session)):
    if not body.query.strip() and not body.criteria:
        raise HTTPException(400, "Опишите, что для вас важно")
    return concierge.search(session, body.query.strip(), criteria_override=body.criteria, limit=body.limit)


@router.get("/api/compare")
def do_compare(ids: str, q: str = "", session: Session = Depends(get_session)):
    hotel_ids = [i for i in ids.split(",") if i][:5]
    if len(hotel_ids) < 2:
        raise HTTPException(400, "Выберите хотя бы два отеля")
    return concierge.compare(session, hotel_ids, q.strip() or None)


# =============================================================== характеристики
@router.get("/api/characteristics")
def characteristics(session: Session = Depends(get_session)):
    specs = all_specs(session, enabled_only=False)
    n_hotels = session.scalar(select(func.count(Hotel.id))) or 1
    cov = dict(session.execute(select(ResolvedValue.char_key, func.count()).where(
        ResolvedValue.status.in_(["ok", "conflict", "stale"])).group_by(ResolvedValue.char_key)).all())
    conf = dict(session.execute(select(ResolvedValue.char_key, func.avg(ResolvedValue.confidence)).where(
        ResolvedValue.status.in_(["ok", "conflict", "stale"])).group_by(ResolvedValue.char_key)).all())
    tools = load_all()
    out = []
    for k, s in specs.items():
        out.append({
            "key": k, "title": s["title"], "group": s["group"], "scope": s["scope"], "icon": s.get("icon"),
            "description": s.get("description", ""), "enabled": s.get("_enabled", True), "origin": s.get("_origin"),
            "compiled_by": s.get("_compiled_by"), "created_from_text": s.get("_created_from_text"),
            "recipe": [{**step, "title": tools[step["tool"]].title if step["tool"] in tools else step["tool"],
                        "agent": tools[step["tool"]].agent if step["tool"] in tools else ""} for step in s.get("recipe", [])],
            "ttl_days": s.get("ttl_days"), "confidence_threshold": s.get("confidence_threshold"),
            "value_fields": s.get("value_fields"), "search_phrases": (s.get("search") or {}).get("phrases", [])[:8],
            "question": (s.get("question") or {}).get("text"), "conflict_policy": (s.get("conflict") or {}).get("policy"),
            "coverage": round(cov.get(k, 0) / n_hotels, 2), "avg_confidence": round(float(conf.get(k) or 0), 2),
            "version": s.get("_version"),
        })
    return {"items": out, "groups": GROUPS}


class CompileBody(BaseModel):
    text: str


@router.post("/api/characteristics/compile")
def characteristic_compile(body: CompileBody, session: Session = Depends(get_session)):
    if len(body.text.strip()) < 3:
        raise HTTPException(400, "Опишите характеристику словами")
    spec, method, errs = compile_text(body.text, set(all_specs(session, enabled_only=False)))
    return {"spec": spec, "method": method, "errors": errs}


class CreateBody(BaseModel):
    text: str
    spec: dict | None = None


@router.post("/api/characteristics")
def characteristic_create(body: CreateBody, session: Session = Depends(get_session)):
    existing = set(all_specs(session, enabled_only=False))
    if body.spec:
        from ..agents.compiler import _validate
        from ..knowledge.registry import normalize

        spec = normalize(body.spec)
        if spec.get("key") in existing or not spec.get("key"):
            spec["key"] = compile_text(body.text, existing)[0]["key"]
        errs = _validate(spec)
        method = "edited"
    else:
        spec, method, errs = compile_text(body.text, existing)
    if errs:
        raise HTTPException(400, "; ".join(errs))
    session.add(Characteristic(key=spec["key"], title=spec["title"], group=spec.get("group", "custom"),
                               scope=spec.get("scope", "hotel"), description=spec.get("description", ""), spec=spec,
                               origin="custom", created_from_text=body.text, compiled_by=method))
    session.flush()
    invalidate()
    run = _enqueue(session, "collect_all", char_keys=[spec["key"]], trigger="new_characteristic",
                   params={"title": spec["title"]})
    return {"spec": spec, "method": method, "run": run_dict(run)}


@router.post("/api/characteristics/{key}/toggle")
def characteristic_toggle(key: str, session: Session = Depends(get_session)):
    row = session.get(Characteristic, key)
    if row is None:
        raise HTTPException(404, "Нет такой характеристики")
    row.enabled = not row.enabled
    session.flush()
    invalidate()
    return {"key": key, "enabled": row.enabled}


@router.post("/api/characteristics/{key}/collect")
def characteristic_collect(key: str, session: Session = Depends(get_session)):
    if get_spec(session, key) is None:
        raise HTTPException(404, "Нет такой характеристики")
    run = _enqueue(session, "collect_all", char_keys=[key], trigger="manual")
    return run_dict(run)


# =============================================================== вопросы и противоречия
@router.get("/api/questions")
def questions(status: str | None = None, hotel_id: str | None = None, limit: int = 100,
              session: Session = Depends(get_session)):
    stmt = select(Question).order_by(desc(Question.id)).limit(limit)
    if status:
        stmt = stmt.where(Question.status == status)
    if hotel_id:
        stmt = stmt.where(Question.hotel_id == hotel_id)
    return {"items": [question_dict(q, session) for q in session.scalars(stmt)]}


@router.post("/api/questions/{qid}/simulate")
def question_simulate(qid: int, session: Session = Depends(get_session)):
    q = session.get(Question, qid)
    if q is None or q.status != "open":
        raise HTTPException(400, "Вопрос уже закрыт или не найден")
    run = orchestrator.run_now(session, "answer", hotel_id=q.hotel_id, char_keys=q.char_keys, trigger="simulator",
                               params={"question_id": q.id})
    steps = list(session.scalars(select(AgentStep).where(AgentStep.run_id == run.id).order_by(AgentStep.idx)))
    session.expire_all()
    return {"run": run_dict(run, steps), "question": question_dict(session.get(Question, qid), session)}


@router.get("/api/conflicts")
def conflicts(status: str | None = None, limit: int = 100, session: Session = Depends(get_session)):
    stmt = select(Conflict).order_by(desc(Conflict.id)).limit(limit)
    if status:
        stmt = stmt.where(Conflict.status == status)
    return {"items": [conflict_dict(c, session) for c in session.scalars(stmt)]}


class ResolveBody(BaseModel):
    keep_fact_id: int
    note: str = ""


@router.post("/api/conflicts/{cid}/resolve")
def conflict_resolve(cid: int, body: ResolveBody, session: Session = Depends(get_session)):
    c = session.get(Conflict, cid)
    if c is None or c.status != "open":
        raise HTTPException(400, "Противоречие не найдено или уже разобрано")
    if body.keep_fact_id not in (c.fact_ids or []):
        raise HTTPException(400, "Этот факт не участвует в противоречии")
    spec = get_spec(session, c.char_key)
    kept = session.get(Fact, body.keep_fact_id)
    arbiter.resolve_conflict(session, c, body.keep_fact_id, "moderator",
                             body.note or f"модератор выбрал источник: {arbiter.SOURCE_RU.get(kept.source, kept.source)}", spec)
    for q in session.scalars(select(Question).where(Question.hotel_id == c.hotel_id, Question.status == "open")):
        if c.char_key in (q.char_keys or []) and q.reason == "conflict":
            q.status = "closed"
    return conflict_dict(c, session)


@router.get("/api/changes")
def changes(limit: int = 50, session: Session = Depends(get_session)):
    specs = all_specs(session, enabled_only=False)
    out = []
    for c in session.scalars(select(Change).order_by(desc(Change.id)).limit(limit)):
        h = session.get(Hotel, c.hotel_id)
        out.append({"id": c.id, "hotel_id": c.hotel_id, "hotel_name": h.label if h else "", "char_key": c.char_key,
                    "title": specs.get(c.char_key, {}).get("title", c.char_key), "old": c.old_summary,
                    "new": c.new_summary, "reason": c.reason, "at": iso(c.created_at), "run_id": c.run_id})
    return {"items": out}


# =============================================================== агенты, актуализация, сценарии
@router.get("/api/agents/overview")
def agents_overview(session: Session = Depends(get_session)):
    now = utcnow()
    runs_by_status = dict(session.execute(select(AgentRun.status, func.count()).group_by(AgentRun.status)).all())
    q_by_status = dict(session.execute(select(Question.status, func.count()).group_by(Question.status)).all())
    c_by_status = dict(session.execute(select(Conflict.status, func.count()).group_by(Conflict.status)).all())
    stale = session.scalar(select(func.count()).select_from(ResolvedValue).where(
        ResolvedValue.valid_until.is_not(None), ResolvedValue.valid_until < now)) or 0
    soon = session.scalar(select(func.count()).select_from(ResolvedValue).where(
        ResolvedValue.valid_until.is_not(None), ResolvedValue.valid_until >= now,
        ResolvedValue.valid_until < now + dt.timedelta(days=30))) or 0
    facts_by_source = dict(session.execute(select(Fact.source, func.count()).where(Fact.status == "active")
                                           .group_by(Fact.source)).all())
    tools = load_all()
    agents: dict[str, dict] = {}
    for t in tools.values():
        a = agents.setdefault(t.agent, {"name": t.agent, "tools": []})
        a["tools"].append({"name": t.name, "title": t.title, "sources": t.sources})
    agents.setdefault("Оркестратор", {"name": "Оркестратор", "tools": [],
                                      "role": "планирует сбор по рецептам и зависимостям, ведёт очередь запусков"})
    agents.setdefault("Арбитр", {"name": "Арбитр", "tools": [],
                                 "role": "сводит источники, ловит противоречия, следит за сроком годности"})
    agents.setdefault("Консьерж", {"name": "Консьерж", "tools": [],
                                   "role": "понимает запрос гостя, подбирает сторону здания и этаж, объясняет выбор"})
    agents.setdefault("Компилятор", {"name": "Компилятор", "tools": [],
                                     "role": "превращает текстовое описание новой характеристики в спецификацию"})
    agents.setdefault("Дедупликатор", {"name": "Дедупликатор", "tools": [],
                                       "role": "склеивает отель, нанесённый в OSM дважды, связывает корпуса комплексов, "
                                               "подписывает тёзок по улице"})
    overrides = [{"id": o.id, "label": o.label, "height_m": o.height_m, "active": o.active, "geometry": o.geometry,
                  "created_at": iso(o.created_at)} for o in session.scalars(select(GeoOverride).order_by(desc(GeoOverride.id)))]
    return {"runs": runs_by_status, "questions": q_by_status, "conflicts": c_by_status, "stale": stale,
            "stale_soon": soon, "facts_by_source": facts_by_source, "agents": list(agents.values()),
            "overrides": overrides, "llm": llm.status(), "autoreply": settings.hotelier_autoreply,
            "worker": settings.worker_enabled, "actualize_interval_min": settings.actualize_interval_min}


@router.post("/api/agents/actualize")
def agents_actualize(session: Session = Depends(get_session)):
    return run_dict(_enqueue(session, "actualize", trigger="manual", params={"limit": 50}))


class AgeBody(BaseModel):
    days: int = 200
    char_keys: list[str] | None = None


@router.post("/api/agents/age")
def agents_age(body: AgeBody, session: Session = Depends(get_session)):
    """Демо: «состарить» данные, чтобы увидеть, как агенты находят и перепроверяют устаревшее."""
    delta = dt.timedelta(days=body.days)
    stmt = select(Fact).where(Fact.status == "active")
    if body.char_keys:
        stmt = stmt.where(Fact.char_key.in_(body.char_keys))
    n = 0
    for f in session.scalars(stmt):
        f.observed_at = f.observed_at - delta
        f.valid_until = f.valid_until - delta
        n += 1
    stmt = select(ResolvedValue)
    if body.char_keys:
        stmt = stmt.where(ResolvedValue.char_key.in_(body.char_keys))
    stale = 0
    for rv in session.scalars(stmt):
        if rv.valid_until:
            rv.valid_until = rv.valid_until - delta
            if rv.valid_until < utcnow() and rv.status == "ok":
                rv.status = "stale"
                stale += 1
    return {"facts_aged": n, "became_stale": stale}


class BuildingScenario(BaseModel):
    lat: float
    lon: float
    height_m: float = 60
    width_m: float = 30
    length_m: float = 50
    angle_deg: float = 0
    label: str = "Новый жилой комплекс"


@router.post("/api/scenarios/building")
def scenario_building(body: BuildingScenario, session: Session = Depends(get_session)):
    rd = get_region()
    x, y = rd.xy(body.lon, body.lat)
    a = math.radians(body.angle_deg)
    hw, hl = body.width_m / 2, body.length_m / 2
    corners = []
    for dx, dy in [(-hw, -hl), (hw, -hl), (hw, hl), (-hw, hl), (-hw, -hl)]:
        cx = float(x) + dx * math.cos(a) - dy * math.sin(a)
        cy = float(y) + dx * math.sin(a) + dy * math.cos(a)
        lon, lat = rd.lonlat(cx, cy)
        corners.append([round(float(lon), 7), round(float(lat), 7)])
    ov = GeoOverride(label=body.label, geometry={"type": "Polygon", "coordinates": [corners]}, height_m=body.height_m)
    session.add(ov)
    session.flush()
    run = _enqueue(session, "scenario", trigger="change:building", params={"override_id": ov.id, "radius_m": 1200,
                                                                         "label": body.label})
    return {"override": {"id": ov.id, "geometry": ov.geometry, "height_m": ov.height_m, "label": ov.label},
            "run": run_dict(run)}


@router.delete("/api/scenarios")
def scenario_reset(session: Session = Depends(get_session)):
    ids = [o.id for o in session.scalars(select(GeoOverride).where(GeoOverride.active.is_(True)))]
    for o in session.scalars(select(GeoOverride)):
        o.active = False
    session.flush()
    get_region().reset_overrides()
    run = None
    if ids:
        run = _enqueue(session, "scenario", trigger="change:reset", params={"reset_ids": ids, "radius_m": 1200})
    return {"reset": ids, "run": run_dict(run) if run else None}


# =============================================================== кабинет отельера
class LoginBody(BaseModel):
    login: str
    password: str


@router.post("/api/auth/login")
def login(body: LoginBody):
    if body.login != settings.hotelier_login or body.password != settings.hotelier_password:
        raise HTTPException(401, "Неверный логин или пароль")
    return {"token": make_token(body.login), "login": body.login}


@router.get("/api/hotelier/hotels")
def hotelier_hotels(q: str = "", user: str = Depends(check_token), session: Session = Depends(get_session)):
    rows = list(session.scalars(select(Hotel).order_by(Hotel.name)))
    if q:
        rows = [h for h in rows if q.lower() in h.label.lower()]
    open_q = dict(session.execute(select(Question.hotel_id, func.count()).where(Question.status == "open")
                                  .group_by(Question.hotel_id)).all())
    return {"items": [{"id": h.id, "name": h.label, "kind": h.kind, "address": h.address,
                       "open_questions": open_q.get(h.id, 0)} for h in rows]}


def _strengths(session: Session, h: Hotel) -> list[dict]:
    specs = all_specs(session)
    out = []
    for key in ["sea_view", "sunset_view", "quietness", "beach_access", "mountain_view", "nightlife", "families",
                "center_access", "night_services"]:
        if key not in specs:
            continue
        rows = list(session.scalars(select(ResolvedValue).where(ResolvedValue.char_key == key)))
        scores = {rv.hotel_id: concierge._cell_score(key, rv) for rv in rows}
        mine = scores.get(h.id)
        vals = [s for s in scores.values() if s is not None]
        if mine is None or len(vals) < 5:
            continue
        pct = sum(1 for s in vals if s < mine) / len(vals)
        rv = session.get(ResolvedValue, (h.id, key))
        if pct >= 0.6 and rv and rv.summary:
            out.append({"key": key, "title": specs[key]["title"], "percentile": round(pct * 100), "summary": rv.summary})
    out.sort(key=lambda x: -x["percentile"])
    return out[:5]


@router.get("/api/hotelier/hotels/{hotel_id}")
def hotelier_hotel(hotel_id: str, user: str = Depends(check_token), session: Session = Depends(get_session)):
    h = _hotel_or_404(session, hotel_id)
    card = hotel_card(session, h)
    card["questions"] = [question_dict(q, session) for q in session.scalars(
        select(Question).where(Question.hotel_id == h.id).order_by(Question.status.desc(), desc(Question.id)))]
    card["conflicts"] = [conflict_dict(c, session) for c in session.scalars(
        select(Conflict).where(Conflict.hotel_id == h.id, Conflict.status == "open"))]
    card["strengths"] = _strengths(session, h)
    logs = list(session.scalars(select(SearchLog).order_by(desc(SearchLog.id)).limit(300)))
    seen = [{"query": s.query, "at": iso(s.created_at), "rank": s.top_hotels.index(h.id) + 1}
            for s in logs if h.id in (s.top_hotels or [])]
    card["search_appearances"] = {"count": len(seen), "items": seen[:10], "searches": len(logs)}
    return card


class AnswerBody(BaseModel):
    text: str
    preview: bool = False


@router.post("/api/hotelier/questions/{qid}/answer")
def hotelier_answer(qid: int, body: AnswerBody, user: str = Depends(check_token), session: Session = Depends(get_session)):
    q = session.get(Question, qid)
    if q is None:
        raise HTTPException(404, "Вопрос не найден")
    if body.preview:
        out = {}
        for key in q.char_keys or []:
            spec = get_spec(session, key)
            parsed = parse_with_llm(spec, q.text, body.text) if llm.enabled() else None
            method = "llm"
            if not parsed or not parsed["fields"]:
                parsed = parse_answer(spec, body.text, (q.context or {}).get("fields"))
                method = "rules"
            out[key] = {**parsed, "method": method, "summary": render_summary(spec, parsed["fields"]) if parsed["fields"] else ""}
        return {"preview": out}
    run = orchestrator.create_run(session, "answer", hotel_id=q.hotel_id, char_keys=q.char_keys, trigger="hotelier",
                                  params={"question_id": q.id, "text": body.text, "answered_by": "hotelier"})
    session.commit()
    orchestrator.execute_run(run.id)
    session.expire_all()
    run = session.get(AgentRun, run.id)
    steps = list(session.scalars(select(AgentStep).where(AgentStep.run_id == run.id).order_by(AgentStep.idx)))
    return {"run": run_dict(run, steps), "question": question_dict(session.get(Question, qid), session)}


@router.post("/api/hotelier/hotels/{hotel_id}/confirm/{key}")
def hotelier_confirm(hotel_id: str, key: str, user: str = Depends(check_token), session: Session = Depends(get_session)):
    """Отельер подтверждает значение, найденное агентами."""
    h = _hotel_or_404(session, hotel_id)
    spec = get_spec(session, key)
    rv = session.get(ResolvedValue, (h.id, key))
    if spec is None or rv is None or not rv.value:
        raise HTTPException(400, "Нечего подтверждать")
    primary = session.get(Fact, rv.primary_fact_id) if rv.primary_fact_id else None
    now = utcnow()
    f = Fact(hotel_id=h.id, char_key=key, value=primary.value if primary else rv.value,
             summary=(primary.summary if primary else rv.summary), source="hotelier_confirmed",
             source_detail=f"подтверждено в кабинете отельера ({user})", method="hotelier.confirm", confidence=0.9,
             observed_at=now, valid_until=now + dt.timedelta(days=int(spec.get("ttl_days", 365))),
             evidence={"confirmed_fact_id": rv.primary_fact_id})
    session.add(f)
    session.flush()
    for old in session.scalars(select(Fact).where(Fact.hotel_id == h.id, Fact.char_key == key, Fact.status == "active",
                                                  Fact.source.in_(["hotelier", "hotelier_confirmed"]), Fact.id != f.id)):
        old.status = "superseded"
    session.flush()
    for q in session.scalars(select(Question).where(Question.hotel_id == h.id, Question.status == "open")):
        if key in (q.char_keys or []):
            q.status = "closed"
    rv = arbiter.resolve(session, h.id, spec, reason="hotelier_confirm")
    return rv_dict(rv, spec)


class CorrectBody(BaseModel):
    text: str


@router.post("/api/hotelier/hotels/{hotel_id}/correct/{key}")
def hotelier_correct(hotel_id: str, key: str, body: CorrectBody, user: str = Depends(check_token),
                     session: Session = Depends(get_session)):
    """Отельер исправляет значение свободным текстом — агент раскладывает его по полям."""
    h = _hotel_or_404(session, hotel_id)
    spec = get_spec(session, key)
    if spec is None:
        raise HTTPException(404, "Нет такой характеристики")
    q = Question(hotel_id=h.id, char_keys=[key], text=f"Исправление от отельера: {spec['title']}", reason="correction",
                 context={"fields": list({**spec.get('value_fields', {}), **spec.get('extra_fields', {})}.keys())})
    session.add(q)
    session.flush()
    run = orchestrator.create_run(session, "answer", hotel_id=h.id, char_keys=[key], trigger="hotelier_correction",
                                  params={"question_id": q.id, "text": body.text, "answered_by": "hotelier_confirmed"})
    session.commit()
    orchestrator.execute_run(run.id)
    session.expire_all()
    rv = session.get(ResolvedValue, (h.id, key))
    steps = list(session.scalars(select(AgentStep).where(AgentStep.run_id == run.id).order_by(AgentStep.idx)))
    return {"value": rv_dict(rv, spec), "run": run_dict(session.get(AgentRun, run.id), steps)}


# =============================================================== метрики
@router.get("/api/metrics")
def metrics(session: Session = Depends(get_session)):
    from ..evaluation import compute_metrics

    return compute_metrics(session)
