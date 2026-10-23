"""Что видно из окна: трассировка лучей по рельефу (Copernicus DEM) и застройке (OSM).

Для каждого фасада здания отеля и каждого этажа строится пучок лучей в секторе
обзора окна (±70° от нормали фасада, шаг 1°). Вдоль луча берутся отметки
поверхности: рельеф + высота зданий/деревьев с поправкой на кривизну Земли и
рефракцию. Точка видна, если её угол возвышения не меньше максимума углов всех
точек ближе к наблюдателю. Отсюда получаем: виден ли участок моря, гор,
что образует линию горизонта и где Солнце садится за горизонт.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from shapely.geometry import Polygon
from shapely.geometry.polygon import orient

from .proj import COMPASS_8, compass_index
from .sun import angle_diff

EARTH_R = 6_371_000.0
REFRACTION_K = 0.13
FLOOR_H = 3.0
EYE_H = 1.6
NEAR_STEP = 2.5
NEAR_MAX = 2500.0
FAR_STEP = 45.0
FAR_MAX = 50_000.0
FOV_HALF = 70  # пучок лучей ±70° (окно — ±60°, балкон — до ±70°)
WINDOW_HALF = 60
MOUNTAIN_ELEV = 450.0
OWN_BUILDING_SKIP = 20.0  # м: игнорируем собственное здание у самого окна

CLS_LAND, CLS_SEA, CLS_BUILDING, CLS_TREES, CLS_MOUNTAIN = 0, 1, 2, 3, 4
CLS_NAMES = {CLS_LAND: "рельеф", CLS_SEA: "море", CLS_BUILDING: "здания", CLS_TREES: "деревья",
             CLS_MOUNTAIN: "горы"}


@dataclass
class Facade:
    key: str
    idx: int
    normal_az: float
    x: float
    y: float
    length: float

    def as_dict(self) -> dict:
        return {"key": self.key, "normal_az": round(self.normal_az, 1), "length_m": round(self.length, 1),
                "x": round(self.x, 1), "y": round(self.y, 1)}


def building_facades(poly: Polygon, min_len: float = 4.0, offset: float = 1.5) -> list[Facade]:
    """Фасады здания по сторонам света (8 румбов) с внешними нормалями."""
    poly = orient(poly, sign=1.0)  # обход против часовой — нормаль (dy, -dx) смотрит наружу
    coords = np.asarray(poly.exterior.coords)
    groups: dict[int, list[tuple[float, float, float, float, float]]] = {}
    for (x1, y1), (x2, y2) in zip(coords[:-1], coords[1:]):
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy)
        if length < min_len:
            continue
        nx, ny = dy / length, -dx / length
        az = math.degrees(math.atan2(nx, ny)) % 360
        groups.setdefault(compass_index(az), []).append((length, az, (x1 + x2) / 2, (y1 + y2) / 2, nx * 0 + ny * 0))
    if not groups:  # очень маленькое здание — строим 4 условных фасада от центроида
        c = poly.centroid
        r = max(3.0, math.sqrt(max(poly.area, 1.0)) / 2)
        out = []
        for idx in (0, 2, 4, 6):
            az = idx * 45.0
            out.append(Facade(COMPASS_8[idx], idx, az, c.x + (r + offset) * math.sin(math.radians(az)),
                              c.y + (r + offset) * math.cos(math.radians(az)), 2 * r))
        return out
    out = []
    for idx, edges in groups.items():
        total = sum(e[0] for e in edges)
        sx = sum(e[0] * math.sin(math.radians(e[1])) for e in edges)
        sy = sum(e[0] * math.cos(math.radians(e[1])) for e in edges)
        az = math.degrees(math.atan2(sx, sy)) % 360
        longest = max(edges, key=lambda e: e[0])
        ex = longest[2] + offset * math.sin(math.radians(longest[1]))
        ey = longest[3] + offset * math.cos(math.radians(longest[1]))
        out.append(Facade(COMPASS_8[idx], idx, az, ex, ey, total))
    out.sort(key=lambda f: f.idx)
    return out


def _curvature(d: np.ndarray) -> np.ndarray:
    return d * d / (2 * EARTH_R) * (1 - REFRACTION_K)


class ViewEngine:
    def __init__(self, region):
        self.rd = region
        self.d_near = np.arange(4.0, NEAR_MAX, NEAR_STEP)
        self.d_far = np.arange(NEAR_MAX, FAR_MAX, FAR_STEP)
        self.d = np.concatenate([self.d_near, self.d_far])
        self.n_near = self.d_near.size
        self.curv = _curvature(self.d)

    # ------------------------------------------------------------------
    def profiles(self, x0: float, y0: float, azimuths: np.ndarray, own_id: int | None):
        """Высоты поверхности и классы вдоль лучей. Возвращает (z[A,N], cls[A,N])."""
        r = self.rd.rasters
        az = np.radians(np.asarray(azimuths, dtype=float))
        sx, cy = np.sin(az)[:, None], np.cos(az)[:, None]
        xs = x0 + sx * self.d[None, :]
        ys = y0 + cy * self.d[None, :]
        A, N = xs.shape
        nn = self.n_near
        z = np.empty((A, N), dtype=np.float32)
        cls = np.zeros((A, N), dtype=np.uint8)

        # ближняя зона: застройка, деревья, море (5 м), рельеф (DTM 25 м)
        xn, yn = xs[:, :nn], ys[:, :nn]
        ground_n = r["dtm_near"].bilinear(xn, yn)
        far_n = r["dem_far"].bilinear(xn, yn)
        ground_n = np.where(np.isnan(ground_n), far_n, ground_n)
        ground_n = np.where(np.isnan(ground_n), 0.0, ground_n)
        bh = r["bld_h"].nearest(xn, yn).astype(np.float32)
        bid = r["bld_id"].nearest(xn, yn, fill=-1)
        vh = r["veg_h"].nearest(xn, yn).astype(np.float32)
        sea_n = r["sea"].nearest(xn, yn, fill=255)
        outside = sea_n == 255
        if np.any(outside):
            sea_n = np.where(outside, r["sea_far"].nearest(xn, yn, fill=0), sea_n)
        if own_id is not None:
            own = (bid == own_id) & (self.d_near[None, :] < OWN_BUILDING_SKIP)
            bh[own] = 0
        is_sea = (sea_n > 0) & (bh == 0)
        top = np.maximum(bh, vh)
        zn = np.where(is_sea, 0.0, ground_n + top)
        cls_n = np.full(zn.shape, CLS_LAND, dtype=np.uint8)
        cls_n[vh > 0] = CLS_TREES
        cls_n[bh >= vh] = np.where(bh[bh >= vh] > 0, CLS_BUILDING, cls_n[bh >= vh])
        cls_n[is_sea] = CLS_SEA
        cls_n[(cls_n == CLS_LAND) & (ground_n >= MOUNTAIN_ELEV)] = CLS_MOUNTAIN
        z[:, :nn] = zn
        cls[:, :nn] = cls_n

        # дальняя зона: только рельеф (DSM 90 м) и море
        xf, yf = xs[:, nn:], ys[:, nn:]
        gf = r["dem_far"].bilinear(xf, yf)
        sea_f = r["sea_far"].nearest(xf, yf, fill=0) > 0
        nodata = np.isnan(gf)
        gf = np.where(nodata, 0.0, gf)
        zf = np.where(sea_f, 0.0, gf)
        cls_f = np.where(sea_f, CLS_SEA, np.where(gf >= MOUNTAIN_ELEV, CLS_MOUNTAIN, CLS_LAND)).astype(np.uint8)
        # вне покрытия DEM считаем морем только если это продолжение моря (юго-запад региона)
        cls_f[nodata] = CLS_SEA
        z[:, nn:] = zf
        cls[:, nn:] = cls_f

        z -= self.curv[None, :].astype(np.float32)
        return z, cls

    # ------------------------------------------------------------------
    def analyse_point(self, x0: float, y0: float, ground: float, floors: list[int], azimuths: np.ndarray,
                      own_id: int | None):
        """Для каждого этажа и азимута: угол горизонта, что его образует, видно ли море/горы, дистанция преграды."""
        z, cls = self.profiles(x0, y0, azimuths, own_id)
        d = self.d[None, :].astype(np.float32)
        res = []
        for f in floors:
            zf = ground + EYE_H + FLOOR_H * (f - 1)
            t = (z - zf) / d
            run = np.maximum.accumulate(t, axis=1)
            prev = np.concatenate([np.full((t.shape[0], 1), -np.inf, dtype=np.float32), run[:, :-1]], axis=1)
            visible = t >= prev - 1e-7
            hz_idx = np.argmax(t, axis=1)
            horizon = np.degrees(np.arctan(t[np.arange(t.shape[0]), hz_idx]))
            sky_cls = cls[np.arange(t.shape[0]), hz_idx]
            sky_d = self.d[hz_idx]
            sea_vis = np.any(visible & (cls == CLS_SEA), axis=1)
            sea_d = np.where(np.any(visible & (cls == CLS_SEA), axis=1),
                             self.d[np.argmax(visible & (cls == CLS_SEA), axis=1)], np.nan)
            mtn_vis = np.any(visible & (cls == CLS_MOUNTAIN) & (d >= 2000), axis=1)
            tan2 = math.tan(math.radians(2.0))
            blocked = t > tan2
            has_block = np.any(blocked, axis=1)
            b_idx = np.argmax(blocked, axis=1)
            block_d = np.where(has_block, self.d[b_idx], np.nan)
            block_cls = np.where(has_block, cls[np.arange(t.shape[0]), b_idx], 255)
            res.append({
                "floor": f, "eye_z": zf, "horizon": horizon, "sky_cls": sky_cls, "sky_d": sky_d, "sea_vis": sea_vis,
                "sea_d": sea_d, "mtn_vis": mtn_vis, "block_d": block_d, "block_cls": block_cls,
            })
        return res

    # ------------------------------------------------------------------
    def line_of_sight(self, x0: float, y0: float, z_eye: float, xt: float, yt: float, zt: float,
                      own_id: int | None, target_radius: float = 30.0) -> tuple[bool, float, str]:
        """Прямая видимость точки-цели. Возвращает (видно, запас по углу в градусах, что мешает)."""
        dist = math.hypot(xt - x0, yt - y0)
        if dist < 5:
            return True, 90.0, ""
        az = math.degrees(math.atan2(xt - x0, yt - y0)) % 360
        z, cls = self.profiles(x0, y0, np.array([az]), own_id)
        z, cls = z[0], cls[0]
        mask = self.d < dist - target_radius
        t_target = (zt - _curvature(np.array([dist]))[0] - z_eye) / dist
        if not np.any(mask):
            return True, 90.0, ""
        t = (z[mask] - z_eye) / self.d[mask]
        k = int(np.argmax(t))
        margin = math.degrees(math.atan(t_target)) - math.degrees(math.atan(t[k]))
        return margin > 0, margin, CLS_NAMES.get(int(cls[mask][k]), "")


def floors_for(levels: int | None, default: int = 5, cap: int = 30) -> list[int]:
    n = max(1, min(cap, levels or default))
    if n <= 12:
        return list(range(1, n + 1))
    # высотки: этажи 1–10 подробно, дальше с шагом
    return sorted(set(list(range(1, 11)) + list(range(12, n + 1, 2)) + [n]))


def window_mask(azimuths: np.ndarray, normal_az: float, half: float = WINDOW_HALF) -> np.ndarray:
    return np.abs(angle_diff(azimuths, normal_az)) <= half
