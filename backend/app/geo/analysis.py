"""Геоаналитика конкретного отеля: виды, закат, горы, ориентиры, шум, маршруты.

Результаты кешируются на объекте HotelGeo, поэтому несколько характеристик,
опирающихся на один и тот же пучок лучей, считаются за один проход.
"""

from __future__ import annotations

import math
from functools import cached_property

import numpy as np
from shapely.geometry import Point

from .noise import NoiseEngine
from .proj import COMPASS_8, COMPASS_8_ACC, COMPASS_8_GEN, COMPASS_8_NOM
from .sun import angle_diff, monthly_sunsets
from .views import (
    CLS_BUILDING,
    CLS_LAND,
    CLS_MOUNTAIN,
    CLS_NAMES,
    CLS_SEA,
    CLS_TREES,
    FOV_HALF,
    WINDOW_HALF,
    ViewEngine,
    building_facades,
    floors_for,
)

MONTHS_NOM = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь",
              "ноябрь", "декабрь"]
MONTHS_SHORT = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]

SEA_GLIMPSE, SEA_PARTIAL, SEA_WIDE = 3, 15, 45
SUN_FULL, SUN_PARTIAL = 1.0, 4.0

_engines: dict[int, tuple[ViewEngine, NoiseEngine]] = {}


def engines(region) -> tuple[ViewEngine, NoiseEngine]:
    key = id(region)
    if key not in _engines:
        _engines[key] = (ViewEngine(region), NoiseEngine(region))
    return _engines[key]


def facade_gen(key: str) -> str:
    return COMPASS_8_GEN[COMPASS_8.index(key)]


def facade_nom(key: str) -> str:
    return COMPASS_8_NOM[COMPASS_8.index(key)]


def facade_acc(key: str) -> str:
    return COMPASS_8_ACC[COMPASS_8.index(key)]


def month_ranges(months: list[int], short: bool = False) -> str:
    """[10, 11, 12, 1, 2] -> «октябрь–февраль» (с переходом через год)."""
    if not months:
        return ""
    names = MONTHS_SHORT if short else MONTHS_NOM
    s = sorted(set(months))
    if len(s) == 12:
        return "круглый год"
    runs = []
    start = prev = s[0]
    for m in s[1:]:
        if m == prev + 1:
            prev = m
            continue
        runs.append((start, prev))
        start = prev = m
    runs.append((start, prev))
    if len(runs) > 1 and runs[0][0] == 1 and runs[-1][1] == 12:
        first = runs.pop(0)
        last = runs.pop()
        runs.append((last[0], first[1]))
    parts = []
    for a, b in runs:
        parts.append(names[a - 1] if a == b else f"{names[a - 1]}–{names[b - 1]}")
    return ", ".join(parts)


def ordinal_floor(n: int) -> str:
    return f"{n}-го этажа"


class HotelGeo:
    def __init__(self, region, hotel: dict, levels: int | None = None, levels_src: str | None = None):
        self.rd = region
        self.hotel = hotel
        self.view_engine, self.noise_engine = engines(region)
        b_idx = hotel.get("building")
        self.building = region.buildings[b_idx] if b_idx is not None else None
        self.own_id = b_idx
        if self.building is not None:
            self.poly = self.building.poly
            self.footprint_known = True
        else:
            self.poly = Point(hotel["x"], hotel["y"]).buffer(9, quad_segs=2)
            self.footprint_known = False
        if levels is None and self.building is not None:
            levels = self.building.levels
            levels_src = self.building.levels_src
        self.levels = levels
        self.levels_src = levels_src or "none"
        self.levels_assumed = levels is None
        self.floors = floors_for(levels, default=5)
        c = self.poly.centroid
        self.cx, self.cy = c.x, c.y

    # ------------------------------------------------------------------
    @cached_property
    def facades(self):
        return building_facades(self.poly)

    @cached_property
    def ground(self) -> float:
        ring = np.asarray(self.poly.exterior.coords)
        pts = np.vstack([ring, [[self.cx, self.cy]]])
        z = self.rd.ground(pts[:, 0], pts[:, 1])
        return float(np.percentile(z, 25))

    @cached_property
    def lonlat(self) -> tuple[float, float]:
        lon, lat = self.rd.lonlat(self.cx, self.cy)
        return float(lon), float(lat)

    @cached_property
    def view(self) -> dict:
        """Пучки лучей для всех фасадов и этажей (тяжёлый расчёт, ~0.5 с)."""
        out = {}
        for f in self.facades:
            az = (f.normal_az + np.arange(-FOV_HALF, FOV_HALF + 1)) % 360
            res = self.view_engine.analyse_point(f.x, f.y, self.ground, self.floors, az, self.own_id)
            out[f.key] = {"facade": f, "az": az, "floors": {r["floor"]: r for r in res}}
        return out

    def data_quality(self) -> dict:
        """Насколько можно доверять геометрии вокруг: доля зданий с известной высотой в 300 м."""
        r = self.rd.rasters
        g = r["bld_id"]
        rad = 300
        r0, c0 = g.cell_of(self.cx - rad, self.cy - rad)
        r1, c1 = g.cell_of(self.cx + rad, self.cy + rad)
        r0, c0 = max(0, r0), max(0, c0)
        sub = g.a[r0:r1, c0:c1]
        ids = np.unique(sub[sub >= 0])
        if ids.size == 0:
            return {"known_height_share": 1.0, "buildings_300m": 0}
        known = sum(1 for i in ids if i < len(self.rd.buildings) and self.rd.buildings[i].height_src in
                    {"osm_height", "osm_levels", "osm_parts"})
        return {"known_height_share": round(known / ids.size, 2), "buildings_300m": int(ids.size)}

    # ------------------------------------------------------------------
    def sea_view(self) -> dict:
        by_facade = {}
        for key, v in self.view.items():
            f = v["facade"]
            win = np.abs(angle_diff(v["az"], f.normal_az)) <= WINDOW_HALF
            widths = {}
            nearest = None
            for fl, r in v["floors"].items():
                w = int(np.sum(r["sea_vis"] & win))
                widths[fl] = w
                if w and nearest is None:
                    nearest = float(np.nanmin(np.where(win, r["sea_d"], np.nan)))
            min_partial = next((fl for fl in sorted(widths) if widths[fl] >= SEA_PARTIAL), None)
            min_glimpse = next((fl for fl in sorted(widths) if widths[fl] >= SEA_GLIMPSE), None)
            maxw = max(widths.values()) if widths else 0
            quality = "wide" if maxw >= SEA_WIDE else "partial" if maxw >= SEA_PARTIAL else "glimpse" if maxw >= SEA_GLIMPSE else "none"
            # чем закрыто море с первого этажа (для объяснения)
            r1 = v["floors"][min(v["floors"])]
            toward_sea = win & ~r1["sea_vis"]
            blockers = r1["sky_cls"][toward_sea]
            blocked_by = None
            if blockers.size:
                vals, cnt = np.unique(blockers[blockers != CLS_SEA], return_counts=True)
                if vals.size:
                    blocked_by = CLS_NAMES.get(int(vals[np.argmax(cnt)]))
            by_facade[key] = {
                "available": min_partial is not None, "min_floor": min_partial, "glimpse_floor": min_glimpse,
                "quality": quality, "max_width_deg": maxw, "width_by_floor": {str(k): w for k, w in widths.items()},
                "nearest_sea_m": round(nearest) if nearest is not None and not math.isnan(nearest) else None,
                "blocked_low_by": blocked_by, "normal_az": round(f.normal_az),
            }
        avail = {k: v for k, v in by_facade.items() if v["available"]}
        order = {"wide": 3, "partial": 2, "glimpse": 1, "none": 0}
        best = None
        if by_facade:
            best = max(by_facade, key=lambda k: (order[by_facade[k]["quality"]], by_facade[k]["max_width_deg"],
                                                 -(by_facade[k]["min_floor"] or 99)))
        min_floor = min((v["min_floor"] for v in avail.values()), default=None)
        quality = by_facade[best]["quality"] if best else "none"
        value = {
            "available": bool(avail), "min_floor": min_floor, "quality": quality,
            "max_width_deg": by_facade[best]["max_width_deg"] if best else 0, "best_facade": best if avail else None,
            "by_facade": by_facade, "floors_checked": self.floors[-1],
        }
        value["summary"] = self._sea_summary(value)
        return value

    def _sea_summary(self, v: dict) -> str:
        bf = v["by_facade"]
        if v["available"]:
            sides = sorted((k for k in bf if bf[k]["available"]), key=lambda k: bf[k]["min_floor"])
            main = sides[0]
            s = f"Море видно из окон {facade_gen(main)} стороны"
            s += " с первого этажа" if bf[main]["min_floor"] == 1 else f" начиная с {ordinal_floor(bf[main]['min_floor'])}"
            s += f" (обзор до {bf[main]['max_width_deg']}°)"
            others = [k for k in sides[1:]]
            if others:
                s += "; также из окон " + ", ".join(f"{facade_gen(k)} стороны — с {bf[k]['min_floor']}-го этажа" for k in others[:2])
            low = bf[main].get("blocked_low_by")
            if bf[main]["min_floor"] and bf[main]["min_floor"] > 1 and low:
                s += f". Ниже обзор закрывают {low}"
            return s
        glimpse = [k for k in bf if bf[k]["quality"] == "glimpse"]
        if glimpse:
            k = glimpse[0]
            return f"Море видно лишь краешком (до {bf[k]['max_width_deg']}°) из окон {facade_gen(k)} стороны с {bf[k]['glimpse_floor']}-го этажа"
        blockers = [bf[k].get("blocked_low_by") for k in bf if bf[k].get("blocked_low_by")]
        tail = f": обзор закрывают {blockers[0]}" if blockers else ""
        return f"Из окон море не видно ни с одного этажа (проверено до {self.floors[-1]}-го){tail}"

    # ------------------------------------------------------------------
    def sunset_view(self) -> dict:
        lon, lat = self.lonlat
        suns = monthly_sunsets(lat, lon, tz_hours=self.rd.meta.get("timezone_offset", 3))
        by_facade: dict[str, dict] = {}
        by_month: dict[str, dict] = {}
        for key, v in self.view.items():
            f = v["facade"]
            az_arr = v["az"]
            fm = {}
            for s in suns:
                d = float(angle_diff(s["azimuth"], f.normal_az))
                if abs(d) > WINDOW_HALF:
                    continue
                i = int(np.argmin(np.abs(angle_diff(az_arr, s["azimuth"]))))
                best = None
                for fl in sorted(v["floors"]):
                    r = v["floors"][fl]
                    h = float(r["horizon"][i])
                    cls = int(r["sky_cls"][i])
                    sky_d = float(r["sky_d"][i])
                    # Закат «виден», если солнце доходит до горизонта (≤ 1°) или садится за далёкие
                    # горы/холмы (≤ 4°, дальше 1.5 км). Солнце, ушедшее за соседний дом, — не вид на закат.
                    far_terrain = cls in (CLS_MOUNTAIN, CLS_LAND) and sky_d >= 1500
                    if h <= SUN_FULL or (h <= SUN_PARTIAL and far_terrain):
                        over = "sea" if cls == CLS_SEA else "mountains" if cls == CLS_MOUNTAIN else (
                            "buildings" if cls == CLS_BUILDING else "trees" if cls == CLS_TREES else "land")
                        hide = self._hide_minutes(s["path"], az_arr, r["horizon"])
                        best = {"min_floor": fl, "over": over, "full": h <= SUN_FULL, "horizon_deg": round(h, 2),
                                "hide_min_before": hide, "azimuth": s["azimuth"], "time": s["local_time"]}
                        break
                if best:
                    fm[str(s["month"])] = best
            by_facade[key] = {"months": fm, "available": bool(fm),
                              "min_floor": min((m["min_floor"] for m in fm.values()), default=None),
                              "month_list": sorted(int(m) for m in fm),
                              "over_sea_months": sorted(int(m) for m, x in fm.items() if x["over"] == "sea")}
        for s in suns:
            cands = [(k, bf["months"][str(s["month"])]) for k, bf in by_facade.items() if str(s["month"]) in bf["months"]]
            if cands:
                k, m = min(cands, key=lambda c: (c[1]["min_floor"], c[1]["over"] != "sea"))
                by_month[str(s["month"])] = {"facade": k, **m}
        months = sorted(int(m) for m in by_month)
        over_sea = sorted(int(m) for m, x in by_month.items() if x["over"] == "sea")
        best = max(by_facade, key=lambda k: (len(by_facade[k]["months"]), -(by_facade[k]["min_floor"] or 99)), default=None)
        value = {
            "available": bool(months), "min_floor": min((x["min_floor"] for x in by_month.values()), default=None),
            "months": months, "over_sea_months": over_sea,
            "best_facade": best if months else None, "by_facade": {k: {kk: vv for kk, vv in bf.items() if kk != "months"} | {"detail": bf["months"]}
                                                                  for k, bf in by_facade.items()},
            "by_month": by_month,
            "sunset_azimuths": {str(s["month"]): s["azimuth"] for s in suns},
            "sunset_times": {str(s["month"]): s["local_time"] for s in suns},
        }
        # единые поля по фасаду для поиска «номер с закатом»
        for bf in value["by_facade"].values():
            bf["months"] = bf.pop("month_list")
        value["summary"] = self._sunset_summary(value)
        return value

    @staticmethod
    def _hide_minutes(path, az_arr, horizon) -> int | None:
        """За сколько минут до заката Солнце уходит за препятствие (None — видно до горизонта)."""
        for m, el, az in path:
            i = int(np.argmin(np.abs(angle_diff(az_arr, az))))
            if abs(float(angle_diff(az_arr[i], az))) > 1.5:
                continue
            if el <= float(horizon[i]):
                return int(m)
        return None

    def _sunset_summary(self, v: dict) -> str:
        if not v["available"]:
            az = v["sunset_azimuths"]
            return (f"Из окон заката не видно: солнце садится на {int(min(az.values()))}–{int(max(az.values()))}° "
                    f"(запад-юго-запад зимой, северо-запад летом), а в эту сторону окна не выходят или вид закрыт")
        bm = v["by_month"]
        groups: dict[tuple, list[int]] = {}
        for m, x in bm.items():
            groups.setdefault((x["facade"], x["min_floor"], x["over"]), []).append(int(m))
        parts = []
        over_ru = {"sea": "над морем", "mountains": "за горами", "land": "за холмами",
                   "buildings": "над крышами города", "trees": "над кронами деревьев"}
        for (fac, fl, over), ms in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0][1])):
            floor_txt = "с 1-го этажа" if fl == 1 else f"с {fl}-го этажа"
            parts.append(f"{month_ranges(ms)} — {over_ru.get(over, '')} из окон {facade_gen(fac)} стороны {floor_txt}")
            if len(parts) == 3:
                break
        return "Закат виден: " + "; ".join(parts)

    # ------------------------------------------------------------------
    def mountain_view(self) -> dict:
        by_facade = {}
        peaks_seen: dict[str, dict] = {}
        for key, v in self.view.items():
            f = v["facade"]
            win = np.abs(angle_diff(v["az"], f.normal_az)) <= WINDOW_HALF
            widths = {fl: int(np.sum(r["mtn_vis"] & win)) for fl, r in v["floors"].items()}
            min_floor = next((fl for fl in sorted(widths) if widths[fl] >= 5), None)
            by_facade[key] = {"available": min_floor is not None, "min_floor": min_floor,
                              "max_width_deg": max(widths.values()) if widths else 0}
            for p in self.rd.peaks:
                dist = math.hypot(p["x"] - f.x, p["y"] - f.y)
                if dist > 60000 or dist < 500:
                    continue
                az = math.degrees(math.atan2(p["x"] - f.x, p["y"] - f.y)) % 360
                if abs(float(angle_diff(az, f.normal_az))) > WINDOW_HALF:
                    continue
                for fl in self.floors:
                    z_eye = self.ground + 1.6 + 3.0 * (fl - 1)
                    ok, margin, _ = self.view_engine.line_of_sight(f.x, f.y, z_eye, p["x"], p["y"], p["ele"], self.own_id,
                                                                   target_radius=60)
                    if ok:
                        prev = peaks_seen.get(p["name"])
                        if prev is None or fl < prev["min_floor"]:
                            peaks_seen[p["name"]] = {"name": p["name"], "ele": round(p["ele"]), "dist_km": round(dist / 1000, 1),
                                                     "min_floor": fl, "facade": key}
                        break
        peaks = sorted(peaks_seen.values(), key=lambda p: (-p["ele"]))[:6]
        for p in peaks:
            bf = by_facade[p["facade"]]
            if not bf["available"] or (bf["min_floor"] or 99) > p["min_floor"]:
                bf["available"] = True
                bf["min_floor"] = min(bf["min_floor"] or 99, p["min_floor"])
        avail = {k: v for k, v in by_facade.items() if v["available"]}
        min_floor = min((v["min_floor"] for v in avail.values()), default=None)
        best = min(avail, key=lambda k: (avail[k]["min_floor"], -avail[k]["max_width_deg"])) if avail else None
        value = {"available": bool(avail), "min_floor": min_floor, "best_facade": best, "peaks": peaks,
                 "by_facade": by_facade}
        if avail:
            s = f"Горы видны из окон {facade_gen(best)} стороны с {min_floor}-го этажа"
            if peaks:
                s += ": " + ", ".join(f"{p['name']} ({p['ele']} м, {p['dist_km']} км)" for p in peaks[:3])
            value["summary"] = s
        else:
            value["summary"] = "Горы из окон не видны — обзор закрывают соседние здания и ближние склоны"
        return value

    # ------------------------------------------------------------------
    def landmark_view(self) -> dict:
        seen = []
        by_facade: dict[str, dict] = {f.key: {"available": False, "min_floor": None, "landmarks": []} for f in self.facades}
        for lm in self.rd.landmarks:
            lx, ly = lm["x"], lm["y"]
            base = lm.get("absolute_ground")
            if base is None:
                base = float(self.rd.ground(np.array([lx]), np.array([ly]))[0])
            target_z = float(base) + lm["height"]
            best = None
            for f in self.facades:
                dist = math.hypot(lx - f.x, ly - f.y)
                if dist > lm.get("max_km", 15) * 1000 or dist < 30:
                    continue
                az = math.degrees(math.atan2(lx - f.x, ly - f.y)) % 360
                if abs(float(angle_diff(az, f.normal_az))) > WINDOW_HALF:
                    continue
                for fl in self.floors:
                    z_eye = self.ground + 1.6 + 3.0 * (fl - 1)
                    ok, margin, _ = self.view_engine.line_of_sight(f.x, f.y, z_eye, lx, ly, target_z, self.own_id,
                                                                   target_radius=lm.get("radius", 25))
                    if ok:
                        if best is None or fl < best["min_floor"]:
                            best = {"name": lm["name"], "min_floor": fl, "facade": f.key, "dist_km": round(dist / 1000, 1)}
                        bf = by_facade[f.key]
                        bf["available"] = True
                        bf["min_floor"] = min(bf["min_floor"] or 99, fl)
                        bf["landmarks"].append(lm["name"])
                        break
            if best:
                seen.append(best)
        seen.sort(key=lambda x: x["min_floor"])
        value = {"available": bool(seen), "landmarks": seen,
                 "min_floor": min((x["min_floor"] for x in seen), default=None), "by_facade": by_facade}
        if seen:
            value["summary"] = "Из окон видно: " + "; ".join(
                f"{x['name']} — {facade_gen(x['facade'])} сторона с {x['min_floor']}-го этажа" for x in seen[:4])
        else:
            value["summary"] = "Ни один из городских ориентиров не попадает в прямую видимость из окон"
        return value

    # ------------------------------------------------------------------
    def window_outlook(self) -> dict:
        by_facade = {}
        for key, v in self.view.items():
            f = v["facade"]
            centre = np.abs(angle_diff(v["az"], f.normal_az)) <= 20
            per_floor = {}
            first_cls = None
            for fl, r in sorted(v["floors"].items()):
                bd = r["block_d"][centre]
                med = float(np.median(np.where(np.isnan(bd), 1e4, bd))) if bd.size else 1e4
                per_floor[fl] = med
                if first_cls is None:
                    bc = r["block_cls"][centre]
                    bc = bc[bc != 255]
                    if bc.size:
                        vals, cnt = np.unique(bc, return_counts=True)
                        first_cls = int(vals[np.argmax(cnt)])
            first = per_floor[min(per_floor)]
            open_from = next((fl for fl in sorted(per_floor) if per_floor[fl] >= 50), None)
            what = {CLS_BUILDING: "building", CLS_TREES: "trees", CLS_LAND: "slope", CLS_MOUNTAIN: "slope"}.get(first_cls)
            by_facade[key] = {"first_obstacle_m": None if first >= 1e4 else round(first), "first_obstacle": what if first < 1e4 else None,
                              "wall_m_floor1": round(first) if (first < 1e4 and what == "building") else None,
                              "open_from_floor": open_from, "open_view": open_from is not None,
                              "available": open_from is not None, "min_floor": open_from}
        open_any = any(v["open_view"] for v in by_facade.values())
        walls = [v["wall_m_floor1"] for v in by_facade.values() if v["wall_m_floor1"] is not None]
        value = {"open_view": open_any, "min_wall_m": min(walls) if walls else None, "by_facade": by_facade}
        parts = []
        tight = sorted((k for k, v in by_facade.items() if v["wall_m_floor1"] is not None and v["wall_m_floor1"] < 25),
                       key=lambda k: by_facade[k]["wall_m_floor1"])
        if tight:
            parts.append("на нижних этажах окна " + ", ".join(f"{facade_gen(k)} стороны смотрят на соседний дом в {by_facade[k]['wall_m_floor1']} м"
                                                                for k in tight[:2]))
        green = [k for k, v in by_facade.items() if v["first_obstacle"] == "trees" and (v["first_obstacle_m"] or 99) < 30]
        if green:
            parts.append("окна " + ", ".join(facade_gen(k) for k in green[:2]) + " стороны выходят в зелень деревьев")
        opened = [k for k, v in by_facade.items() if v["open_view"]]
        if opened:
            k = min(opened, key=lambda k: by_facade[k]["open_from_floor"])
            parts.append(f"открытый вид дальше 50 м — из окон {facade_gen(k)} стороны с {by_facade[k]['open_from_floor']}-го этажа")
        value["summary"] = ("; ".join(parts) or "Перед окнами нет близких преград")
        value["summary"] = value["summary"][0].upper() + value["summary"][1:]
        return value

    # ------------------------------------------------------------------
    def noise(self) -> dict:
        h = 4.5 if (self.levels or 2) >= 2 else 1.6
        by_facade = {}
        for f in self.facades:
            lv = self.noise_engine.facade_levels(f, h, self.own_id)
            by_facade[f.key] = {**lv, "normal_az": round(f.normal_az)}
        if not by_facade:
            return {"summary": "Нет данных о фасадах", "by_facade": {}}
        quiet = min(by_facade, key=lambda k: by_facade[k]["night_db"])
        loud = max(by_facade, key=lambda k: by_facade[k]["night_db"])
        value = {
            "night_db": by_facade[quiet]["night_db"], "day_db": by_facade[quiet]["day_db"],
            "night_db_worst": by_facade[loud]["night_db"], "day_db_worst": by_facade[loud]["day_db"],
            "quiet_side": quiet, "loud_side": loud, "label": by_facade[quiet]["label"],
            "label_worst": by_facade[loud]["label"], "by_facade": by_facade,
        }
        s = f"Ночью тише всего у {facade_gen(quiet)} стороны: ≈{by_facade[quiet]['night_db']:.0f} дБ ({by_facade[quiet]['label']})"
        if loud != quiet and by_facade[loud]["night_db"] - by_facade[quiet]["night_db"] >= 3:
            src = by_facade[loud]["sources"][0]["name"] if by_facade[loud]["sources"] else "дорога"
            s += (f"; {facade_nom(loud)} сторона — ≈{by_facade[loud]['night_db']:.0f} дБ ({by_facade[loud]['label']}), "
                  f"главный источник: {src}")
        value["summary"] = s
        return value

    # ------------------------------------------------------------------
    def _start_node(self):
        g = self.rd.walk_graph
        ring = np.asarray(self.poly.exterior.coords)
        best, best_d = None, 1e9
        for x, y in np.vstack([ring, [[self.cx, self.cy]]]):
            n = g.nearest_node(x, y, max_dist=200)
            if n is None:
                continue
            d = math.hypot(g.xy[n][0] - x, g.xy[n][1] - y)
            if d < best_d:
                best, best_d = n, d
        return best, best_d

    def walk_to_areas(self, cat: str, max_minutes: float = 60):
        g = self.rd.walk_graph
        start, start_d = self._start_node()
        if start is None:
            return None
        targets: set[int] = set()
        target_area: dict[int, dict] = {}
        for a in self.rd.areas:
            if a["cat"] != cat:
                continue
            poly = a["poly"]
            if poly.distance(Point(self.cx, self.cy)) > 4000:
                continue
            c = poly.centroid
            near = g.nodes_within(c.x, c.y, math.sqrt(poly.area) + 400)
            zone = poly.buffer(15)
            for n in near:
                x, y = g.xy[n]
                if zone.contains(Point(x, y)):
                    targets.add(int(n))
                    target_area[int(n)] = a
        if not targets:
            return None
        res = g.shortest(start, targets, max_time=max_minutes * 60)
        if res is None:
            return None
        nodes, edges = res
        route = g.describe(nodes, edges)
        return route, target_area.get(nodes[-1]), start_d

    def walk_to_point(self, x: float, y: float, radius: float = 120, max_minutes: float = 90):
        g = self.rd.walk_graph
        start, start_d = self._start_node()
        if start is None:
            return None
        targets = set(int(n) for n in g.nodes_within(x, y, radius))
        if not targets:
            return None
        res = g.shortest(start, targets, max_time=max_minutes * 60)
        if res is None:
            return None
        nodes, edges = res
        return g.describe(nodes, edges), start_d

    def route_payload(self, route) -> dict:
        lon, lat = self.rd.lonlat(np.array([c[0] for c in route.coords]), np.array([c[1] for c in route.coords]))
        dist = [0.0]
        for (x1, y1), (x2, y2) in zip(route.coords[:-1], route.coords[1:]):
            dist.append(dist[-1] + math.hypot(x2 - x1, y2 - y1))
        step = max(1, len(route.coords) // 120)
        return {
            "line": [[round(float(a), 6), round(float(b), 6)] for a, b in zip(lon[::step], lat[::step])] +
                    [[round(float(lon[-1]), 6), round(float(lat[-1]), 6)]],
            "profile": [[round(d), round(z, 1)] for d, z in list(zip(dist, route.elevations))[::step]] +
                       [[round(dist[-1]), round(route.elevations[-1], 1)]],
        }
