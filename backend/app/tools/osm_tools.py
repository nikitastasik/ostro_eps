"""Инструменты исследователя OSM: инфраструктура вокруг, история здания, этажность, теги отеля."""

from __future__ import annotations

import re

import numpy as np
from shapely.geometry import LineString, Point

from ..geo.opening_hours import parse as parse_oh
from ..knowledge.categories import FOOD, plural_gen, title
from ..nlp.ru import NEAR_M, dist_text, walk_min_text
from .base import ToolResult, cap, named, plural, quote, tool

AGENT = "Исследователь OSM"


def _snapshot(ctx) -> str:
    return ctx.region.meta["sources"]["osm"]["snapshot"]


def _detail(ctx, what: str) -> str:
    return f"OpenStreetMap (снимок {_snapshot(ctx)}): {what}"


def _has_point(ctx, params) -> tuple[bool, str]:
    b = ctx.hotel.building or {}
    if b.get("x") is None:
        return False, "нет координат отеля"
    return True, "есть координаты отеля и слой OSM вокруг"


def pois_near(ctx, cats: set[str], radius: float) -> list[tuple[object, float]]:
    rd = ctx.region
    geo = ctx.geo
    xy = rd.poi_xy
    if xy.size == 0:
        return []
    mask = np.isin(rd.poi_cats, list(cats))
    idx = np.nonzero(mask)[0]
    d = np.hypot(xy[idx, 0] - geo.cx, xy[idx, 1] - geo.cy)
    sel = idx[d <= radius]
    dd = d[d <= radius]
    order = np.argsort(dd)
    return [(rd.pois[int(sel[i])], float(dd[i])) for i in order]


def areas_near(ctx, cats: set[str], radius: float) -> list[tuple[dict, float]]:
    geo = ctx.geo
    out = []
    for a in ctx.region.areas:
        if a["cat"] not in cats:
            continue
        d = float(a["poly"].distance(geo.poly))
        if d <= radius:
            out.append((a, d))
    out.sort(key=lambda t: t[1])
    return out


_fmt_m = dist_text


# ----------------------------------------------------------------------------------------
@tool("osm.nightlife", "Оживлённость района", AGENT, ["OSM бары, клубы, кафе", "OSM пешеходные улицы"],
      "Считает бары, ночные клубы, кафе и рестораны вокруг отеля и близость пешеходных улиц и набережной.",
      applicable=_has_point)
def nightlife(ctx, params) -> ToolResult:
    bars = pois_near(ctx, {"bar", "pub"}, 300)
    clubs = pois_near(ctx, {"nightclub", "karaoke"}, 500)
    food = pois_near(ctx, FOOD, 300)
    geo = ctx.geo
    ped_d = None
    for w in ctx.region.roads["ways"]:
        if w["cls"] != "pedestrian":
            continue
        line = LineString(w["xy"])
        d = line.distance(Point(geo.cx, geo.cy))
        if d <= 400 and (ped_d is None or d < ped_d[0]):
            ped_d = (d, w.get("name"))
    score = len(bars) + 2 * len(clubs) + 0.25 * len(food) + (1.5 if ped_d and ped_d[0] < 250 else 0)
    level = "lively" if score >= 6 else "moderate" if score >= 2.5 else "calm"
    value = {"level": level, "score": round(score, 1), "bars_300m": len(bars), "nightclubs_500m": len(clubs),
             "food_300m": len(food), "pedestrian_m": round(ped_d[0]) if ped_d else None,
             "pedestrian_name": ped_d[1] if ped_d else None,
             "examples": [{"name": p.name, "cat": title(p.cat), "dist_m": round(d)} for p, d in (bars + clubs)[:5] if p.name]}
    level_ru = {"lively": "оживлённый, в гуще событий", "moderate": "умеренно оживлённый", "calm": "спокойный"}[level]
    parts = [f"Район {level_ru}"]
    parts.append(f"в 300 м баров и пабов: {len(bars)}, кафе и ресторанов: {len(food)}" if bars or food
                 else "в 300 м нет ни баров, ни кафе")
    if clubs:
        parts.append(f"ночных клубов в 500 м: {len(clubs)}")
    if ped_d and ped_d[0] < 250:
        parts.append(f"пешеходная улица{' ' + quote(ped_d[1]) if ped_d[1] else ''} в {_fmt_m(ped_d[0])}")
    return ToolResult(value, "; ".join(parts), 0.7, "osm", _detail(ctx, "amenity=bar/pub/nightclub/cafe/restaurant, highway=pedestrian"),
                      evidence={"score_formula": "бары + 2·клубы + 0,25·кафе + 1,5 (пешеходная улица ближе 250 м)"})


@tool("osm.construction", "Стройки рядом", AGENT, ["OSM landuse=construction", "OSM building=construction"],
      "Ищет отмеченные стройплощадки и строящиеся здания в 300 м от отеля.", applicable=_has_point)
def construction(ctx, params) -> ToolResult:
    near = areas_near(ctx, {"construction"}, 300)
    value = {"sites_300m": len(near), "nearest_m": round(near[0][1]) if near else None,
             "items": [{"name": a.get("name"), "dist_m": round(d), "area_m2": a.get("area_m2")} for a, d in near[:5]]}
    if near:
        s = f"Стройка в {_fmt_m(near[0][1])}" + (f" ({quote(near[0][0]['name'])})" if near[0][0].get("name") else "")
        if len(near) > 1:
            s += f", всего строек в 300 м: {len(near)}"
        s += " — днём возможен шум"
    else:
        s = "Стройплощадок в 300 м по данным OSM нет"
    return ToolResult(value, s, 0.55, "osm", _detail(ctx, "landuse=construction, building=construction"), ttl_days=30,
                      evidence={"note": "OSM обновляется с задержкой: стройки быстро появляются и исчезают, поэтому срок годности 30 дней"})


@tool("osm.night_services", "Что открыто ночью", AGENT, ["OSM аптеки, магазины, кафе", "OSM opening_hours"],
      "Разбирает opening_hours аптек, магазинов и кафе рядом и находит круглосуточные или открытые после полуночи.",
      applicable=_has_point)
def night_services(ctx, params) -> ToolResult:
    geo = ctx.geo
    known = total = 0
    best = {}
    items = []
    for cats, key, radius in (({"pharmacy"}, "pharmacy", 1500), ({"convenience"}, "shop", 900)):
        for p, d in pois_near(ctx, cats, radius):
            total += 1
            sched = parse_oh(p.tags.get("opening_hours"))
            if sched is None:
                continue
            known += 1
            if sched.always or sched.night_days(60) >= 5:
                if key not in best or d < best[key][1]:
                    best[key] = (p, d, sched)
    for key, (p, d, sched) in list(best.items()):
        res = geo.walk_to_point(p.x, p.y, radius=60, max_minutes=40)
        walk_m = round(res[0].length_m) if res else round(d * 1.3)
        best[key] = (p, walk_m, sched)
        items.append({"kind": key, "name": p.name or title(p.cat), "walk_m": walk_m, "hours": sched.summary(),
                      "opening_hours": p.tags.get("opening_hours")})
    food_night = 0
    for p, _d in pois_near(ctx, FOOD | {"bar", "pub", "nightclub"}, 500):
        sched = parse_oh(p.tags.get("opening_hours"))
        if sched is not None and (sched.always or sched.night_days(60) >= 3):
            food_night += 1
    share = known / total if total else 0.0
    ph = best.get("pharmacy")
    sh = best.get("shop")
    value = {
        "available": bool((ph and ph[1] <= 1000) or (sh and sh[1] <= 600)),
        "pharmacy_night_m": ph[1] if ph else None, "shop_night_m": sh[1] if sh else None,
        "food_night": food_night, "hours_known_share": round(share, 2), "items": items,
    }
    parts = []
    ph_where = (("прямо рядом" if ph[1] < NEAR_M else f"{_fmt_m(ph[1])} пешком") if ph else "")
    parts.append(f"{named('аптека', ph[0].name, 'ночная')} — {ph_where} ({ph[2].summary()})" if ph
                 else "круглосуточной аптеки в 1,5 км по данным OSM нет")
    parts.append(f"круглосуточный магазин в {_fmt_m(sh[1])}" if sh else "круглосуточного магазина рядом не найдено")
    if food_night:
        parts.append(f"заведений, открытых после полуночи, в 500 м: {food_night}")
    conf = 0.35 + 0.45 * share
    note = f"часы работы известны у {round(share * 100)}% аптек и магазинов рядом"
    return ToolResult(value, cap("; ".join(parts)), conf, "osm", _detail(ctx, "opening_hours"),
                      evidence={"hours_known": known, "places_checked": total, "note": note}, ttl_days=90)


@tool("osm.families", "Инфраструктура для детей", AGENT, ["OSM детские площадки", "OSM парки", "OSM больницы"],
      "Детские площадки в 400 м, путь до парка, расстояние до детской больницы.", applicable=_has_point)
def families(ctx, params) -> ToolResult:
    geo = ctx.geo
    pg = pois_near(ctx, {"playground"}, 400)
    pg_areas = areas_near(ctx, {"playground"}, 400)
    seen = {p.osm for p, _ in pg}
    count = len(pg) + sum(1 for a, _ in pg_areas if a["osm"] not in seen)
    nearest_pg = min([d for _, d in pg] + [d for _, d in pg_areas], default=None)
    park = geo.walk_to_areas("park", max_minutes=25)
    kh = pois_near(ctx, {"kids_hospital"}, 60000)
    value = {
        "playgrounds_400m": count, "playground_m": round(nearest_pg) if nearest_pg is not None else None,
        "park_m": round(park[0].length_m) if park else None, "park_minutes": round(park[0].minutes_to, 1) if park else None,
        "park_name": (park[1] or {}).get("name") if park else None,
        "kids_hospital_km": round(kh[0][1] / 1000, 1) if kh else None, "kids_hospital": kh[0][0].name if kh else None,
    }
    parts = [f"детских площадок в 400 м: {count}" + (f" (ближайшая в {_fmt_m(nearest_pg)})" if nearest_pg is not None else "")]
    if park:
        park = named("парк", value["park_name"])
        parts.append(f"{park} прямо у отеля" if value["park_m"] < 60 else f"{park} — {walk_min_text(value['park_minutes'])}")
    if kh:
        parts.append(f"детская больница в {value['kids_hospital_km']} км".replace(".", ","))
    return ToolResult(value, cap("; ".join(parts)), 0.68, "osm", _detail(ctx, "leisure=playground/park, amenity=hospital"))


DRIVE_OK = {"tertiary", "tertiary_link", "secondary", "secondary_link", "primary", "primary_link", "trunk", "trunk_link",
            "residential", "unclassified", "service", "living_street", "road"}


@tool("osm.car", "Доступность на машине", AGENT, ["OSM дороги", "OSM парковки", "OSM зарядные станции"],
      "Расстояние от здания до проезжей дороги, парковки в 300 м, зарядки для электромобилей в 2 км.", applicable=_has_point)
def car(ctx, params) -> ToolResult:
    geo = ctx.geo
    best = None
    for w in ctx.region.roads["ways"]:
        if w["cls"] not in DRIVE_OK or not w.get("drive", True) or w.get("access") in {"no", "private", "destination"} and w["cls"] == "service":
            continue
        xs = [p[0] for p in w["xy"]]
        ys = [p[1] for p in w["xy"]]
        if min(xs) > geo.cx + 300 or max(xs) < geo.cx - 300 or min(ys) > geo.cy + 300 or max(ys) < geo.cy - 300:
            continue
        d = LineString(w["xy"]).distance(geo.poly)
        if best is None or d < best[0]:
            best = (d, w)
    parks = pois_near(ctx, {"parking"}, 300)
    park_areas = areas_near(ctx, {"parking_area"}, 300)
    seen = {p.osm for p, _ in parks}
    n_parking = len(parks) + sum(1 for a, _ in park_areas if a["osm"] not in seen)
    nearest_park = min([d for _, d in parks] + [d for _, d in park_areas], default=None)
    ev = pois_near(ctx, {"ev_charging"}, 2000)
    ped = areas_near(ctx, {"pedestrian"}, 15)
    road_m = round(best[0]) if best else None
    value = {
        "car_to_door": bool(best and best[0] <= 30), "road_access_m": road_m,
        "pedestrian_zone": bool(ped) and (road_m is None or road_m > 40),
        "parking_300m": n_parking, "parking_m": round(nearest_park) if nearest_park is not None else None,
        "ev_chargers_2km": len(ev), "ev_m": round(ev[0][1]) if ev else None,
    }
    parts = []
    if value["car_to_door"]:
        parts.append("можно подъехать прямо к зданию" if road_m < 5 else f"можно подъехать к зданию (проезд в {road_m} м)")
    elif road_m is not None:
        parts.append(f"ближайший проезд в {road_m} м от здания — к самому входу не подъехать")
    if value["pedestrian_zone"]:
        parts.append("отель в пешеходной зоне")
    parts.append(f"парковок в 300 м: {n_parking}" + (f", ближайшая в {_fmt_m(nearest_park)}" if nearest_park is not None else ""))
    parts.append(f"зарядок для электромобилей в 2 км: {len(ev)}" + (f" (ближайшая в {_fmt_m(ev[0][1])})" if ev else ""))
    return ToolResult(value, cap("; ".join(parts)), 0.66, "osm", _detail(ctx, "highway=*, amenity=parking, amenity=charging_station"))


@tool("osm.green_walk", "Где гулять с собакой", AGENT, ["OSM парки и скверы", "OSM площадки для собак"],
      "Пеший путь до ближайшего парка, площадь зелени в 500 м, площадки для выгула собак.", applicable=_has_point)
def green_walk(ctx, params) -> ToolResult:
    geo = ctx.geo
    park = geo.walk_to_areas("park", max_minutes=30)
    zone = geo.poly.buffer(500)
    green = sum(a["poly"].intersection(zone).area for a in ctx.region.areas if a["cat"] == "park"
                and a["poly"].distance(geo.poly) <= 500)
    dog = pois_near(ctx, {"dog_park"}, 3000)
    dog_a = areas_near(ctx, {"dog_park"}, 3000)
    dog_d = min([d for _, d in dog] + [d for _, d in dog_a], default=None)
    value = {"park_m": round(park[0].length_m) if park else None, "park_minutes": round(park[0].minutes_to, 1) if park else None,
             "park_name": (park[1] or {}).get("name") if park else None, "green_ha_500m": round(green / 10000, 1),
             "dog_park_m": round(dog_d) if dog_d is not None else None}
    parts = []
    if park:
        pk = named("парк", value["park_name"]) if value["park_name"] else "парк или сквер"
        parts.append(f"{pk} прямо у отеля" if value["park_m"] < 60 else f"{pk} в {_fmt_m(park[0].length_m)} пешком")
    parts.append(f"зелёных зон в 500 м: {value['green_ha_500m']} га".replace(".", ",") if green >= 1000
                 else "зелёных зон в 500 м почти нет")
    parts.append(f"площадка для собак в {_fmt_m(dog_d)}" if dog_d is not None else "специальных площадок для собак рядом нет")
    return ToolResult(value, cap("; ".join(parts)), 0.66, "osm", _detail(ctx, "leisure=park/garden/dog_park"))


def _year(v: str | None) -> int | None:
    if not v:
        return None
    m = re.search(r"(1[6-9]\d{2}|20[0-2]\d)", v)
    if m:
        return int(m.group(1))
    m = re.search(r"C(1[6-9]|20)", v)
    if m:
        return (int(m.group(1)) - 1) * 100 + 50
    return None


@tool("osm.building_history", "История здания по OSM", AGENT, ["OSM теги здания и отеля"],
      "Год постройки (start_date), архитектурный стиль, статус объекта культурного наследия, ссылки на Wikidata/Википедию.",
      applicable=_has_point)
def building_history(ctx, params) -> ToolResult:
    tags = dict(ctx.hotel.osm_tags or {})
    b_idx = (ctx.hotel.building or {}).get("idx")
    if b_idx is not None:
        tags = {**ctx.region.buildings[b_idx].tags, **{k: v for k, v in tags.items() if v}}
    year = _year(tags.get("start_date") or tags.get("construction_date"))
    heritage = bool(tags.get("heritage") or tags.get("ref:okn") or tags.get("heritage:operator"))
    style = tags.get("building:architecture") or tags.get("architecture")
    style_ru = {"stalinist_neoclassicism": "сталинский неоклассицизм", "modern": "модерн", "art_nouveau": "модерн",
                "neoclassicism": "неоклассицизм", "classicism": "классицизм", "constructivism": "конструктивизм",
                "soviet_modernism": "советский модернизм", "contemporary": "современная архитектура"}.get(style or "", style)
    if year is None and not heritage and not style and not tags.get("wikidata") and not tags.get("wikipedia"):
        return ToolResult(None, "В OSM нет данных о годе постройки и истории здания", 0.0, "osm",
                          _detail(ctx, "start_date, heritage, building:architecture"), status="no_data")
    value = {"year_built": year, "heritage": heritage, "style": style_ru, "heritage_ref": tags.get("ref:okn"),
             "wikidata": tags.get("wikidata"), "wikipedia": tags.get("wikipedia"), "architect": tags.get("architect"),
             "historic": tags.get("historic")}
    parts = []
    if year:
        parts.append(f"здание {year} года")
    if style_ru:
        parts.append(f"архитектура: {style_ru}")
    if heritage:
        parts.append("объект культурного наследия" + (f" (ОКН {value['heritage_ref']})" if value["heritage_ref"] else ""))
    if value["architect"]:
        parts.append(f"архитектор {value['architect']}")
    if value["wikipedia"]:
        parts.append(f"статья в Википедии: {value['wikipedia']}")
    return ToolResult(value, cap("; ".join(parts)), 0.75, "osm", _detail(ctx, "start_date, heritage, building:architecture"))


@tool("osm.building_levels", "Этажность по OSM", AGENT, ["OSM building:levels / height"],
      "Этажность и высота здания отеля из тегов OSM (building:levels, height, building:part).", applicable=_has_point)
def building_levels(ctx, params) -> ToolResult:
    b = ctx.hotel.building or {}
    idx = b.get("idx")
    tags = ctx.hotel.osm_tags or {}
    lv_tag = tags.get("building:levels")
    if lv_tag and re.match(r"^\d+", lv_tag):
        lv = int(re.match(r"^\d+", lv_tag).group(0))
        return ToolResult({"levels": lv, "height_m": round(lv * 3.2 + 1, 1)}, f"{lv} {plural(lv, 'этаж', 'этажа', 'этажей')} (тег отеля building:levels)",
                          0.85, "osm", _detail(ctx, "building:levels у объекта отеля"))
    if idx is None:
        return ToolResult(None, "Контур здания отеля в OSM не найден", 0.0, "osm", _detail(ctx, "здания"), status="no_data")
    bld = ctx.region.buildings[idx]
    if bld.levels_src in {"osm_levels", "osm_height", "osm_parts"} and bld.levels:
        src_ru = {"osm_levels": "building:levels", "osm_height": "height", "osm_parts": "building:part"}[bld.levels_src]
        return ToolResult({"levels": bld.levels, "height_m": bld.height}, f"{bld.levels} {plural(bld.levels, 'этаж', 'этажа', 'этажей')} (тег {src_ru} здания в OSM)",
                          0.85, "osm", _detail(ctx, f"{src_ru} здания {bld.osm}"), evidence={"building": bld.osm})
    return ToolResult(None, "У здания в OSM нет этажности", 0.0, "osm", _detail(ctx, "building:levels"), status="no_data",
                      evidence={"building": bld.osm})


@tool("geo.levels_neighbors", "Этажность по соседним зданиям", "Геоаналитик", ["OSM этажность соседних зданий"],
      "Оценка этажности здания без тега: медиана этажности размеченных зданий в радиусе 150 м. "
      "Проверено кросс-валидацией на размеченных зданиях региона (см. метрики).", applicable=_has_point)
def levels_neighbors(ctx, params) -> ToolResult:
    idx = (ctx.hotel.building or {}).get("idx")
    stats = ctx.region.meta.get("stats", {}).get("levels_imputation") or {}
    detail = "Медиана этажности OSM-зданий в радиусе 150 м"
    if idx is None:
        return ToolResult(None, "Нет контура здания", 0.0, "osm", detail, status="no_data")
    b = ctx.region.buildings[idx]
    if b.levels_src != "neighbors" or not b.levels:
        return ToolResult(None, "Оценка по соседям не нужна или невозможна (мало размеченных соседей)", 0.0, "osm", detail,
                          status="no_data")
    within1 = stats.get("within1_neighbors") or 0.7
    conf = round(0.5 * within1, 2)
    return ToolResult({"levels": b.levels, "height_m": b.height},
                      f"≈{b.levels} {plural(b.levels, 'этаж', 'этажа', 'этажей')} — оценка по соседним зданиям (тега этажности у здания нет)", conf, "osm_estimate",
                      detail, evidence={"validation": stats, "note": f"на размеченных зданиях оценка попадает в ±1 этаж в {round(within1 * 100)}% случаев"})


TAG_RULES = {
    "pets": lambda v: {"allowed": v in {"yes", "leashed", "designated"}},
    "dog": lambda v: {"allowed": v in {"yes", "leashed", "designated"}, "animals": ["dog"] if v in {"yes", "leashed"} else []},
    "pets_allowed": lambda v: {"allowed": v == "yes"},
    "internet_access": lambda v: {"wifi": v in {"wlan", "yes", "wifi"}, "internet_access": v},
    "elevator": lambda v: {"available": v == "yes"},
    "wheelchair": lambda v: {"wheelchair": v if v in {"yes", "limited", "no"} else None},
}

TAG_TEXT = {
    "pets": lambda v: "с животными можно" if v in {"yes", "leashed", "designated"} else "с животными нельзя" if v == "no" else None,
    "dog": lambda v: "с собаками можно" if v in {"yes", "leashed", "designated"} else "с собаками нельзя" if v == "no" else None,
    "pets_allowed": lambda v: "с животными можно" if v == "yes" else "с животными нельзя" if v == "no" else None,
    "internet_access": lambda v: "есть Wi‑Fi" if v in {"wlan", "yes", "wifi"} else "интернета нет" if v == "no" else None,
    "elevator": lambda v: "есть лифт" if v == "yes" else "лифта нет" if v == "no" else None,
    "wheelchair": lambda v: {"yes": "доступно для гостей на коляске", "limited": "частично доступно для гостей на коляске",
                             "no": "не приспособлено для коляски"}.get(v),
}


def _tag_text(k: str, v: str) -> str:
    t = TAG_TEXT[k](v) if k in TAG_TEXT else None
    return f"{t} ({k}={v})" if t else f"{k}={v}"


@tool("osm.hotel_tag", "Теги объекта отеля в OSM", AGENT, ["OSM теги отеля"],
      "Читает указанные теги самого отеля в OSM (pets, wheelchair, internet_access, elevator и т. п.).",
      applicable=_has_point, params_schema={"tags": "list[str]"}, generic=True)
def hotel_tag(ctx, params) -> ToolResult:
    tags = ctx.hotel.osm_tags or {}
    found = {}
    value: dict = {}
    for t in params.get("tags", []):
        if t in tags:
            found[t] = tags[t]
            rule = TAG_RULES.get(t)
            if rule:
                value.update({k: v for k, v in rule(tags[t]).items() if v is not None})
            else:
                value[t] = tags[t]
    if not found:
        return ToolResult(None, f"В OSM у отеля нет тегов {', '.join(params.get('tags', []))}", 0.0, "osm",
                          _detail(ctx, "теги отеля"), status="no_data")
    s = "В OSM у отеля отмечено: " + ", ".join(_tag_text(k, v) for k, v in found.items())
    return ToolResult(value, s, 0.55, "osm", _detail(ctx, "теги отеля " + ", ".join(found)), evidence={"tags": found})


# ---------------- универсальные инструменты для новых характеристик ----------------
def _filter_ok(p, flt: str | None) -> bool | None:
    if not flt:
        return True
    sched = parse_oh(p.tags.get("opening_hours"))
    if sched is None:
        return None
    if flt == "24_7":
        return sched.always
    if flt == "night":
        return sched.always or sched.night_days(60) >= 5
    return True


@tool("osm.poi_nearby", "Объекты рядом (OSM)", AGENT, ["OSM POI"],
      "Сколько объектов заданных категорий в радиусе и до ближайшего; фильтр по часам работы (24_7 или night).",
      applicable=_has_point, generic=True,
      params_schema={"categories": "list[str] из справочника категорий", "radius_m": "int", "filter": "24_7 | night | null"})
def poi_nearby(ctx, params) -> ToolResult:
    cats = set(params.get("categories", []))
    radius = float(params.get("radius_m", 500))
    flt = params.get("filter")
    found, unknown = [], 0
    for p, d in pois_near(ctx, cats, radius):
        ok = _filter_ok(p, flt)
        if ok is None:
            unknown += 1
            continue
        if ok:
            found.append((p, d))
    for a, d in areas_near(ctx, cats, radius):
        found.append((type("A", (), {"name": a.get("name"), "cat": a["cat"]})(), d))
    found.sort(key=lambda t: t[1])
    label = ", ".join(plural_gen(c) for c in cats)
    value = {"available": bool(found), "count": len(found), "nearest_m": round(found[0][1]) if found else None,
             "items": [{"name": p.name, "cat": title(p.cat), "dist_m": round(d)} for p, d in found[:5]]}
    if found:
        s = f"В {_fmt_m(radius)}: {len(found)} ({label}), ближайший в {_fmt_m(found[0][1])}"
    else:
        s = f"В {_fmt_m(radius)} нет ({label}) по данным OSM"
    if flt:
        s += f"; часы работы неизвестны у {unknown}"
    conf = 0.7 if not flt else 0.7 * (len(found) + 1) / (len(found) + unknown + 1)
    return ToolResult(value, s, conf, "osm", _detail(ctx, label), evidence={"radius_m": radius, "filter": flt,
                                                                             "unknown_hours": unknown})


@tool("osm.poi_walk", "Пешком до ближайшего объекта", AGENT, ["OSM POI", "OSM пешеходный граф", "Copernicus DEM"],
      "Реальный пеший путь до ближайшего объекта заданных категорий с учётом рельефа.",
      applicable=_has_point, generic=True, params_schema={"categories": "list[str]", "max_minutes": "int", "filter": "24_7 | night | null"})
def poi_walk(ctx, params) -> ToolResult:
    cats = set(params.get("categories", []))
    max_min = float(params.get("max_minutes", 15))
    flt = params.get("filter")
    cands = [(p, d) for p, d in pois_near(ctx, cats, max_min * 90) if _filter_ok(p, flt)]
    best = None
    for p, _d in cands[:4]:
        res = ctx.geo.walk_to_point(p.x, p.y, radius=60, max_minutes=max_min * 2)
        if res and (best is None or res[0].minutes_to < best[1].minutes_to):
            best = (p, res[0])
    label = ", ".join(plural_gen(c) for c in cats)
    if best is None:
        return ToolResult({"available": False, "minutes_to": None, "walk_m": None},
                          f"Пешком в пределах {max_min:.0f} мин нет ({label})", 0.6, "osm", _detail(ctx, label))
    p, r = best
    value = {"available": r.minutes_to <= max_min, "minutes_to": round(r.minutes_to, 1), "minutes_back": round(r.minutes_back, 1),
             "walk_m": round(r.length_m), "climb_back_m": round(r.climb_back_m), "name": p.name}
    where = "прямо рядом" if value["walk_m"] < NEAR_M else f"{_fmt_m(value['walk_m'])}, {walk_min_text(value['minutes_to'])}"
    s = f"{cap(named(title(p.cat).lower(), p.name))}: {where}"
    return ToolResult(value, s, 0.75, "osm", _detail(ctx, label))


@tool("osm.area_nearby", "Площадные объекты рядом", AGENT, ["OSM парки, пляжи, стройки"],
      "Парки, пляжи, пешеходные зоны, стройки (полигоны) в радиусе: количество, расстояние, площадь.",
      applicable=_has_point, generic=True, params_schema={"categories": "list[str]", "radius_m": "int"})
def area_nearby(ctx, params) -> ToolResult:
    cats = set(params.get("categories", []))
    radius = float(params.get("radius_m", 500))
    near = areas_near(ctx, cats, radius)
    label = ", ".join(plural_gen(c) for c in cats)
    value = {"available": bool(near), "count": len(near), "nearest_m": round(near[0][1]) if near else None,
             "area_ha": round(sum(a.get("area_m2", 0) for a, _ in near) / 10000, 1)}
    s = (f"В {_fmt_m(radius)}: {len(near)} ({label}), ближайший в {_fmt_m(near[0][1])}" if near
         else f"В {_fmt_m(radius)} нет ({label})")
    return ToolResult(value, s, 0.7, "osm", _detail(ctx, label))

