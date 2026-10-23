"""Оценка шума у каждого фасада здания.

Упрощённая инженерная модель в духе CNOSSOS-EU / RLS-90, без данных о трафике:
  * дороги — линейные источники, уровень на 10 м зависит от класса дороги OSM,
    полос и разрешённой скорости; ночью трафик ниже;
  * железная дорога — линейный источник (пригородные «Ласточки» и грузовые поезда ночью);
  * бары и ночные клубы — точечные источники только ночью, если открыты после 23:00
    (по opening_hours) или это ночной клуб;
  * затухание: геометрическое расхождение, экранирование зданиями по растру застройки
    (луч источник→фасад), самоэкранирование здания для фасадов, «отвёрнутых» от источника.
Результат — ориентир в дБ(A) у фасада снаружи, а не замер. Уверенность модели
поэтому ограничена, а окончательный ответ про шумоизоляцию даёт отельер.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .opening_hours import parse as parse_oh
from .sun import angle_diff

# уровень у дороги на расстоянии 10 м, дБ(A), день
ROAD_LEVEL = {
    "motorway": 78,
    "trunk": 76,
    "primary": 73,
    "secondary": 70,
    "tertiary": 65,
    "unclassified": 60,
    "residential": 56,
    "living_street": 50,
    "service": 50,
    "motorway_link": 72,
    "trunk_link": 71,
    "primary_link": 68,
    "secondary_link": 66,
    "tertiary_link": 62,
}
NIGHT_DROP = {"motorway": 6, "trunk": 6, "primary": 7, "secondary": 8, "tertiary": 9}
NIGHT_DROP_DEFAULT = 10
RAIL_DAY, RAIL_NIGHT = 70, 67  # на 25 м
NIGHTLIFE = {"nightclub": 72, "bar": 62, "pub": 62, "karaoke": 66}
BACKGROUND_DAY, BACKGROUND_NIGHT = 45.0, 35.0
SAMPLE_STEP = 10.0
D0 = 10.0
RADIUS = 450.0
SCREEN_DB = 15.0
SELF_SCREEN_DB = 18.0

LEVELS = [(40, "тихо"), (45, "спокойно"), (55, "умеренный шум"), (60, "шумно"), (999, "очень шумно")]


def night_label(db: float) -> str:
    for limit, label in LEVELS:
        if db < limit:
            return label
    return LEVELS[-1][1]


@dataclass
class Source:
    kind: str
    name: str
    x: float
    y: float
    level_day: float  # уровень одиночной точки-образца на 10 м
    level_night: float
    ref: str


def _line_samples(coords: np.ndarray, step: float):
    pts = []
    for (x1, y1), (x2, y2) in zip(coords[:-1], coords[1:]):
        seg = math.hypot(x2 - x1, y2 - y1)
        if seg < 0.5:
            continue
        n = max(1, int(seg // step))
        for k in range(n):
            t = (k + 0.5) / n
            pts.append((x1 + (x2 - x1) * t, y1 + (y2 - y1) * t, seg / n))
    return pts


class NoiseEngine:
    def __init__(self, region):
        self.rd = region
        self._sources: list[Source] | None = None
        self._xy: np.ndarray | None = None

    def sources(self) -> list[Source]:
        if self._sources is not None:
            return self._sources
        out: list[Source] = []
        for w in self.rd.roads["ways"]:
            cls = w["cls"]
            if cls not in ROAD_LEVEL or w.get("tunnel"):
                continue
            base = ROAD_LEVEL[cls]
            lanes = w.get("lanes") or 0
            if lanes >= 4:
                base += 2
            ms = w.get("maxspeed") or 0
            if ms >= 70:
                base += 2
            elif 0 < ms <= 30:
                base -= 2
            night = base - NIGHT_DROP.get(cls.replace("_link", ""), NIGHT_DROP_DEFAULT)
            coords = np.asarray(w["xy"], dtype=float).reshape(-1, 2)
            for x, y, dl in _line_samples(coords, SAMPLE_STEP):
                corr = 10 * math.log10(dl / (math.pi * D0))
                out.append(Source("road", w.get("name") or _road_kind(cls), x, y, base + corr, night + corr, cls))
        for r in self.rd.rails:
            if r.get("tunnel") or r.get("usage") in {"industrial", "military"} or r.get("service") in {"yard", "siding", "spur"}:
                continue
            coords = np.asarray(r["xy"], dtype=float).reshape(-1, 2)
            for x, y, dl in _line_samples(coords, SAMPLE_STEP):
                # уровень на 25 м -> эквивалентный уровень на 10 м для линейного источника
                corr = 10 * math.log10(dl / (math.pi * D0)) + 10 * math.log10(25 / D0)
                out.append(
                    Source("rail", r.get("name") or "железная дорога", x, y, RAIL_DAY + corr, RAIL_NIGHT + corr, "railway")
                )
        for p in self.rd.pois:
            if p.cat not in NIGHTLIFE:
                continue
            sched = parse_oh(p.tags.get("opening_hours"))
            if sched is not None:
                late = sched.night_days(60) >= 1 or (sched.latest_close() or 0) > 23 * 60 + 30
            else:
                late = p.cat in {"nightclub", "karaoke"}
            if not late:
                continue
            out.append(Source("nightlife", p.name or _poi_kind(p.cat), p.x, p.y, -99, NIGHTLIFE[p.cat], p.cat))
        self._sources = out
        self._xy = np.array([[s.x, s.y] for s in out], dtype=float).reshape(-1, 2)
        return out

    def _screened(self, sx: float, sy: float, rx: float, ry: float, rz_rel: float, own_id: int | None) -> bool:
        """Есть ли между источником и фасадом здание выше луча (растр застройки 5 м)."""
        r = self.rd.rasters
        dist = math.hypot(rx - sx, ry - sy)
        n = int(dist // 5.0)
        if n < 3:
            return False
        t = np.linspace(0.08, 0.92, n)
        xs = sx + (rx - sx) * t
        ys = sy + (ry - sy) * t
        bh = r["bld_h"].nearest(xs, ys).astype(float)
        bid = r["bld_id"].nearest(xs, ys, fill=-1)
        if own_id is not None:
            bh[bid == own_id] = 0
        ray_h = 0.5 + (rz_rel - 0.5) * t  # высота луча над землёй (рельеф между точками не учитываем)
        return bool(np.any(bh > ray_h + 0.5))

    def facade_levels(self, facade, floor_height: float, own_id: int | None) -> dict:
        srcs = self.sources()
        xy = self._xy
        dx = xy[:, 0] - facade.x
        dy = xy[:, 1] - facade.y
        dist = np.hypot(dx, dy)
        near = np.nonzero(dist < RADIUS)[0]
        e_day = 10 ** (BACKGROUND_DAY / 10)
        e_night = 10 ** (BACKGROUND_NIGHT / 10)
        contrib: dict[tuple[str, str], list[float]] = {}
        for i in near:
            s = srcs[i]
            d = max(3.0, math.hypot(float(dist[i]), floor_height))
            az = math.degrees(math.atan2(dx[i], dy[i])) % 360
            off = abs(float(angle_diff(az, facade.normal_az)))
            att = 20 * math.log10(d / D0) + 0.005 * d
            if off > 95:
                att += SELF_SCREEN_DB
            elif off > 75:
                att += 4
            if self._screened(s.x, s.y, facade.x, facade.y, floor_height, own_id):
                att += SCREEN_DB
            ld = s.level_day - att
            ln = s.level_night - att
            e_day += 10 ** (ld / 10)
            e_night += 10 ** (ln / 10)
            key = (s.kind, s.name)
            acc = contrib.setdefault(key, [0.0, 0.0, 1e9])
            acc[0] += 10 ** (ld / 10)
            acc[1] += 10 ** (ln / 10)
            acc[2] = min(acc[2], float(dist[i]))
        day = 10 * math.log10(e_day)
        night = 10 * math.log10(e_night)
        top = sorted(contrib.items(), key=lambda kv: -kv[1][1])[:4]
        sources = []
        for (kind, name), (ed, en, dmin) in top:
            ln = 10 * math.log10(en) if en > 0 else -99
            if ln < night - 10:
                continue
            sources.append(
                {
                    "kind": kind,
                    "name": name,
                    "night_db": round(ln, 1),
                    "day_db": round(10 * math.log10(ed), 1) if ed > 0 else None,
                    "distance_m": round(dmin),
                }
            )
        return {"day_db": round(day, 1), "night_db": round(night, 1), "label": night_label(night), "sources": sources}


def _road_kind(cls: str) -> str:
    return {
        "motorway": "магистраль",
        "trunk": "магистраль без названия",
        "primary": "главная улица без названия",
        "secondary": "крупная улица без названия",
        "tertiary": "улица без названия",
        "residential": "жилая улица",
        "service": "внутриквартальный проезд",
        "unclassified": "дорога без названия",
        "living_street": "жилая зона",
    }.get(cls.replace("_link", ""), "дорога")


def _poi_kind(cat: str) -> str:
    return {"nightclub": "ночной клуб", "bar": "бар", "pub": "паб", "karaoke": "караоке"}.get(cat, cat)
