"""Агент-консьерж: понимает запрос гостя, ранжирует отели и объясняет выбор.

Ключевая идея — номер, а не отель: характеристики вида и шума известны по
фасадам и этажам, поэтому консьерж подбирает лучшую сторону здания, на которой
выполняется максимум пожеланий одновременно, и честно говорит о компромиссах:
«закат есть, но только с 5-го этажа», «тихая сторона — во двор, но без вида».
"""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import llm
from ..geo.analysis import facade_acc, facade_gen, month_ranges
from ..knowledge.registry import all_specs
from ..models import Hotel, Question, ResolvedValue, SearchLog
from ..nlp.query_parser import Criterion, ParsedQuery, criteria_from_dicts, parse_query

PRIOR = 0.3  # ожидание совпадения, когда данных нет
MONTHS_PREP = ["январе", "феврале", "марте", "апреле", "мае", "июне", "июле", "августе", "сентябре", "октябре",
               "ноябре", "декабре"]


# ---------------------------------------------------------------- разбор запроса
def llm_parse(text: str, specs: dict[str, dict]) -> ParsedQuery | None:
    if not llm.enabled():
        return None
    catalog = []
    for key, s in specs.items():
        fields = {**s.get("value_fields", {}), **s.get("extra_fields", {})}
        catalog.append({"key": key, "title": s["title"], "scope": s["scope"], "description": s.get("description", "")[:200],
                        "fields": {f: {k: v for k, v in d.items() if k in {"type", "title", "values"}} for f, d in fields.items()},
                        "example_criteria": (s.get("search") or {}).get("criteria", [])[:2]})
    system = (
        "Ты агент-консьерж сервиса бронирования отелей. Переведи запрос гостя в критерии поиска по каталогу "
        "характеристик. Используй только ключи и поля из каталога. Операторы: eq, ne, le, ge, lt, gt, in, contains, "
        "nonempty. Для числовых пожеланий можешь задать soft — допуск, в пределах которого совпадение частичное. "
        "weight: 2 — если гость говорит «обязательно/главное», 0.5 — «желательно», иначе 1; required=true только для "
        "явно обязательных. month — номер месяца, если гость его упомянул и он относится к характеристике. "
        "Всё, что не удалось сопоставить каталогу, верни в unmatched. Ответ — JSON "
        '{"criteria":[{"key","field","op","value","soft","weight","required","month","label"}],"unmatched":[...]}'
    )
    res = llm.chat_json(system, json.dumps({"query": text, "catalog": catalog}, ensure_ascii=False), max_tokens=900)
    if not res:
        return None
    crits = criteria_from_dicts(res.get("criteria", []), specs)
    if not crits:
        return None
    return ParsedQuery(text=text, criteria=crits, unmatched=[str(u) for u in res.get("unmatched", [])][:5], parser="llm")


def understand(text: str, specs: dict[str, dict]) -> ParsedQuery:
    rules = parse_query(text, specs)
    via_llm = llm_parse(text, specs)
    if via_llm is not None:
        return via_llm
    return rules


# ---------------------------------------------------------------- оценка критерия
def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def eval_op(v, c: Criterion, month_list=None) -> float | None:
    """Степень выполнения критерия 0..1 или None, если значения нет."""
    if c.month and c.month_field:
        if month_list is None:
            return None
        return 1.0 if int(c.month) in [int(m) for m in month_list] else 0.0
    if v is None:
        return None
    op, target = c.op, c.value
    if op == "eq":
        if isinstance(target, bool) or isinstance(v, bool):
            return 1.0 if bool(v) == bool(target) else 0.0
        return 1.0 if str(v) == str(target) else 0.0
    if op == "ne":
        return 0.0 if str(v) == str(target) else 1.0
    if op in {"le", "lt", "ge", "gt"}:
        x, t = _num(v), _num(target)
        if x is None or t is None:
            return None
        ok = {"le": x <= t, "lt": x < t, "ge": x >= t, "gt": x > t}[op]
        if ok:
            return 1.0
        soft = _num(c.soft)
        if soft and soft > 0:
            return max(0.0, 1.0 - abs(x - t) / soft)
        return 0.0
    if op == "in":
        vals = target if isinstance(target, list) else [target]
        return 1.0 if str(v) in [str(t) for t in vals] else 0.0
    if op == "contains":
        if isinstance(v, list):
            return 1.0 if target in v else 0.0
        return 1.0 if str(target) in str(v) else 0.0
    if op == "nonempty":
        return 1.0 if v else 0.0
    return None


def _field(value: dict | None, field: str):
    if not value:
        return None
    return value.get(field)


def _partial(spec: dict, value: dict | None) -> float:
    best = 0.0
    for p in (spec.get("search") or {}).get("partial", []):
        if eval_op(_field(value, p["field"]), Criterion(spec["key"], p["field"], p["op"], p["value"])) == 1.0:
            best = max(best, float(p.get("score", 0.5)))
    return best


# ---------------------------------------------------------------- тексты объяснений
def _floor_txt(n) -> str:
    return "с 1-го этажа" if not n or n == 1 else f"только с {n}-го этажа"


def facade_text(key: str, fv: dict, facade: str, month: int | None = None) -> str:
    g = facade_gen(facade)
    if key == "sea_view":
        if fv.get("available"):
            return f"море видно из окон {g} стороны {_floor_txt(fv.get('min_floor'))} (обзор до {fv.get('max_width_deg')}°)"
        if fv.get("quality") == "glimpse":
            return f"с {g} стороны море видно лишь краешком"
        return f"с {g} стороны моря не видно"
    if key == "sunset_view":
        months = fv.get("months") or []
        if fv.get("available"):
            if month and month not in months:
                return f"с {g} стороны закат виден, но не в {MONTHS_PREP[month - 1]} ({month_ranges(months)})"
            sea = fv.get("over_sea_months") or []
            if month:
                over = " над морем" if month in sea else ""
                return f"в {MONTHS_PREP[month - 1]} закат{over} виден из окон {g} стороны {_floor_txt(fv.get('min_floor'))}"
            tail = ""
            if sea and set(sea) != set(months):
                tail = f"; над морем — {month_ranges(sea)}"
            elif sea:
                tail = ", над морем"
            return f"закат виден из окон {g} стороны {_floor_txt(fv.get('min_floor'))}: {month_ranges(months)}{tail}"
        return f"с {g} стороны заката не видно"
    if key == "mountain_view":
        return (f"горы видны с {g} стороны {_floor_txt(fv.get('min_floor'))}" if fv.get("available")
                else f"с {g} стороны гор не видно")
    if key == "landmark_view":
        names = fv.get("landmarks") or []
        return (f"с {g} стороны видно: {', '.join(names[:2])} {_floor_txt(fv.get('min_floor'))}" if fv.get("available")
                else f"с {g} стороны ориентиров не видно")
    if key == "quietness":
        db = fv.get("night_db")
        if db is None:
            return ""
        src = (fv.get("sources") or [{}])[0].get("name")
        tail = f", шумит {src}" if src and db >= 50 else ""
        return f"ночью у {g} стороны ≈{db:.0f} дБ ({fv.get('label')}){tail}"
    if key == "window_outlook":
        if fv.get("open_view"):
            return f"открытый вид с {g} стороны {_floor_txt(fv.get('open_from_floor'))}"
        w = fv.get("wall_m_floor1")
        return f"с {g} стороны окна смотрят на соседний дом" + (f" в {w} м" if w else "")
    return ""


def hotel_text(key: str, rv: ResolvedValue | None) -> str:
    if rv is None or not rv.value:
        return ""
    v = rv.value
    if key == "beach_access" and v.get("minutes_to") is not None:
        s = f"до пляжа {v['walk_m']} м, ≈{v['minutes_to']:.0f} мин пешком"
        extras = []
        if (v.get("climb_back_m") or 0) >= 15:
            extras.append(f"обратно подъём {v['climb_back_m']} м")
        if v.get("steps"):
            extras.append("лестницы")
        if v.get("major_crossings"):
            extras.append("переход через дорогу: " + ", ".join(sorted({c["road"] for c in v.get("crossings", [])}))[:60])
        if v.get("rail") and v["rail"] != "none":
            extras.append({"underpass": "подземный переход под ж/д", "bridge": "мост над ж/д", "level": "через ж/д пути"}[v["rail"]])
        return s + (f" ({'; '.join(extras)})" if extras else "")
    if key == "center_access" and v.get("minutes_to") is not None:
        return f"до центра ≈{v['minutes_to']:.0f} мин пешком"
    return rv.summary.split(";")[0] if rv.summary else ""


# ---------------------------------------------------------------- ранжирование
def _group(criteria: list[Criterion]) -> dict[str, list[Criterion]]:
    out: dict[str, list[Criterion]] = {}
    for c in criteria:
        out.setdefault(c.key, []).append(c)
    return out


def evaluate_need(spec: dict, crits: list[Criterion], value: dict | None) -> float | None:
    if not value:
        return None
    scores = []
    for c in crits:
        ml = value.get(c.month_field) if c.month_field else None
        s = eval_op(_field(value, c.field), c, month_list=ml)
        if s is None:
            continue
        scores.append(s)
    if not scores:
        return None
    m = min(scores)
    if m < 1.0:
        m = max(m, _partial(spec, value))
    return m


def search(session: Session, text: str, criteria_override: list[dict] | None = None, limit: int = 30,
           log: bool = True) -> dict:
    specs = all_specs(session)
    if criteria_override is not None:
        pq = ParsedQuery(text=text, criteria=criteria_from_dicts(criteria_override, specs), parser="manual")
    else:
        pq = understand(text, specs)
    groups = _group(pq.criteria)
    keys = list(groups)
    parsed = pq.as_dict()
    for c in parsed["criteria"]:
        spec = specs.get(c["key"], {})
        f = {**spec.get("value_fields", {}), **spec.get("extra_fields", {})}.get(c["field"], {})
        c["field_title"] = f.get("title", c["field"])
    hotels = list(session.scalars(select(Hotel)))
    rvs: dict[tuple[str, str], ResolvedValue] = {}
    if keys:
        for rv in session.scalars(select(ResolvedValue).where(ResolvedValue.char_key.in_(keys + ["room_orientation"]))):
            rvs[(rv.hotel_id, rv.char_key)] = rv
    open_q = {}
    for q in session.scalars(select(Question).where(Question.status == "open")):
        for k in q.char_keys or []:
            open_q[(q.hotel_id, k)] = q.id

    facade_keys = [k for k in keys if specs[k]["scope"] == "facade"]
    hotel_keys = [k for k in keys if specs[k]["scope"] != "facade"]
    results = []
    for h in hotels:
        needs = []
        # --- характеристики уровня отеля
        hotel_part = {}
        for k in hotel_keys:
            rv = rvs.get((h.id, k))
            m = evaluate_need(specs[k], groups[k], rv.value if rv else None)
            hotel_part[k] = (m, rv)
        # --- подбор лучшей стороны здания под все «фасадные» пожелания
        best_facade, best_fsum, facade_part = None, -1.0, {}
        if facade_keys:
            facades = set()
            for k in facade_keys:
                rv = rvs.get((h.id, k))
                if rv and rv.value and rv.value.get("by_facade"):
                    facades.update(rv.value["by_facade"].keys())
            for f in sorted(facades):
                part = {}
                fsum = 0.0
                for k in facade_keys:
                    rv = rvs.get((h.id, k))
                    fv = ((rv.value or {}).get("by_facade") or {}).get(f) if rv else None
                    m = evaluate_need(specs[k], groups[k], fv) if fv is not None else None
                    conf = rv.confidence if rv else 0.0
                    w = sum(c.weight for c in groups[k]) / len(groups[k])
                    em = PRIOR if m is None else conf * m + (1 - conf) * PRIOR
                    fsum += w * em
                    part[k] = (m, rv, fv)
                if fsum > best_fsum:
                    best_fsum, best_facade, facade_part = fsum, f, part
            if best_facade is None:
                for k in facade_keys:
                    rv = rvs.get((h.id, k))
                    m = evaluate_need(specs[k], groups[k], rv.value if rv else None)
                    facade_part[k] = (m, rv, None)
        # --- суммарная оценка
        total_w, total = 0.0, 0.0
        excluded = False
        floors_needed = []
        month = next((c.month for c in pq.criteria if c.month), None)
        for k in keys:
            spec = specs[k]
            w = sum(c.weight for c in groups[k]) / len(groups[k])
            required = any(c.required for c in groups[k])
            if k in hotel_part:
                m, rv = hotel_part[k]
                fv = None
            else:
                m, rv, fv = facade_part.get(k, (None, None, None))
            conf = rv.confidence if rv else 0.0
            em = PRIOR if m is None else conf * m + (1 - conf) * PRIOR
            total += w * em
            total_w += w
            if required and m is not None and m == 0 and conf >= 0.5:
                excluded = True
            status = "unknown" if m is None else "match" if m >= 0.99 else "partial" if m > 0.2 else "miss"
            if fv is not None and best_facade:
                text = facade_text(k, fv, best_facade, month)
                mf = fv.get("min_floor") if status in {"match", "partial"} and k != "quietness" else None
                if mf and mf > 1:
                    floors_needed.append(mf)
            else:
                text = hotel_text(k, rv) if k in {"beach_access", "center_access"} else (rv.summary if rv and rv.summary else "")
            if m is None:
                text = "нет данных" + (" — агент спросил отельера, ждём ответа" if (h.id, k) in open_q else "")
            needs.append({"key": k, "title": spec["title"], "status": status, "match": None if m is None else round(m, 2),
                          "confidence": round(conf, 2), "text": text, "sources": rv.sources if rv else [],
                          "rv_status": rv.status if rv else "unknown", "label": groups[k][0].label})
        score = round(100 * total / total_w) if total_w else 0
        room = None
        if best_facade and facade_keys:
            floor = max(floors_needed) if floors_needed else None
            room = {"facade": best_facade, "facade_text": f"{facade_gen(best_facade)} стороны", "min_floor": floor}
            ro = rvs.get((h.id, "room_orientation"))
            cats = ((ro.value or {}).get("categories") if ro else None) or []
            for c in cats:
                if c.get("facade") == best_facade or (c.get("side") == "sea" and "sea_view" in facade_keys) or \
                        (c.get("side") == "courtyard" and "quietness" in facade_keys and len(facade_keys) == 1):
                    room["category"] = c["name"]
                    break
            parts = [f"номер с окнами на {facade_acc(best_facade)} сторону"]
            if floor:
                parts.append(f"не ниже {floor}-го этажа")
            if room.get("category"):
                parts.append(f"категория «{room['category']}» (со слов отельера)")
            room["text"] = ", ".join(parts)
        caveats = [n for n in needs if n["status"] in {"partial", "miss"}]
        results.append({
            "hotel": hotel_brief(h), "score": score, "excluded": excluded, "needs": needs, "room": room,
            "summary": _summary(needs, room), "caveats": len(caveats),
            "known": sum(1 for n in needs if n["status"] != "unknown"),
        })
    results.sort(key=lambda r: (r["excluded"], -r["score"], -r["known"]))
    top = results[:limit]
    if log:
        session.add(SearchLog(query=text, parsed=pq.as_dict(), parser=pq.parser,
                              top_hotels=[r["hotel"]["id"] for r in top[:10]]))
    return {"query": parsed, "results": top, "total": len(results),
            "criteria_keys": keys, "llm": llm.enabled()}


def _summary(needs: list[dict], room: dict | None) -> str:
    good = [n for n in needs if n["status"] == "match"]
    weak = [n for n in needs if n["status"] in {"partial", "miss"}]
    unknown = [n for n in needs if n["status"] == "unknown"]
    parts = []
    if good:
        parts.append("подходит: " + ", ".join(n["title"].lower() for n in good))
    if weak:
        parts.append("не дотягивает: " + ", ".join(n["title"].lower() for n in weak))
    if unknown:
        parts.append("нет данных: " + ", ".join(n["title"].lower() for n in unknown))
    s = "; ".join(parts)
    if room and room.get("text"):
        s += f". Лучше всего — {room['text']}"
    return s[0].upper() + s[1:] if s else ""


def hotel_brief(h: Hotel) -> dict:
    b = h.building or {}
    return {"id": h.id, "name": h.name, "kind": h.kind, "stars": h.stars, "lat": h.lat, "lon": h.lon,
            "address": h.address, "levels": b.get("levels"), "website": h.website}


def compare(session: Session, hotel_ids: list[str]) -> dict:
    specs = all_specs(session)
    hotels = [session.get(Hotel, i) for i in hotel_ids]
    hotels = [h for h in hotels if h is not None]
    rows = []
    from ..knowledge.registry import GROUPS

    order = {g["key"]: i for i, g in enumerate(GROUPS)}
    for key, spec in sorted(specs.items(), key=lambda kv: (order.get(kv[1]["group"], 99), kv[1]["title"])):
        cells = []
        for h in hotels:
            rv = session.get(ResolvedValue, (h.id, key))
            cells.append({"summary": rv.summary if rv else "", "confidence": rv.confidence if rv else 0.0,
                          "status": rv.status if rv else "unknown", "sources": rv.sources if rv else [],
                          "score": _cell_score(key, rv)})
        if all(not c["summary"] for c in cells):
            continue
        scores = [c["score"] for c in cells if c["score"] is not None]
        best = max(scores) if scores else None
        for c in cells:
            c["best"] = best is not None and c["score"] == best and len(scores) > 1 and len(set(scores)) > 1
        rows.append({"key": key, "title": spec["title"], "group": spec["group"], "cells": cells})
    return {"hotels": [hotel_brief(h) for h in hotels], "rows": rows}


def _cell_score(key: str, rv: ResolvedValue | None) -> float | None:
    """Чем больше, тем лучше — для подсветки лучшего значения в сравнении."""
    if rv is None or not rv.value:
        return None
    v = rv.value
    try:
        if key == "sea_view":
            return (v.get("max_width_deg") or 0) / 10 - (v.get("min_floor") or 20) if v.get("available") else -100
        if key == "sunset_view":
            return len(v.get("months") or []) * 10 - (v.get("min_floor") or 20)
        if key == "quietness":
            return -float(v.get("night_db") or 99)
        if key in {"beach_access", "center_access"}:
            return -float(v.get("minutes_to") or 999)
        if key == "nightlife":
            return float(v.get("score") or 0)
        if key == "families":
            return float(v.get("playgrounds_400m") or 0)
        if key == "construction_nearby":
            return -float(v.get("sites_300m") or 0)
        if key == "night_services":
            return 1.0 if v.get("available") else 0.0
        if key == "mountain_view":
            return 1.0 if v.get("available") else 0.0
    except (TypeError, ValueError):
        return None
    return None

