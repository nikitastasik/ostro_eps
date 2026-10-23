"""Геоинструменты агента-геоаналитика: виды, закат, шум, пешие маршруты, транспорт."""

from __future__ import annotations

import math

import numpy as np

from ..geo.analysis import month_ranges
from ..geo.sun import angle_diff
from .base import ToolResult, cap, quote, tool

AGENT = "Геоаналитик"
LEVEL_FACTOR = {"osm_levels": 1.0, "osm_height": 1.0, "osm_parts": 1.0, "hotelier": 1.0, "hotelier_confirmed": 1.0,
                "osm": 1.0, "osm_estimate": 0.8, "neighbors": 0.8, "default": 0.6, "none": 0.6}
LEVEL_SRC_RU = {"osm_levels": "тег building:levels в OSM", "osm_height": "тег height в OSM",
                "osm_parts": "3D-части здания в OSM", "hotelier": "ответ отельера", "hotelier_confirmed": "подтверждено отельером",
                "osm": "OSM", "osm_estimate": "оценка по соседним зданиям", "neighbors": "оценка по соседним зданиям",
                "default": "предположение по типу здания",
                "none": "неизвестна — считали до 5 этажа"}


def _has_geo(ctx, params) -> tuple[bool, str]:
    b = ctx.hotel.building or {}
    if b.get("x") is None:
        return False, "нет координат отеля в локальной сетке региона"
    near = ctx.region.meta["near"]
    x, y = b["x"], b["y"]
    if not (near["x0"] <= x <= near["x0"] + near["nx"] * near["res"] and near["y0"] <= y <= near["y0"] + near["ny"] * near["res"]):
        return False, "отель вне зоны детальной модели застройки"
    if b.get("idx") is None:
        return True, "контура здания нет — фасады строим условно вокруг точки отеля (уверенность ниже)"
    return True, "есть контур здания, рельеф и модель застройки вокруг"


def _view_confidence(ctx) -> tuple[float, dict]:
    geo = ctx.geo
    q = geo.data_quality()
    f_levels = LEVEL_FACTOR.get(geo.levels_src, 0.6)
    f_foot = 1.0 if geo.footprint_known else 0.65
    f_obst = 0.7 + 0.3 * q["known_height_share"]
    conf = 0.85 * f_levels * f_foot * f_obst
    factors = {
        "base_model": 0.85, "levels": geo.levels, "levels_source": geo.levels_src, "levels_factor": f_levels,
        "footprint_known": geo.footprint_known, "footprint_factor": f_foot,
        "neighbours_known_height_share": q["known_height_share"], "neighbours_300m": q["buildings_300m"],
        "obstacles_factor": round(f_obst, 2),
    }
    return conf, factors


def _source_detail(ctx) -> str:
    geo = ctx.geo
    meta = ctx.region.meta["sources"]
    return (f"Рельеф: {meta['dem']['title']}; здания: OSM (снимок {meta['osm']['snapshot']}); "
            f"этажность отеля: {LEVEL_SRC_RU.get(geo.levels_src, geo.levels_src)}")


def _strip(value: dict) -> dict:
    return {k: v for k, v in value.items() if k != "summary"}


@tool("geo.sea_view", "Видимость моря из окон", AGENT, ["Copernicus DEM", "OSM здания", "OSM береговая линия"],
      "Трассировка лучей из окна каждого фасада на каждом этаже по рельефу и застройке; доля обзора, где видна поверхность моря.",
      applicable=_has_geo, cost="high")
def sea_view(ctx, params) -> ToolResult:
    v = ctx.geo.sea_view()
    conf, factors = _view_confidence(ctx)
    return ToolResult(_strip(v), v["summary"], conf, "geo", _source_detail(ctx),
                      evidence={"confidence_factors": factors, "floors_checked": v["floors_checked"],
                                "method": "line-of-sight, шаг 1°, окно ±60° от нормали фасада, кривизна Земли и рефракция"})


@tool("geo.sunset_view", "Закат из окон по месяцам", AGENT, ["Copernicus DEM", "OSM здания", "NOAA Solar Calculator"],
      "Азимут и траектория Солнца перед закатом для 15-го числа каждого месяца + линия горизонта из окна каждого фасада и этажа.",
      applicable=_has_geo, cost="high")
def sunset_view(ctx, params) -> ToolResult:
    v = ctx.geo.sunset_view()
    conf, factors = _view_confidence(ctx)
    conf *= 0.97
    ev = {"confidence_factors": factors, "sunset_azimuths": v.get("sunset_azimuths"),
          "rule": "закат «виден», если высота горизонта по азимуту заката ≤ 4° (полностью — ≤ 1°) и азимут в ±60° от нормали фасада"}
    return ToolResult(_strip(v), v["summary"], conf, "geo", _source_detail(ctx), evidence=ev)


@tool("geo.mountain_view", "Видимость гор и вершин", AGENT, ["Copernicus DEM", "OSM вершины natural=peak"],
      "Лучи до 50 км: видимые участки рельефа выше 450 м и прямая видимость именованных вершин из OSM.",
      applicable=_has_geo, cost="high")
def mountain_view(ctx, params) -> ToolResult:
    v = ctx.geo.mountain_view()
    conf, factors = _view_confidence(ctx)
    return ToolResult(_strip(v), v["summary"], conf * 0.95, "geo", _source_detail(ctx),
                      evidence={"confidence_factors": factors, "peaks_checked": len(ctx.region.peaks)})


@tool("geo.landmark_view", "Видимость городских ориентиров", AGENT, ["Copernicus DEM", "OSM здания", "список ориентиров"],
      "Прямая видимость от окна до вершины ориентира (Морской вокзал, маяк, башня на Ахуне и др.).",
      applicable=_has_geo, cost="medium")
def landmark_view(ctx, params) -> ToolResult:
    v = ctx.geo.landmark_view()
    conf, factors = _view_confidence(ctx)
    return ToolResult(_strip(v), v["summary"], conf * 0.9, "geo", _source_detail(ctx),
                      evidence={"confidence_factors": factors, "landmarks_checked": [lm["name"] for lm in ctx.region.landmarks]})


@tool("geo.window_outlook", "Что перед окнами", AGENT, ["OSM здания", "Copernicus DEM"],
      "Расстояние до первой преграды выше уровня глаз в секторе ±20° от нормали фасада на каждом этаже.",
      applicable=_has_geo, cost="high")
def window_outlook(ctx, params) -> ToolResult:
    v = ctx.geo.window_outlook()
    conf, factors = _view_confidence(ctx)
    return ToolResult(_strip(v), v["summary"], conf * 0.9, "geo", _source_detail(ctx),
                      evidence={"confidence_factors": factors})


@tool("geo.noise", "Шум у фасадов", AGENT, ["OSM дороги (класс, полосы, скорость)", "OSM железная дорога", "OSM бары и клубы (opening_hours)"],
      "Инженерная модель: линейные источники-дороги и ж/д, точечные — ночные заведения; расхождение, экранирование зданиями.",
      applicable=_has_geo, cost="medium")
def noise(ctx, params) -> ToolResult:
    geo = ctx.geo
    v = geo.noise()
    q = geo.data_quality()
    conf = 0.62 * (1.0 if geo.footprint_known else 0.7) * (0.85 + 0.15 * q["known_height_share"])
    ev = {"model": "CNOSSOS-подобная упрощённая: L(10 м) по классу дороги, −10·lg(d) для линии, −15 дБ экран, −18 дБ самоэкранирование",
          "receiver_height_m": 4.5, "note": "оценка снаружи у окна; внутри номера с закрытыми окнами на 25–35 дБ тише"}
    meta = ctx.region.meta["sources"]
    return ToolResult(_strip(v), v["summary"], conf, "geo", f"Модель шума по данным OSM (снимок {meta['osm']['snapshot']})",
                      evidence=ev)


def _rail_ru(kind: str | None) -> str:
    return {"underpass": "по подземному переходу под железной дорогой", "bridge": "по надземному переходу над железной дорогой",
            "level": "через железнодорожные пути по наземному переходу"}.get(kind or "", "")


def _beach_text(name: str | None, surface_ru: str | None) -> str:
    """«галечный пляж „Маяк“»: без повторов, когда в названии из OSM уже есть слово «пляж»."""
    name = " ".join((name or "").split())
    kind = f"{surface_ru} пляж" if surface_ru else "пляж"
    if not name or name.lower() == "пляж":
        return kind
    if name.lower().startswith("пляж "):
        rest = name[5:].strip()
        if rest[:1].isupper() or rest[:1] in "\"«":
            rest = rest.strip("\"«»")
            return f"{kind} {quote(rest)}"
        return f"{kind} {rest}"
    if "пляж" in name.lower():
        return name + (f" ({surface_ru})" if surface_ru else "")
    return f"{kind} {quote(name)}"


@tool("geo.walk_beach", "Пеший маршрут до пляжа", AGENT, ["OSM пешеходный граф", "OSM пляжи natural=beach", "Copernicus DEM"],
      "Кратчайший по времени пеший путь до ближайшего пляжа; время по функции Тоблера (уклон), лестницы, переходы, ж/д.",
      applicable=_has_geo, cost="medium")
def walk_beach(ctx, params) -> ToolResult:
    geo = ctx.geo
    res = geo.walk_to_areas("beach")
    meta = ctx.region.meta["sources"]
    detail = f"Пешеходный граф OSM (снимок {meta['osm']['snapshot']}) + рельеф Copernicus DEM"
    if res is None:
        return ToolResult(None, "Пеший маршрут до пляжа не найден в графе OSM", 0.0, "geo", detail, status="no_data",
                          note="нет пешеходной связности или пляжей рядом")
    route, area, start_d = res
    straight = float(area["poly"].distance(geo.poly)) if area else None
    surface = (area or {}).get("surface")
    surface_ru = {"pebblestone": "галечный", "gravel": "галечный", "pebbles": "галечный", "sand": "песчаный",
                  "concrete": "бетонный", "stone": "каменистый"}.get(surface or "", None)
    major = [c for c in route.crossings]
    value = {
        "walk_m": round(route.length_m), "straight_m": round(straight) if straight is not None else None,
        "minutes_to": round(route.minutes_to, 1), "minutes_back": round(route.minutes_back, 1),
        "climb_to_m": round(route.climb_to_m), "climb_back_m": round(route.climb_back_m),
        "max_slope_pct": round(route.max_slope_pct), "steps": route.steps_segments,
        "major_crossings": len(major), "crossings": [{"road": c["road"], "signals": c["signals"]} for c in major],
        "rail": (route.rail or {}).get("kind", "none"),
        "beach": {"name": (area or {}).get("name"), "surface": surface_ru, "fee": (area or {}).get("fee"),
                  "access": (area or {}).get("access")},
        **geo.route_payload(route),
    }
    parts = [f"{value['walk_m']} м пешком (≈{value['minutes_to']:.0f} мин туда, ≈{value['minutes_back']:.0f} мин обратно)"]
    if straight is not None and value["walk_m"] > 1.6 * max(straight, 50):
        parts.append(f"по прямой всего {round(straight)} м — путь в обход")
    if value["climb_back_m"] >= 15:
        parts.append(f"обратно подъём {value['climb_back_m']} м")
    if value["steps"]:
        parts.append("есть лестницы")
    if major:
        parts.append("переход через дорогу: " + ", ".join(sorted({c['road'] for c in major}))
                     + (" (светофор)" if all(c["signals"] for c in major) else ""))
    if value["rail"] != "none":
        parts.append(_rail_ru(value["rail"]))
    if value["beach"]["name"] or surface_ru:
        parts.append(_beach_text(value["beach"]["name"], surface_ru))
    conf = 0.86 if start_d < 60 else 0.66
    return ToolResult(value, "; ".join(parts), conf, "geo", detail,
                      evidence={"start_offset_m": round(start_d), "algorithm": "Дейкстра по времени, Тоблер: v = 6·exp(−3.5·|s+0.05|) км/ч"})


@tool("geo.walk_center", "Пеший маршрут до центра", AGENT, ["OSM пешеходный граф", "Copernicus DEM"],
      "Пеший путь до центральной точки региона (Морской вокзал) с учётом рельефа.", applicable=_has_geo, cost="medium")
def walk_center(ctx, params) -> ToolResult:
    geo = ctx.geo
    center = ctx.region.meta.get("center")
    meta = ctx.region.meta["sources"]
    detail = f"Пешеходный граф OSM (снимок {meta['osm']['snapshot']}) + рельеф Copernicus DEM"
    if not center:
        return ToolResult(None, "Центр региона не задан", 0.0, "geo", detail, status="not_applicable")
    cx, cy = ctx.region.xy(center["lon"], center["lat"])
    res = geo.walk_to_point(float(cx), float(cy), radius=150)
    if res is None:
        return ToolResult(None, "Пеший маршрут до центра не найден", 0.0, "geo", detail, status="no_data")
    route, start_d = res
    value = {"walk_m": round(route.length_m), "minutes_to": round(route.minutes_to, 1),
             "minutes_back": round(route.minutes_back, 1), "climb_to_m": round(route.climb_to_m),
             "climb_back_m": round(route.climb_back_m), "target": center["name"], **geo.route_payload(route)}
    s = f"До {center['name_gen']} {value['walk_m']} м пешком, ≈{value['minutes_to']:.0f} мин"
    if value["climb_to_m"] >= 15 or value["climb_back_m"] >= 15:
        s += f" (набор высоты {value['climb_to_m']} м туда / {value['climb_back_m']} м обратно)"
    return ToolResult(value, s, 0.85 if start_d < 60 else 0.65, "geo", detail, evidence={"target": center})


@tool("geo.transport", "Транспорт: остановка, станция, аэропорт", AGENT,
      ["OSM остановки и станции", "OSM маршруты поездов", "OSM дорожный граф"],
      "Ближайшая остановка, пеший путь до ж/д станции, есть ли с неё прямые поезда до аэропорта (route=train в OSM), время на машине до аэропорта.",
      applicable=_has_geo, cost="medium")
def transport(ctx, params) -> ToolResult:
    rd = ctx.region
    geo = ctx.geo
    meta = rd.meta["sources"]
    detail = f"OSM (снимок {meta['osm']['snapshot']}): остановки, станции, маршруты поездов, дорожный граф"
    xy = rd.poi_xy
    cats = rd.poi_cats
    d = np.hypot(xy[:, 0] - geo.cx, xy[:, 1] - geo.cy)
    value: dict = {}
    bus = np.nonzero(cats == "bus_stop")[0]
    if bus.size:
        i = bus[np.argmin(d[bus])]
        value["bus_stop_m"] = round(float(d[i]))
        value["bus_stop_name"] = rd.pois[i].name
    stations = [i for i in np.nonzero(cats == "station")[0] if d[i] < 6000]
    best = None
    for i in sorted(stations, key=lambda i: d[i])[:3]:
        p = rd.pois[i]
        res = geo.walk_to_point(p.x, p.y, radius=150, max_minutes=60)
        if res is None:
            continue
        route, _ = res
        if best is None or route.minutes_to < best[1].minutes_to:
            best = (p, route)
    if best:
        p, route = best
        value["station"] = p.name
        value["station_m"] = round(route.length_m)
        value["station_minutes"] = round(route.minutes_to, 1)
        airport_station = rd.meta.get("airport", {}).get("station_osm")
        trains = [r for r in rd.meta.get("train_routes", []) if p.osm in r.get("stop_osm", []) and airport_station in r.get("stop_osm", [])]
        value["airport_train"] = bool(trains)
        if trains:
            value["airport_train_routes"] = sorted({r.get("name") or r.get("ref") or "поезд" for r in trains})[:3]
    ap = rd.meta.get("airport")
    if ap:
        dg = rd.drive_graph
        s_node, s_d = dg.nearest_node(geo.cx, geo.cy, max_dist=500)
        ax, ay = rd.xy(ap["lon"], ap["lat"])
        t_node, _ = dg.nearest_node(float(ax), float(ay), max_dist=800)
        if s_node is not None and t_node is not None:
            tr = dg.travel(s_node, t_node)
            if tr:
                value["airport_drive_min"] = round(tr[0] / 60)
                value["airport_drive_km"] = round(tr[1] / 1000, 1)
    if not value:
        return ToolResult(None, "Нет данных о транспорте рядом", 0.0, "geo", detail, status="no_data")
    parts = []
    if "bus_stop_m" in value:
        parts.append(f"остановка в {value['bus_stop_m']} м" + (f" ({quote(value['bus_stop_name'])})" if value.get("bus_stop_name") else ""))
    if "station" in value:
        walk = "меньше минуты" if value["station_minutes"] < 1 else f"{value['station_minutes']:.0f} мин"
        parts.append(f"ж/д станция {quote(value['station'])} — {walk} пешком")
        if value.get("airport_train"):
            parts.append("с неё ходят прямые поезда до аэропорта")
    if "airport_drive_min" in value:
        parts.append(f"до аэропорта ≈{value['airport_drive_min']} мин на машине без пробок ({value['airport_drive_km']} км)"
                     .replace(".", ","))
    return ToolResult(value, cap("; ".join(parts)), 0.7, "geo", detail,
                      evidence={"drive_speeds": "городские скорости по классу дороги, без учёта пробок"})


@tool("geo.target_view", "Видимость заданной точки", AGENT, ["Copernicus DEM", "OSM здания"],
      "Прямая видимость из окон отеля до произвольной точки (lat, lon, высота над землёй, м). Подходит для новых характеристик вида.",
      applicable=_has_geo, params_schema={"lat": "float", "lon": "float", "height_m": "float", "name": "str"},
      generic=True, cost="medium")
def target_view(ctx, params) -> ToolResult:
    geo = ctx.geo
    rd = ctx.region
    tx, ty = rd.xy(float(params["lon"]), float(params["lat"]))
    tz = float(rd.ground(np.array([float(tx)]), np.array([float(ty)]))[0]) + float(params.get("height_m", 20))
    best = None
    for f in geo.facades:
        az = math.degrees(math.atan2(float(tx) - f.x, float(ty) - f.y)) % 360
        if abs(float(angle_diff(az, f.normal_az))) > 60:
            continue
        for fl in geo.floors:
            z_eye = geo.ground + 1.6 + 3.0 * (fl - 1)
            ok, _, _ = geo.view_engine.line_of_sight(f.x, f.y, z_eye, float(tx), float(ty), tz, geo.own_id)
            if ok:
                if best is None or fl < best[1]:
                    best = (f.key, fl)
                break
    name = params.get("name", "объект")
    conf, factors = _view_confidence(ctx)
    if best:
        value = {"available": True, "min_floor": best[1], "facade": best[0]}
        summary = f"{name} виден из окон стороны {best[0]} с {best[1]}-го этажа"
    else:
        value = {"available": False, "min_floor": None, "facade": None}
        summary = f"{name} из окон не виден"
    return ToolResult(value, summary, conf * 0.9, "geo", _source_detail(ctx), evidence={"confidence_factors": factors})


def months_text(months: list[int]) -> str:
    return month_ranges(months)
