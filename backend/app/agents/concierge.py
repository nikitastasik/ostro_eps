"""Агент-консьерж: понимает запрос гостя, ранжирует отели и объясняет выбор.

Ключевая идея — номер, а не отель: характеристики вида и шума известны по
фасадам и этажам, поэтому консьерж подбирает лучшую сторону здания, на которой
выполняется максимум пожеланий одновременно, и честно говорит о компромиссах:
«закат есть, но только с 5-го этажа», «тихая сторона — во двор, но без вида».
"""

from __future__ import annotations

import json
import math

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import llm
from ..geo.analysis import facade_acc, facade_gen, month_ranges
from ..knowledge.registry import all_specs
from ..models import Hotel, Question, ResolvedValue, SearchLog
from ..nlp.query_parser import Criterion, ParsedQuery, criteria_from_dicts, parse_query
from ..nlp.ru import dec, walk_min_text, walk_to_text

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
        s = walk_to_text("пляжа", v["walk_m"], v["minutes_to"], "прямо у пляжа (первая линия)")
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
        return f"до центра {walk_min_text(v['minutes_to'])}"
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


# ---------------------------------------------------------------- доля подходящих номеров
FLOOR_FIELD = {"sea_view": "min_floor", "sunset_view": "min_floor", "mountain_view": "min_floor",
               "landmark_view": "min_floor", "window_outlook": "open_from_floor"}
SHARE_OK = 0.6  # если пожелание выполняется у меньшей доли номеров — «подходит с условием»
NOUN = {"sunset_view": "закат", "sea_view": "море", "mountain_view": "горы", "landmark_view": "ориентиры",
        "window_outlook": "открытый вид", "quietness": "тишина"}
STATUS_RU = {"match": "подходит", "conditional": "подходит с условием", "partial": "не дотягивает",
             "miss": "не подходит", "unknown": "нет данных"}


def _facade_floor(key: str, fv: dict | None) -> int:
    """С какого этажа пожелание выполняется на этой стороне (шум в модели от этажа не зависит)."""
    field = FLOOR_FIELD.get(key)
    return max(1, int((fv or {}).get(field) or 1)) if field else 1


def room_share(lengths: dict[str, float], levels: int, ok_from: dict[str, int | None]) -> float:
    """Доля номеров, где выполняются пожелания.

    Номер — это пара «сторона здания × этаж». Числитель: Σ длина стороны × число этажей от того, с которого
    выполняются все пожелания, до верхнего; знаменатель: Σ длина × все этажи. ok_from: сторона → с какого
    этажа всё выполняется (None — не выполняется ни на каком).
    """
    levels = max(1, int(levels or 1))
    total = sum(lengths.values()) * levels
    ok = sum(lengths.get(f, 0.0) * max(0, levels - fl + 1) for f, fl in ok_from.items() if fl is not None)
    return min(1.0, ok / total) if total else 0.0


def share_factor(share: float | None) -> float:
    """Множитель «фасадной» части оценки: монотонно растёт с долей подходящих номеров.

    0,7 при доле 0 и 1,0 при доле 1, по корню: разница между 5% и 30% номеров важнее, чем между 70% и 95%.
    """
    if share is None:
        return 1.0
    return 0.7 + 0.3 * math.sqrt(max(0.0, min(1.0, share)))


def _levels(h: Hotel, rvs: dict) -> int:
    rv = rvs.get((h.id, "building_levels"))
    lv = (rv.value or {}).get("levels") if rv and rv.value else None
    return int(lv or (h.building or {}).get("levels") or 5)


def _pct(x: float) -> str:
    return f"≈{max(1, round(x * 100))}%"


def evaluate_hotel(h: Hotel, specs: dict, groups: dict[str, list[Criterion]], rvs: dict, open_q: dict,
                   month: int | None = None) -> dict:
    """Оценка одного отеля под пожелания: лучшая сторона, статусы, доля подходящих номеров, итоговый балл."""
    keys = list(groups)
    facade_keys = [k for k in keys if specs[k]["scope"] == "facade"]
    hotel_keys = [k for k in keys if specs[k]["scope"] != "facade"]
    b = h.building or {}
    levels = _levels(h, rvs)

    def em(m, rv):
        conf = rv.confidence if rv else 0.0
        return PRIOR if m is None else conf * m + (1 - conf) * PRIOR

    def weight(k):
        return sum(c.weight for c in groups[k]) / len(groups[k])

    hotel_part = {}
    for k in hotel_keys:
        rv = rvs.get((h.id, k))
        hotel_part[k] = (evaluate_need(specs[k], groups[k], rv.value if rv else None), rv)
    # --- «фасадные» пожелания: оценка по каждой стороне здания и выбор лучшей
    per: dict[str, dict] = {}
    best_facade, best_fsum = None, -1.0
    if facade_keys:
        facades = set()
        for k in facade_keys:
            rv = rvs.get((h.id, k))
            if rv and rv.value and rv.value.get("by_facade"):
                facades.update(rv.value["by_facade"].keys())
        for f in sorted(facades):
            part, fsum = {}, 0.0
            for k in facade_keys:
                rv = rvs.get((h.id, k))
                fv = ((rv.value or {}).get("by_facade") or {}).get(f) if rv else None
                m = evaluate_need(specs[k], groups[k], fv) if fv is not None else None
                fsum += weight(k) * em(m, rv)
                part[k] = (m, rv, fv)
            per[f] = part
            if fsum > best_fsum:
                best_fsum, best_facade = fsum, f
    lengths = {f["key"]: float(f.get("length_m") or 0) for f in b.get("facades") or []}
    if per and not any(lengths.get(f) for f in per):
        lengths = dict.fromkeys(per, 1.0)  # контура нет — стороны считаем равными
    share_k: dict[str, float] = {}
    sides_k: dict[str, int] = {}
    share_all = None
    if per:
        def ok(f, k):
            return (per[f][k][0] or 0) >= 0.99
        for k in facade_keys:
            share_k[k] = room_share(lengths, levels, {f: _facade_floor(k, per[f][k][2]) if ok(f, k) else None for f in per})
            sides_k[k] = sum(1 for f in per if ok(f, k))
        share_all = room_share(lengths, levels, {
            f: max(_facade_floor(k, per[f][k][2]) for k in facade_keys) if all(ok(f, k) for k in facade_keys) else None
            for f in per})
    # --- статусы, тексты, балл
    needs, floors_needed = [], []
    hotel_sum = facade_sum = total_w = 0.0
    excluded = False
    for k in keys:
        spec = specs[k]
        w = weight(k)
        if k in hotel_part:
            (m, rv), fv = hotel_part[k], None
        elif best_facade is not None:
            m, rv, fv = per[best_facade][k]
        else:
            rv = rvs.get((h.id, k))
            m, fv = evaluate_need(spec, groups[k], rv.value if rv else None), None
        conf = rv.confidence if rv else 0.0
        e = em(m, rv)
        if k in hotel_part:
            hotel_sum += w * e
        else:
            facade_sum += w * e
        total_w += w
        if any(c.required for c in groups[k]) and m is not None and m == 0 and conf >= 0.5:
            excluded = True
        status = "unknown" if m is None else "match" if m >= 0.99 else "partial" if m > 0.2 else "miss"
        floor = _facade_floor(k, fv) if fv is not None and status in {"match", "partial"} else None
        if status == "match" and k in share_k:
            if share_k[k] < SHARE_OK or (floor or 1) > 1 or sides_k[k] < len(per):
                status = "conditional"
        if fv is not None and best_facade:
            text = facade_text(k, fv, best_facade, month)
            if status == "conditional":
                text += f" — {_pct(share_k[k])} номеров"
            if floor and floor > 1 and k != "quietness" and status in {"match", "conditional", "partial"}:
                floors_needed.append(floor)
        else:
            text = hotel_text(k, rv) if k in {"beach_access", "center_access"} else (rv.summary if rv and rv.summary else "")
        if m is None:
            text = "нет данных" + (" — агент спросил отельера, ждём ответа" if (h.id, k) in open_q else "")
        need = {"key": k, "title": spec["title"], "status": status, "match": None if m is None else round(m, 2),
                "confidence": round(conf, 2), "text": text, "sources": rv.sources if rv else [],
                "rv_status": rv.status if rv else "unknown", "label": groups[k][0].label, "floor": floor,
                "share": round(share_k[k], 3) if k in share_k else None, "contrib": w * e}
        if k == "quietness" and fv is not None:
            need["db"] = fv.get("night_db")
        if k == "sunset_view" and fv is not None:
            need["over_sea"] = bool(fv.get("over_sea_months"))
        if k in {"beach_access", "center_access"} and rv and rv.value:
            need["minutes"] = rv.value.get("minutes_to")
        needs.append(need)
    mult = share_factor(share_all) if facade_keys else 1.0
    for n in needs:
        if n["key"] in share_k:
            n["contrib"] *= mult
    score = round(100 * (hotel_sum + mult * facade_sum) / total_w) if total_w else 0
    room = None
    if best_facade and facade_keys:
        floor = max(floors_needed) if floors_needed else None
        room = {"facade": best_facade, "facade_text": f"{facade_gen(best_facade)} стороны", "min_floor": floor,
                "share": round(share_all or 0, 3)}
        ro = rvs.get((h.id, "room_orientation"))
        cats = ((ro.value or {}).get("categories") if ro else None) or []
        for c in cats:
            if c.get("facade") == best_facade or (c.get("side") == "sea" and "sea_view" in facade_keys) or \
                    (c.get("side") == "courtyard" and "quietness" in facade_keys and len(facade_keys) == 1):
                room["category"] = c["name"]
                break
        parts = [f"номер с окнами на {facade_acc(best_facade)} сторону"]
        if b.get("corpus"):
            parts.insert(0, b["corpus"])
        if floor:
            parts.append(f"не ниже {floor}-го этажа")
        if room.get("category"):
            parts.append(f"категория «{room['category']}» (со слов отельера)")
        room["text"] = ", ".join(parts)
        room["share_text"] = (f"под все пожелания подходит {_pct(share_all)} номеров отеля" if share_all
                              else "ни одна сторона не выполняет все пожелания сразу")
    known = [n for n in needs if n["status"] != "unknown"]
    beach = rvs.get((h.id, "beach_access"))
    return {
        "hotel": hotel_brief(h), "score": score, "excluded": excluded, "needs": needs, "room": room,
        "summary": _summary(needs, room), "verdict": _summary(needs, None),
        "caveats": sum(1 for n in needs if n["status"] in {"partial", "miss"}),
        "known": len(known), "share": None if share_all is None else round(share_all, 3),
        "_tie": (round(share_all or 0, 3), round(sum(n["confidence"] for n in known) / len(known), 3) if known else 0,
                 -float((beach.value or {}).get("minutes_to") or 999) if beach and beach.value else -999.0),
    }


def _why_above(a: dict, b: dict) -> str:
    """Почему отель a стоит выше следующего b: главное различие по пожеланиям, иначе — разбор ничьей."""
    nb = {n["key"]: n for n in b["needs"]}
    diffs = sorted(((n["contrib"] - nb[n["key"]]["contrib"], n, nb[n["key"]]) for n in a["needs"] if n["key"] in nb),
                   key=lambda t: -t[0])
    for d, x, y in diffs:
        if d < 0.004:
            break
        k = x["key"]
        if x.get("floor") and y.get("floor") and x["floor"] < y["floor"] and k in NOUN:
            return f"{NOUN[k]} с {x['floor']}-го этажа, а не с {y['floor']}-го"
        if x["status"] != y["status"]:
            return f"{x['title'].lower()}: {STATUS_RU[x['status']]}, а у следующего — {STATUS_RU[y['status']]}"
        if x.get("share") is not None and y.get("share") is not None and x["share"] > y["share"]:
            return f"{x['title'].lower()} у {_pct(x['share'])} номеров, а не у {_pct(y['share'])}"
        if x.get("db") is not None and y.get("db") is not None and x["db"] < y["db"]:
            return f"ночью тише: ≈{x['db']:.0f} дБ против ≈{y['db']:.0f}"
        if x.get("minutes") is not None and y.get("minutes") is not None and x["minutes"] < y["minutes"]:
            return f"{x['title'].lower()}: {walk_min_text(x['minutes'])}, а не {walk_min_text(y['minutes'])}"
        if x["confidence"] > y["confidence"]:
            return f"{x['title'].lower()}: данные надёжнее (уверенность {dec(x['confidence'], 2)} против {dec(y['confidence'], 2)})"
    (sa, ca, da), (sb, cb, db) = a["_tie"], b["_tie"]
    if a["score"] > b["score"]:
        return "выше по совокупности пожеланий"
    if _pct(sa) != _pct(sb) and sa > sb:
        return f"при равной оценке — больше подходящих номеров ({_pct(sa)} против {_pct(sb)})"
    if ca > cb:
        return f"при равной оценке — надёжнее данные (уверенность {dec(ca, 2)} против {dec(cb, 2)})"
    if da > db:
        return "при равной оценке — ближе к пляжу"
    return "при равной оценке — чуть больше подходящих номеров" if sa > sb else "при равной оценке — по порядку в каталоге"


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
        extra = ["room_orientation", "building_levels", "beach_access"]
        for rv in session.scalars(select(ResolvedValue).where(ResolvedValue.char_key.in_(keys + extra))):
            rvs[(rv.hotel_id, rv.char_key)] = rv
    open_q = {}
    for q in session.scalars(select(Question).where(Question.status == "open")):
        for k in q.char_keys or []:
            open_q[(q.hotel_id, k)] = q.id
    month = next((c.month for c in pq.criteria if c.month), None)
    results = [evaluate_hotel(h, specs, groups, rvs, open_q, month) for h in hotels]
    # порядок: балл, затем разбор ничьих — доля подходящих номеров, уверенность данных, близость пляжа, id
    results.sort(key=lambda r: (r["excluded"], -r["score"], -r["_tie"][0], -r["_tie"][1], -r["_tie"][2],
                                r["hotel"]["id"]))
    # корпуса одного комплекса — одна карточка: лучший корпус, остальные ссылкой «ещё N корпус»
    collapsed, by_cx = [], {}
    for r in results:
        cx = r["hotel"].get("complex_id")
        if cx and cx in by_cx:
            by_cx[cx]["others"].append({"id": r["hotel"]["id"], "name": r["hotel"]["name"],
                                        "corpus": r["hotel"].get("corpus"), "score": r["score"]})
            continue
        if cx:
            r["others"] = []
            by_cx[cx] = r
        collapsed.append(r)
    results = collapsed
    top = results[:limit]
    for a, b in zip(top, top[1:]):
        a["why_above"] = _why_above(a, b) if keys else None
    for r in results:
        r.pop("_tie", None)
        for n in r["needs"]:
            n.pop("contrib", None)
    if log:
        session.add(SearchLog(query=text, parsed=pq.as_dict(), parser=pq.parser,
                              top_hotels=[r["hotel"]["id"] for r in top[:10]]))
    return {"query": parsed, "results": top, "total": len(results),
            "criteria_keys": keys, "llm": llm.enabled()}


def fit(session: Session, hotel_id: str, text: str) -> dict | None:
    """Как отель подходит под запрос гостя — та же оценка, что в поиске (для карточки отеля и пожелания)."""
    h = session.get(Hotel, hotel_id)
    if h is None:
        return None
    specs = all_specs(session)
    pq = understand(text, specs)
    groups = _group(pq.criteria)
    keys = list(groups) + ["room_orientation", "building_levels", "beach_access"]
    rvs = {(hotel_id, rv.char_key): rv for rv in session.scalars(
        select(ResolvedValue).where(ResolvedValue.hotel_id == hotel_id, ResolvedValue.char_key.in_(keys)))}
    open_q = {(hotel_id, k): q.id for q in session.scalars(select(Question).where(
        Question.hotel_id == hotel_id, Question.status == "open")) for k in q.char_keys or []}
    month = next((c.month for c in pq.criteria if c.month), None)
    r = evaluate_hotel(h, specs, groups, rvs, open_q, month)
    r.pop("_tie", None)
    for n in r["needs"]:
        n.pop("contrib", None)
    return {"query": pq.as_dict(), "result": r}


def _summary(needs: list[dict], room: dict | None) -> str:
    """Итоговая строка: четыре группы пожеланий и совет по номеру."""
    groups = [("подходит", {"match"}), ("с условием", {"conditional"}), ("не дотягивает", {"partial", "miss"}),
              ("нет данных", {"unknown"})]
    parts = [f"{name}: " + ", ".join(n["title"].lower() for n in needs if n["status"] in sts)
             for name, sts in groups if any(n["status"] in sts for n in needs)]
    s = "; ".join(parts)
    if room and room.get("text"):
        s += f". Лучше всего — {room['text']}"
    return s[0].upper() + s[1:] if s else ""


def hotel_brief(h: Hotel) -> dict:
    b = h.building or {}
    return {"id": h.id, "name": h.label, "osm_name": h.name, "kind": h.kind, "stars": h.stars, "lat": h.lat, "lon": h.lon,
            "complex_id": b.get("complex_id"), "corpus": b.get("corpus"),
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

