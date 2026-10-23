"""Загрузка подготовленных данных региона (data/<region>/).

Все тяжёлые данные (растры рельефа и застройки, графы, POI) собираются заранее
скриптом scripts/build_region.py из открытых источников и лежат в репозитории
в компактном виде, поэтому приложение стартует за секунды и не ходит в сеть.
"""

from __future__ import annotations

import gzip
import json
import logging
import threading
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import LineString, Polygon

from .config import settings
from .geo.proj import LocalProjection
from .geo.raster import Grid, rasterize_polygon

log = logging.getLogger(__name__)


def _load_json(path: Path):
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@dataclass
class Building:
    idx: int
    osm: str
    poly: Polygon
    height: float
    height_src: str
    levels: int | None
    levels_src: str
    btype: str
    name: str | None = None
    tags: dict = field(default_factory=dict)
    dsm_h: float | None = None


@dataclass
class Poi:
    idx: int
    osm: str
    cat: str
    name: str | None
    x: float
    y: float
    tags: dict


class RegionData:
    def __init__(self, folder: Path):
        self.folder = folder
        self.meta = _load_json(folder / "region.json")
        o = self.meta["origin"]
        self.proj = LocalProjection(o["lat"], o["lon"])
        self._lock = threading.RLock()
        self._overrides_applied: list[int] = []

    # ---------- растры ----------
    @cached_property
    def rasters(self) -> dict[str, Grid]:
        z = np.load(self.folder / "rasters.npz")
        near = self.meta["near"]
        out = {}
        for key in ("bld_h", "bld_id", "veg_h", "sea"):
            out[key] = Grid(z[key].copy(), near["x0"], near["y0"], near["res"])
        dn = self.meta["dem_near"]
        out["dtm_near"] = Grid(z["dtm_near"].astype(np.float32), dn["x0"], dn["y0"], dn["res"])
        out["dsm_near"] = Grid(z["dsm_near"].astype(np.float32), dn["x0"], dn["y0"], dn["res"])
        df = self.meta["dem_far"]
        out["dem_far"] = Grid(z["dem_far"].astype(np.float32), df["x0"], df["y0"], df["res"])
        out["sea_far"] = Grid(z["sea_far"], df["x0"], df["y0"], df["res"])
        return out

    def ground(self, x, y):
        """Отметка земли (DTM, очищенная от застройки), м."""
        r = self.rasters
        v = r["dtm_near"].bilinear(x, y)
        far = r["dem_far"].bilinear(x, y)
        v = np.where(np.isnan(v), far, v)
        return np.where(np.isnan(v), 0.0, v)

    # ---------- векторные слои ----------
    @cached_property
    def buildings(self) -> list[Building]:
        raw = _load_json(self.folder / "buildings.json.gz")
        out = []
        for i, b in enumerate(raw):
            ring = np.asarray(b["xy"], dtype=float).reshape(-1, 2)
            poly = Polygon(ring)
            if not poly.is_valid:
                poly = poly.buffer(0)
            out.append(Building(
                idx=i, osm=b["osm"], poly=poly, height=b["h"], height_src=b["h_src"],
                levels=b.get("lv"), levels_src=b.get("lv_src", "none"), btype=b.get("t", "yes"),
                name=b.get("name"), tags=b.get("tags", {}), dsm_h=b.get("dsm_h"),
            ))
        return out

    @cached_property
    def building_tree(self) -> shapely.STRtree:
        return shapely.STRtree([b.poly for b in self.buildings])

    @cached_property
    def pois(self) -> list[Poi]:
        raw = _load_json(self.folder / "pois.json.gz")
        return [Poi(i, p["osm"], p["cat"], p.get("name"), p["x"], p["y"], p.get("tags", {})) for i, p in enumerate(raw)]

    @cached_property
    def poi_xy(self) -> np.ndarray:
        return np.array([[p.x, p.y] for p in self.pois], dtype=float).reshape(-1, 2)

    @cached_property
    def poi_cats(self) -> np.ndarray:
        return np.array([p.cat for p in self.pois])

    @cached_property
    def areas(self) -> list[dict]:
        raw = _load_json(self.folder / "areas.json.gz")
        for a in raw:
            a["poly"] = Polygon(np.asarray(a["xy"], dtype=float).reshape(-1, 2)).buffer(0)
        return raw

    @cached_property
    def roads(self) -> dict:
        return _load_json(self.folder / "roads.json.gz")

    @cached_property
    def streets(self) -> list[tuple[str, LineString]]:
        """Именованные улицы OSM: (название, линия) — для подписей тёзок и зданий-заслонов."""
        out = []
        for w in self.roads["ways"]:
            pts = json.loads(w["xy"]) if isinstance(w["xy"], str) else w["xy"]
            if w.get("name") and len(pts) >= 2:
                out.append((w["name"], LineString(pts)))
        return out

    @cached_property
    def street_tree(self) -> shapely.STRtree:
        return shapely.STRtree([ln for _, ln in self.streets])

    def nearest_street(self, x: float, y: float, max_d: float = 150.0) -> str | None:
        idx = self.street_tree.query_nearest(shapely.Point(x, y), max_distance=max_d)
        return self.streets[int(idx[0])][0] if len(idx) else None

    @cached_property
    def rails(self) -> list[dict]:
        return _load_json(self.folder / "rails.json.gz")

    @cached_property
    def peaks(self) -> list[dict]:
        return _load_json(self.folder / "peaks.json")

    @cached_property
    def landmarks(self) -> list[dict]:
        return _load_json(self.folder / "landmarks.json")

    @cached_property
    def hotels(self) -> list[dict]:
        return _load_json(self.folder / "hotels.json")

    @cached_property
    def climate(self) -> dict:
        return _load_json(self.folder / "climate.json")

    # ---------- графы ----------
    @cached_property
    def walk_graph(self):
        from .geo.routing import WalkGraph

        return WalkGraph.build(self)

    @cached_property
    def drive_graph(self):
        from .geo.routing import DriveGraph

        return DriveGraph.build(self)

    # ---------- изменения среды (сценарии) ----------
    def apply_building_override(self, override_id: int, polygon_lonlat: list, height_m: float) -> None:
        """Добавляет новое здание в растр застройки (сценарий «построили дом»)."""
        with self._lock:
            if override_id in self._overrides_applied:
                return
            ring = np.asarray(polygon_lonlat, dtype=float)
            x, y = self.proj.to_xy(ring[:, 0], ring[:, 1])
            poly = Polygon(np.column_stack([x, y])).buffer(0)
            r = self.rasters
            new_id = -1000 - override_id
            rasterize_polygon(r["bld_h"], poly, min(255, int(round(height_m))), mode="max",
                              ids=r["bld_id"].a, id_value=new_id)
            self._overrides_applied.append(override_id)
            log.info("Применено изменение среды #%s: здание %.0f м", override_id, height_m)

    def reset_overrides(self) -> None:
        with self._lock:
            self.__dict__.pop("rasters", None)
            self._overrides_applied = []

    # ---------- утилиты ----------
    def lonlat(self, x, y):
        return self.proj.to_lonlat(x, y)

    def xy(self, lon, lat):
        return self.proj.to_xy(lon, lat)


_region: RegionData | None = None
_region_lock = threading.Lock()


def get_region() -> RegionData:
    global _region
    if _region is None:
        with _region_lock:
            if _region is None:
                _region = RegionData(settings.region_dir)
    return _region
