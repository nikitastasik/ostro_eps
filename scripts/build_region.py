"""Сборка компактного набора данных региона из открытых источников.

Вход:
  * вырезка OpenStreetMap (PBF) — см. scripts/fetch_data.sh (из планетарного дампа OSM на AWS
    Open Data, s3://osm-pds, без Overpass);
  * Copernicus DEM GLO-30 (DSM, 1″) — тайлы с s3://copernicus-dem-30m;
  * climate.json — scripts/build_climate.py.
Выход (data/<region>/): region.json, rasters.npz, buildings.json.gz, roads.json.gz,
rails.json.gz, pois.json.gz, areas.json.gz, peaks.json, landmarks.json, hotels.json, sea.geojson.

Зависимости только для сборки: osmium (pyosmium), tifffile, imagecodecs, numpy, shapely.
"""

from __future__ import annotations

import argparse
import datetime as dt
import glob
import gzip
import json
import math
import os
import re
import sys
from collections import Counter

import numpy as np
import osmium
import shapely
from shapely.geometry import LineString, MultiPolygon, Polygon, box, mapping
from shapely.ops import linemerge, polygonize, unary_union

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from app.agents.dedup import describe as dedup_describe  # noqa: E402
from app.agents.dedup import resolve as dedup_resolve  # noqa: E402
from app.geo.proj import LocalProjection  # noqa: E402
from app.geo.raster import Grid, rasterize_polygon  # noqa: E402

HOTEL_KINDS = {"hotel", "guest_house", "hostel", "motel", "apartment", "chalet"}
FLOOR_M = 3.2

POI_RULES = [
    # (категория, ключ, значения)
    ("pharmacy", "amenity", {"pharmacy"}),
    ("pharmacy", "healthcare", {"pharmacy"}),
    ("convenience", "shop", {"convenience", "supermarket", "grocery", "general", "deli"}),
    ("cafe", "amenity", {"cafe"}),
    ("restaurant", "amenity", {"restaurant"}),
    ("fast_food", "amenity", {"fast_food", "food_court"}),
    ("bar", "amenity", {"bar"}),
    ("pub", "amenity", {"pub", "biergarten"}),
    ("nightclub", "amenity", {"nightclub"}),
    ("karaoke", "amenity", {"karaoke_box"}),
    ("atm", "amenity", {"atm"}),
    ("bank", "amenity", {"bank"}),
    ("playground", "leisure", {"playground"}),
    ("dog_park", "leisure", {"dog_park"}),
    ("hospital", "amenity", {"hospital"}),
    ("clinic", "amenity", {"clinic", "doctors"}),
    ("parking", "amenity", {"parking"}),
    ("ev_charging", "amenity", {"charging_station"}),
    ("bus_stop", "highway", {"bus_stop"}),
    ("station", "railway", {"station", "halt"}),
    ("museum", "tourism", {"museum", "gallery"}),
    ("attraction", "tourism", {"attraction", "theme_park", "zoo", "aquarium"}),
    ("viewpoint", "tourism", {"viewpoint"}),
    ("coworking", "amenity", {"coworking_space"}),
    ("coworking", "office", {"coworking"}),
    ("laundry", "shop", {"laundry", "dry_cleaning"}),
    ("marketplace", "amenity", {"marketplace"}),
    ("water_park", "leisure", {"water_park"}),
    ("fitness", "leisure", {"fitness_centre", "sports_centre"}),
    ("car_rental", "amenity", {"car_rental"}),
    ("bicycle_rental", "amenity", {"bicycle_rental", "scooter_rental"}),
    ("fuel", "amenity", {"fuel"}),
    ("theatre", "amenity", {"theatre", "cinema", "arts_centre"}),
    ("place_of_worship", "amenity", {"place_of_worship"}),
    ("kindergarten", "amenity", {"kindergarten"}),
    ("ice_cream", "amenity", {"ice_cream"}),
]
POI_KEEP_TAGS = {
    "opening_hours", "healthcare:speciality", "capacity", "fee", "access", "operator", "brand", "cuisine",
    "outdoor_seating", "wheelchair", "socket:type2", "socket:chademo", "socket:type2_combo", "parking",
    "surface", "religion", "station", "train", "website", "level", "covered", "supervised",
}
AREA_RULES = [
    ("beach", "natural", {"beach"}),
    ("park", "leisure", {"park", "garden"}),
    ("dog_park", "leisure", {"dog_park"}),
    ("playground", "leisure", {"playground"}),
    ("construction", "landuse", {"construction"}),
    ("construction", "building", {"construction"}),
    ("pedestrian", "highway", {"pedestrian"}),
    ("square", "place", {"square"}),
    ("wood", "natural", {"wood"}),
    ("wood", "landuse", {"forest"}),
    ("scrub", "natural", {"scrub"}),
    ("water_park", "leisure", {"water_park"}),
    ("aerodrome", "aeroway", {"aerodrome"}),
    ("hospital_area", "amenity", {"hospital"}),
    ("parking_area", "amenity", {"parking"}),
    ("stadium", "leisure", {"stadium"}),
    ("sanatorium", "leisure", {"resort"}),
]
VEG_HEIGHT = {"wood": 16, "park": 11, "scrub": 3}
DEFAULT_LEVELS = {
    "house": 2, "detached": 2, "semidetached_house": 2, "terrace": 2, "bungalow": 1, "cabin": 1,
    "garage": 1, "garages": 1, "shed": 1, "roof": 1, "kiosk": 1, "hut": 1, "service": 1, "carport": 1,
    "greenhouse": 1, "toilets": 1, "transformer_tower": 1, "industrial": 2, "warehouse": 2, "retail": 2,
    "commercial": 3, "office": 4, "apartments": 5, "residential": 5, "dormitory": 5, "hotel": 5,
    "hospital": 5, "school": 3, "kindergarten": 2, "university": 4, "public": 3, "civic": 3,
    "church": 2, "cathedral": 3, "train_station": 2, "sports_hall": 2, "construction": 4, "yes": 3,
}
WALK_CLASSES = {
    "footway", "path", "pedestrian", "steps", "living_street", "residential", "service", "unclassified",
    "tertiary", "tertiary_link", "secondary", "secondary_link", "primary", "primary_link", "trunk",
    "trunk_link", "track", "cycleway", "corridor", "bridleway", "road", "platform",
}
DRIVE_CLASSES = {
    "motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link", "secondary",
    "secondary_link", "tertiary", "tertiary_link", "unclassified", "residential", "living_street", "service",
    "road",
}


def num(v, default=None):
    if v is None:
        return default
    m = re.match(r"^\s*(-?\d+(?:[.,]\d+)?)", str(v))
    if not m:
        return default
    return float(m.group(1).replace(",", "."))


def tags_dict(tags) -> dict:
    return {t.k: t.v for t in tags}


class Collector(osmium.SimpleHandler):
    def __init__(self, bbox):
        super().__init__()
        self.bbox = bbox
        self.hotels = []
        self.buildings = []  # (osm, ring_lonlat(list), holes, tags)
        self.building_parts = []
        self.roads = []
        self.rails = []
        self.coast = []
        self.pois = []
        self.areas = []
        self.peaks = []
        self.trees = []
        self.tree_rows = []
        self.crossing_nodes = {}
        self.rail_crossings = {}
        self.stations = []
        self.routes = []
        self.stop_nodes = {}

    # --- узлы ---
    def node(self, n):
        if not n.tags:
            return
        t = tags_dict(n.tags)
        lon, lat = n.location.lon, n.location.lat
        osm = f"n{n.id}"
        if t.get("tourism") in HOTEL_KINDS:
            self.hotels.append({"osm": osm, "lon": lon, "lat": lat, "tags": t, "poly": None})
        if t.get("highway") == "crossing" or t.get("crossing"):
            self.crossing_nodes[n.id] = {
                "signals": t.get("crossing") in {"traffic_signals", "pelican", "toucan"} or t.get("crossing_ref") in {"pelican", "toucan"}
                or "traffic_signals" in t.get("crossing", ""),
            }
        if t.get("railway") in {"crossing", "level_crossing"}:
            self.rail_crossings[n.id] = t.get("railway")
        if t.get("natural") == "peak" and t.get("name") and t.get("ele"):
            self.peaks.append({"osm": osm, "name": t["name"], "ele": num(t["ele"]), "lon": lon, "lat": lat})
        if t.get("railway") in {"stop", "station", "halt", "platform"} or t.get("public_transport") in {"stop_position", "platform", "station"}:
            self.stop_nodes[n.id] = (lon, lat)
        if t.get("natural") == "tree":
            self.trees.append((lon, lat, num(t.get("height"), 9)))
        cat = poi_category(t)
        if cat:
            self.pois.append({"osm": osm, "cat": cat, "lon": lon, "lat": lat, "tags": t})

    # --- линии ---
    def way(self, w):
        t = tags_dict(w.tags)
        if not t:
            return
        coords, refs = [], []
        for nd in w.nodes:
            try:
                coords.append((nd.location.lon, nd.location.lat))
                refs.append(nd.ref)
            except osmium.InvalidLocationError:
                continue
        if len(coords) < 2:
            return
        osm = f"w{w.id}"
        if t.get("natural") == "coastline":
            self.coast.append(coords)
        hw = t.get("highway")
        if hw:
            self.roads.append({"osm": osm, "nodes": refs, "coords": coords, "tags": t})
        rw = t.get("railway")
        if rw in {"rail", "light_rail", "narrow_gauge"}:
            self.rails.append({"osm": osm, "coords": coords, "tags": t})
        if t.get("natural") == "tree_row":
            self.tree_rows.append((coords, num(t.get("height"), 9)))

    # --- полигоны (замкнутые линии и мультиполигоны) ---
    def area(self, a):
        t = tags_dict(a.tags)
        if not t:
            return
        osm = ("w" if a.from_way() else "r") + str(a.orig_id())
        polys = []
        try:
            for outer in a.outer_rings():
                ring = [(n.lon, n.lat) for n in outer]
                holes = [[(n.lon, n.lat) for n in inner] for inner in a.inner_rings(outer)]
                if len(ring) >= 4:
                    polys.append((ring, holes))
        except osmium.InvalidLocationError:
            return
        if not polys:
            return
        b = t.get("building")
        if b and b != "no":
            for ring, holes in polys:
                self.buildings.append({"osm": osm, "ring": ring, "holes": holes, "tags": t})
        if t.get("building:part") and t.get("building:part") != "no":
            for ring, _holes in polys:
                self.building_parts.append({"osm": osm, "ring": ring, "tags": t})
        if t.get("tourism") in HOTEL_KINDS:
            ring = max(polys, key=lambda p: len(p[0]))[0]
            poly = Polygon(ring)
            c = poly.representative_point()
            self.hotels.append({"osm": osm, "lon": c.x, "lat": c.y, "tags": t, "poly": ring})
        for cat, key, vals in AREA_RULES:
            if t.get(key) in vals:
                for ring, holes in polys:
                    self.areas.append({"osm": osm, "cat": cat, "ring": ring, "holes": holes, "tags": t})
        cat = poi_category(t)
        if cat and not (b and cat in {"parking"} and False):
            ring = max(polys, key=lambda p: len(p[0]))[0]
            c = Polygon(ring).representative_point()
            self.pois.append({"osm": osm, "cat": cat, "lon": c.x, "lat": c.y, "tags": t})

    def relation(self, r):
        t = tags_dict(r.tags)
        if t.get("type") == "route" and t.get("route") in {"train", "bus", "trolleybus"}:
            stops = [m.ref for m in r.members if m.type == "n" and (m.role.startswith("stop") or m.role.startswith("platform"))]
            self.routes.append({"osm": f"r{r.id}", "route": t.get("route"), "name": t.get("name"),
                                "ref": t.get("ref"), "from": t.get("from"), "to": t.get("to"), "stops": stops})


def poi_category(t: dict) -> str | None:
    for cat, key, vals in POI_RULES:
        if t.get(key) in vals:
            if cat in {"hospital", "clinic"}:
                spec = t.get("healthcare:speciality", "") + " " + t.get("name", "").lower()
                if "paediatric" in spec or "детск" in spec:
                    return "kids_hospital"
            if cat == "bus_stop" and t.get("bus") == "no":
                continue
            return cat
    if t.get("public_transport") == "platform" and t.get("bus") == "yes":
        return "bus_stop"
    return None


def keep_tags(t: dict) -> dict:
    return {k: v for k, v in t.items() if k in POI_KEEP_TAGS}


# ----------------------------------------------------------------------------------------
def load_dem(dem_dir: str):
    import tifffile

    tiles = []
    for path in sorted(glob.glob(os.path.join(dem_dir, "*.tif"))):
        with tifffile.TiffFile(path) as tf:
            p = tf.pages[0]
            a = p.asarray().astype(np.float32)
            tie = p.tags["ModelTiepointTag"].value
            scale = p.tags["ModelPixelScaleTag"].value
            tiles.append((a, tie[3], tie[4], scale[0], scale[1]))
    lon_min = min(t[1] for t in tiles)
    lat_max = max(t[2] for t in tiles)
    res = tiles[0][3]
    lon_max = max(t[1] + t[0].shape[1] * t[3] for t in tiles)
    lat_min = min(t[2] - t[0].shape[0] * t[4] for t in tiles)
    nx = int(round((lon_max - lon_min) / res))
    ny = int(round((lat_max - lat_min) / res))
    mosaic = np.full((ny, nx), np.nan, dtype=np.float32)
    for a, lon0, lat0, _sx, _sy in tiles:
        c0 = int(round((lon0 - lon_min) / res))
        r0 = int(round((lat_max - lat0) / res))
        mosaic[r0:r0 + a.shape[0], c0:c0 + a.shape[1]] = a
    return mosaic, lon_min, lat_max, res


def sample_dem(dem, lon, lat):
    mosaic, lon_min, lat_max, res = dem
    fc = (np.asarray(lon) - lon_min) / res - 0.5
    fr = (lat_max - np.asarray(lat)) / res - 0.5
    c0 = np.clip(np.floor(fc).astype(int), 0, mosaic.shape[1] - 2)
    r0 = np.clip(np.floor(fr).astype(int), 0, mosaic.shape[0] - 2)
    tx = np.clip(fc - c0, 0, 1)
    ty = np.clip(fr - r0, 0, 1)
    v = (mosaic[r0, c0] * (1 - tx) * (1 - ty) + mosaic[r0, c0 + 1] * tx * (1 - ty)
         + mosaic[r0 + 1, c0] * (1 - tx) * ty + mosaic[r0 + 1, c0 + 1] * tx * ty)
    return v


def grey_filter(a: np.ndarray, size: int, op) -> np.ndarray:
    pad = size // 2
    p = np.pad(a, pad, mode="edge")
    win = np.lib.stride_tricks.sliding_window_view(p, (size, size))
    return op(win, axis=(2, 3))


# ----------------------------------------------------------------------------------------
def build_sea_polygon(coast_lines, bbox_ll):
    """Полигон моря из береговой линии OSM (суша слева, море справа по направлению линии)."""
    lines = [LineString(c) for c in coast_lines if len(c) >= 2]
    merged = linemerge(unary_union(lines))
    frame = box(*bbox_ll)
    parts = list(getattr(merged, "geoms", [merged]))
    islands = [Polygon(p.coords) for p in parts if p.is_ring and len(p.coords) >= 4]
    open_parts = [p for p in parts if not p.is_ring]
    # При вырезке по рамке линии берега обрываются чуть внутри неё: продлеваем концы
    # по направлению последних ~2 км до пересечения с рамкой, чтобы линия разрезала регион.
    extended = []
    span = max(bbox_ll[2] - bbox_ll[0], bbox_ll[3] - bbox_ll[1]) * 2
    for p in open_parts:
        coords = list(p.coords)

        def far_dir(pts):
            x0, y0 = pts[0]
            for x, y in pts[1:]:
                if math.hypot(x - x0, y - y0) > 0.02:
                    return x0 - x, y0 - y
            x, y = pts[-1]
            return x0 - x, y0 - y

        dx, dy = far_dir(coords)
        n = math.hypot(dx, dy) or 1
        start = (coords[0][0] + dx / n * span, coords[0][1] + dy / n * span)
        dx, dy = far_dir(coords[::-1])
        n = math.hypot(dx, dy) or 1
        end = (coords[-1][0] + dx / n * span, coords[-1][1] + dy / n * span)
        extended.append(LineString([start] + coords + [end]))
    merged = unary_union(extended).intersection(frame)
    pieces = list(polygonize(unary_union([merged, frame.exterior])))
    sea_parts = []
    segs = [merged] if isinstance(merged, LineString) else list(getattr(merged, "geoms", []))
    probes = []
    for s in segs:
        if s.length <= 0:
            continue
        coords = list(s.coords)
        for i in range(0, len(coords) - 1, max(1, len(coords) // 20)):
            (x1, y1), (x2, y2) = coords[i], coords[i + 1]
            dx, dy = x2 - x1, y2 - y1
            ln = math.hypot(dx, dy)
            if ln == 0:
                continue
            mx, my = (x1 + x2) / 2, (y1 + y2) / 2
            # справа от направления: (dy, -dx)
            eps = 2e-5
            probes.append(((mx + dy / ln * eps, my - dx / ln * eps), True))
            probes.append(((mx - dy / ln * eps, my + dx / ln * eps), False))
    for poly in pieces:
        votes = 0
        for (px, py), is_sea in probes:
            if poly.contains(shapely.Point(px, py)):
                votes += 1 if is_sea else -1
        if votes > 0:
            sea_parts.append(poly)
    sea = unary_union(sea_parts) if sea_parts else Polygon()
    if islands:
        sea = sea.difference(unary_union(islands))
    print(f"sea: pieces={len(pieces)} sea_pieces={len(sea_parts)} islands={len(islands)} area={sea.area:.4f}", flush=True)
    return sea


# ----------------------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pbf", required=True)
    ap.add_argument("--dem-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--hotels-bbox", default="39.665,43.535,39.800,43.660", help="lon_min,lat_min,lon_max,lat_max")
    ap.add_argument("--near-margin", type=float, default=2000.0)
    ap.add_argument("--near-res", type=float, default=5.0)
    ap.add_argument("--extract-bbox", default="39.0,43.35,40.45,44.15")
    ap.add_argument("--snapshot", default="2026-10-19")
    args = ap.parse_args()

    hb = [float(v) for v in args.hotels_bbox.split(",")]
    eb = [float(v) for v in args.extract_bbox.split(",")]
    os.makedirs(args.out, exist_ok=True)

    print("reading OSM ...", flush=True)
    col = Collector(eb)
    col.apply_file(args.pbf, locations=True, idx="flex_mem")
    print(f"hotels={len(col.hotels)} buildings={len(col.buildings)} roads={len(col.roads)} rails={len(col.rails)} "
          f"pois={len(col.pois)} areas={len(col.areas)} coast={len(col.coast)} peaks={len(col.peaks)}", flush=True)

    lat0 = (hb[1] + hb[3]) / 2
    lon0 = (hb[0] + hb[2]) / 2
    proj = LocalProjection(lat0, lon0)

    def xy(coords):
        a = np.asarray(coords, dtype=float)
        x, y = proj.to_xy(a[:, 0], a[:, 1])
        return np.column_stack([x, y])

    hx0, hy0 = proj.to_xy(hb[0], hb[1])
    hx1, hy1 = proj.to_xy(hb[2], hb[3])
    m = args.near_margin
    res = args.near_res
    nx0, ny0 = math.floor((float(hx0) - m) / res) * res, math.floor((float(hy0) - m) / res) * res
    nx1, ny1 = math.ceil((float(hx1) + m) / res) * res, math.ceil((float(hy1) + m) / res) * res
    near_nx = int(round((nx1 - nx0) / res)) + 1
    near_ny = int(round((ny1 - ny0) / res)) + 1
    near_box = box(nx0, ny0, nx1, ny1)
    print(f"near grid {near_nx}x{near_ny} @ {res} m", flush=True)

    # ---------------- DEM ----------------
    print("DEM ...", flush=True)
    dem = load_dem(args.dem_dir)
    # ближняя зона: 25 м; DTM = «открытие» DSM окном 3x3 (убирает здания и рощи < ~75 м)
    dres = 25.0
    dx0, dy0 = nx0 - 500, ny0 - 500
    dnx = int((nx1 - nx0 + 1000) / dres) + 1
    dny = int((ny1 - ny0 + 1000) / dres) + 1
    gx, gy = np.meshgrid(dx0 + np.arange(dnx) * dres, dy0 + np.arange(dny) * dres)
    glon, glat = proj.to_lonlat(gx, gy)
    dsm_near = sample_dem(dem, glon, glat).astype(np.float32)
    dtm_near = grey_filter(grey_filter(dsm_near, 3, np.min), 3, np.max).astype(np.float32)
    dtm_near = np.minimum(dtm_near, dsm_near)
    # дальняя зона: 90 м по всей вырезке
    fres = 90.0
    ex0, ey0 = proj.to_xy(eb[0], eb[1])
    ex1, ey1 = proj.to_xy(eb[2], eb[3])
    fx0, fy0 = math.floor(float(ex0) / fres) * fres, math.floor(float(ey0) / fres) * fres
    fnx = int((float(ex1) - fx0) / fres) + 1
    fny = int((float(ey1) - fy0) / fres) + 1
    fgx, fgy = np.meshgrid(fx0 + np.arange(fnx) * fres, fy0 + np.arange(fny) * fres)
    flon, flat = proj.to_lonlat(fgx, fgy)
    dem_far = sample_dem(dem, flon, flat)
    dem_far = np.where(np.isnan(dem_far), 0, dem_far)

    # ---------------- море ----------------
    print("sea ...", flush=True)
    sea_ll = build_sea_polygon(col.coast, eb)
    sea_xy = shapely.transform(sea_ll, lambda c: np.column_stack(proj.to_xy(c[:, 0], c[:, 1])))
    sea_far = np.zeros((fny, fnx), dtype=np.uint8)
    sea_far_grid = Grid(sea_far, fx0, fy0, fres)
    for p in getattr(sea_xy, "geoms", [sea_xy]):
        if not p.is_empty:
            rasterize_polygon(sea_far_grid, p, 1, mode="set")
    sea_far = np.where((sea_far > 0) | (dem_far <= -0.5) & (sea_far > 0), 1, sea_far).astype(np.uint8)
    dem_far = np.where(sea_far > 0, 0, dem_far)

    near_shape = (near_ny, near_nx)
    sea_near = Grid(np.zeros(near_shape, dtype=np.uint8), nx0, ny0, res)
    sea_clip = sea_xy.intersection(near_box.buffer(50))
    for p in getattr(sea_clip, "geoms", [sea_clip]):
        if isinstance(p, Polygon) and not p.is_empty:
            rasterize_polygon(sea_near, p, 1, mode="set")

    # ---------------- здания ----------------
    print("buildings ...", flush=True)
    parts_by_area = []
    for bp in col.building_parts:
        h = num(bp["tags"].get("height"))
        lv = num(bp["tags"].get("building:levels"))
        if h is None and lv is not None:
            h = lv * FLOOR_M
        if h:
            parts_by_area.append((Polygon(xy(bp["ring"])).buffer(0), h, lv))
    part_tree = shapely.STRtree([p[0] for p in parts_by_area]) if parts_by_area else None

    recs = []
    for b in col.buildings:
        ring = xy(b["ring"])
        poly = Polygon(ring, [xy(h) for h in b["holes"] if len(h) >= 4]).buffer(0)
        if poly.is_empty or poly.area < 6:
            continue
        if not poly.intersects(near_box):
            continue
        if not isinstance(poly, Polygon):
            poly = max(poly.geoms, key=lambda g: g.area)
        t = b["tags"]
        btype = t.get("building", "yes")
        h_tag = num(t.get("height"))
        lv_tag = num(t.get("building:levels"))
        roof_lv = num(t.get("roof:levels"), 0) or 0
        levels, lv_src = None, "none"
        height, h_src = None, "default"
        if lv_tag is not None and lv_tag > 0:
            levels, lv_src = int(round(lv_tag)), "osm_levels"
        if h_tag is not None and h_tag > 0:
            height, h_src = h_tag, "osm_height"
            if levels is None:
                levels, lv_src = max(1, int(round(h_tag / FLOOR_M))), "osm_height"
        elif levels is not None:
            height, h_src = levels * FLOOR_M + roof_lv * 2.0 + 1.0, "osm_levels"
        if part_tree is not None:
            idxs = part_tree.query(poly, predicate="intersects")
            if len(idxs):
                hp = max(parts_by_area[i][1] for i in idxs)
                if height is None or hp > height:
                    height, h_src = hp, "osm_parts"
                    if levels is None:
                        levels, lv_src = max(1, int(round(hp / FLOOR_M))), "osm_parts"
        c = poly.centroid
        recs.append({"b": b, "poly": poly, "t": t, "btype": btype, "levels": levels, "lv_src": lv_src,
                     "height": height, "h_src": h_src, "cx": c.x, "cy": c.y})

    # Этажность неразмеченных зданий: медиана размеченных соседей в радиусе 150 м (не менее 3).
    # Типы «house/garage/...» оставляем по умолчанию — для них соседи не показательны.
    IMPUTE_TYPES = {"yes", "apartments", "residential", "commercial", "hotel", "office", "retail", "dormitory", "public"}
    RADIUS = 150.0
    tagged = [r for r in recs if r["lv_src"] in {"osm_levels", "osm_height", "osm_parts"} and r["levels"]]
    cell = {}
    for r in tagged:
        cell.setdefault((int(r["cx"] // RADIUS), int(r["cy"] // RADIUS)), []).append(r)

    def neighbours(r, exclude=None):
        cx, cy = int(r["cx"] // RADIUS), int(r["cy"] // RADIUS)
        out = []
        for i in (-1, 0, 1):
            for j in (-1, 0, 1):
                for o in cell.get((cx + i, cy + j), []):
                    if o is exclude:
                        continue
                    if math.hypot(o["cx"] - r["cx"], o["cy"] - r["cy"]) <= RADIUS:
                        out.append(o["levels"])
        return out

    # перекрёстная проверка (leave-one-out) на размеченных зданиях тех же типов
    errs_n, errs_d = [], []
    for r in tagged:
        if r["btype"] not in IMPUTE_TYPES:
            continue
        nb = neighbours(r, exclude=r)
        if len(nb) >= 3:
            errs_n.append(abs(float(np.median(nb)) - r["levels"]))
            errs_d.append(abs(DEFAULT_LEVELS.get(r["btype"], 3) - r["levels"]))
    impute_stats = {
        "validated_on": len(errs_n),
        "mae_neighbors": round(float(np.mean(errs_n)), 2) if errs_n else None,
        "within1_neighbors": round(float(np.mean(np.array(errs_n) <= 1)), 3) if errs_n else None,
        "mae_default": round(float(np.mean(errs_d)), 2) if errs_d else None,
        "within1_default": round(float(np.mean(np.array(errs_d) <= 1)), 3) if errs_d else None,
        "dsm_check": {"note": "оценка высоты по Copernicus DSM (30 м) проверена на 19 тыс. зданиях с этажностью OSM: "
                              "корреляция ≈ 0,1, источник отклонён"},
    }
    print("levels imputation validation:", impute_stats, flush=True)
    for r in recs:
        if r["height"] is not None:
            continue
        nb = neighbours(r) if r["btype"] in IMPUTE_TYPES else []
        if len(nb) >= 3:
            lv = int(round(float(np.median(nb))))
            r["levels"], r["lv_src"] = lv, "neighbors"
            r["height"], r["h_src"] = lv * FLOOR_M + 1.0, "neighbors"
        else:
            dl = DEFAULT_LEVELS.get(r["btype"], 3)
            r["height"], r["h_src"] = dl * FLOOR_M + 1.0, "default"

    bld_h = Grid(np.zeros(near_shape, dtype=np.uint8), nx0, ny0, res)
    bld_id = np.full(near_shape, -1, dtype=np.int32)
    buildings_out = []
    lv_src_count = Counter()
    for r in recs:
        poly, t = r["poly"], r["t"]
        lv_src_count[r["h_src"]] += 1
        idx = len(buildings_out)
        rasterize_polygon(bld_h, poly, int(min(255, round(r["height"]))), mode="max", ids=bld_id, id_value=idx)
        rec = {
            "osm": r["b"]["osm"], "xy": np.round(np.asarray(poly.exterior.coords), 1).ravel().tolist(),
            "h": round(float(r["height"]), 1), "h_src": r["h_src"], "lv": r["levels"], "lv_src": r["lv_src"],
            "t": r["btype"],
        }
        if t.get("name"):
            rec["name"] = t["name"]
        extra = {k: t[k] for k in ("start_date", "building:architecture", "heritage", "heritage:operator",
                                    "ref:okn", "wikidata", "wikipedia", "historic", "architect", "addr:street",
                                    "addr:housenumber", "roof:levels", "building:levels:underground") if k in t}
        if extra:
            rec["tags"] = extra
        buildings_out.append(rec)
    print("building height sources:", dict(lv_src_count), flush=True)

    # ---------------- растительность ----------------
    veg = Grid(np.zeros(near_shape, dtype=np.uint8), nx0, ny0, res)
    for a in col.areas:
        if a["cat"] in VEG_HEIGHT:
            poly = Polygon(xy(a["ring"])).buffer(0)
            if poly.intersects(near_box):
                rasterize_polygon(veg, poly.intersection(near_box.buffer(20)), VEG_HEIGHT[a["cat"]], mode="max")
    for lon, lat, h in col.trees:
        x, y = proj.to_xy(lon, lat)
        p = shapely.Point(float(x), float(y)).buffer(3.5)
        if p.intersects(near_box):
            rasterize_polygon(veg, p, int(min(25, h or 9)), mode="max")
    for coords, h in col.tree_rows:
        line = LineString(xy(coords)).buffer(3.0)
        if line.intersects(near_box):
            rasterize_polygon(veg, line, int(min(25, h or 9)), mode="max")
    # не ставим деревья на здания и море
    veg.a[(bld_h.a > 0) | (sea_near.a > 0)] = 0

    # ---------------- дороги ----------------
    print("roads ...", flush=True)
    walk_box = near_box.buffer(1500)
    ways_out = []
    used_nodes = set()
    for r in col.roads:
        t = r["tags"]
        cls = t["highway"]
        if cls in {"proposed", "construction", "abandoned", "razed", "bus_stop", "elevator", "raceway"}:
            continue
        line_xy = xy(r["coords"])
        line = LineString(line_xy)
        in_walk = line.intersects(walk_box)
        drivable = cls in DRIVE_CLASSES and t.get("motor_vehicle") != "no" and t.get("access") not in {"no"}
        major = cls in {"motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link", "secondary",
                        "secondary_link", "tertiary", "tertiary_link"}
        if not in_walk and not (drivable and major):
            continue
        foot = t.get("foot")
        access = t.get("access")
        walk = cls in WALK_CLASSES and foot != "no" and not (access in {"no", "private"} and foot not in {"yes", "designated", "permissive"})
        if cls in {"trunk", "trunk_link"} and t.get("sidewalk") in {"no", "none"} and foot != "yes":
            walk = False
        oneway = 0
        ow = t.get("oneway")
        if ow in {"yes", "true", "1"} or cls in {"motorway", "motorway_link"} or t.get("junction") == "roundabout":
            oneway = 1
        elif ow == "-1":
            oneway = -1
        rec = {
            "id": r["osm"], "cls": cls, "nodes": r["nodes"], "xy": np.round(line_xy, 1).tolist(),
            "walk": bool(walk and in_walk), "drive": bool(drivable),
        }
        if t.get("name"):
            rec["name"] = t["name"]
        for k in ("tunnel", "bridge"):
            if t.get(k) and t.get(k) != "no":
                rec[k] = True
        if t.get("covered") == "yes" and cls in {"footway", "path"}:
            rec["tunnel"] = True
        if t.get("layer"):
            rec["layer"] = num(t.get("layer"), 0)
        if t.get("lanes"):
            rec["lanes"] = int(num(t.get("lanes"), 0) or 0)
        if t.get("maxspeed"):
            rec["maxspeed"] = int(num(t.get("maxspeed"), 0) or 0)
        if t.get("footway"):
            rec["footway"] = t["footway"]
        if oneway:
            rec["oneway"] = oneway
        if t.get("surface"):
            rec["surface"] = t["surface"]
        if t.get("motor_vehicle") or access:
            rec["access"] = t.get("motor_vehicle") or access
        ways_out.append(rec)
        used_nodes.update(r["nodes"])
    crossing_nodes = {str(k): v for k, v in col.crossing_nodes.items() if k in used_nodes}
    for k, v in col.rail_crossings.items():
        if k in used_nodes:
            crossing_nodes.setdefault(str(k), {"signals": False})["rail"] = v
    roads_doc = {"ways": ways_out, "crossing_nodes": crossing_nodes}

    rails_out = []
    for r in col.rails:
        line_xy = xy(r["coords"])
        if not LineString(line_xy).intersects(near_box.buffer(3000)):
            continue
        t = r["tags"]
        rec = {"id": r["osm"], "xy": np.round(line_xy, 1).tolist(), "railway": t.get("railway")}
        for k in ("tunnel", "bridge"):
            if t.get(k) and t.get(k) != "no":
                rec[k] = True
        for k in ("usage", "service", "name"):
            if t.get(k):
                rec[k] = t[k]
        rails_out.append(rec)

    # ---------------- POI и площади ----------------
    print("pois/areas ...", flush=True)
    pois_out = []
    seen = set()
    poi_box = near_box.buffer(2500)
    for p in col.pois:
        x, y = proj.to_xy(p["lon"], p["lat"])
        if not poi_box.contains(shapely.Point(float(x), float(y))):
            # станции и больницы оставляем по всей вырезке (аэропорт, детские больницы)
            if p["cat"] not in {"station", "kids_hospital", "hospital"}:
                continue
        key = (p["osm"], p["cat"])
        if key in seen:
            continue
        seen.add(key)
        rec = {"osm": p["osm"], "cat": p["cat"], "x": round(float(x), 1), "y": round(float(y), 1),
               "tags": keep_tags(p["tags"])}
        if p["tags"].get("name"):
            rec["name"] = p["tags"]["name"]
        pois_out.append(rec)
    areas_out = []
    for a in col.areas:
        if a["cat"] in {"wood", "scrub"}:
            continue
        poly = Polygon(xy(a["ring"])).buffer(0)
        if poly.is_empty:
            continue
        if not poly.intersects(poi_box) and a["cat"] not in {"aerodrome"}:
            continue
        simp = poly.simplify(1.5)
        if isinstance(simp, MultiPolygon):
            simp = max(simp.geoms, key=lambda g: g.area)
        rec = {"osm": a["osm"], "cat": a["cat"], "xy": np.round(np.asarray(simp.exterior.coords), 1).ravel().tolist(),
               "area_m2": round(poly.area)}
        t = a["tags"]
        for k in ("name", "surface", "access", "fee", "operator", "supervised", "opening_hours"):
            if t.get(k):
                rec[k] = t[k]
        areas_out.append(rec)

    # ---------------- отели ----------------
    print("hotels ...", flush=True)
    btree = shapely.STRtree([Polygon(np.asarray(b["xy"]).reshape(-1, 2)) for b in buildings_out])
    bpolys = [Polygon(np.asarray(b["xy"]).reshape(-1, 2)) for b in buildings_out]
    hotel_box = box(float(hx0), float(hy0), float(hx1), float(hy1))
    hotels_out = []
    seen_h = set()
    for h in col.hotels:
        t = h["tags"]
        name = t.get("name") or t.get("name:ru")
        if not name:
            continue
        x, y = proj.to_xy(h["lon"], h["lat"])
        pt = shapely.Point(float(x), float(y))
        if not hotel_box.contains(pt):
            continue
        if h["osm"] in seen_h:
            continue
        seen_h.add(h["osm"])
        bidx = None
        if h["poly"] is not None:
            hp = Polygon(xy(h["poly"])).buffer(0)
            cands = btree.query(hp, predicate="intersects")
            if len(cands):
                bidx = int(max(cands, key=lambda i: bpolys[i].intersection(hp).area))
        if bidx is None:
            cands = btree.query(pt.buffer(12), predicate="intersects")
            if len(cands):
                bidx = int(min(cands, key=lambda i: bpolys[i].distance(pt)))
        addr = ", ".join(v for v in [t.get("addr:street"), t.get("addr:housenumber")] if v) or None
        stars = num(t.get("stars"))
        hotels_out.append({
            "id": f"osm-{h['osm']}", "osm": h["osm"], "name": name, "kind": t.get("tourism"),
            "stars": int(stars) if stars else None, "lat": round(h["lat"], 7), "lon": round(h["lon"], 7),
            "x": round(float(x), 1), "y": round(float(y), 1), "address": addr,
            "website": t.get("website") or t.get("contact:website"), "phone": t.get("phone") or t.get("contact:phone"),
            "building": bidx,
            "tags": {k: v for k, v in t.items() if not k.startswith("name:") and k not in {"source"}},
        })
    print(f"hotels in bbox with name: {len(hotels_out)}; with building: {sum(1 for h in hotels_out if h['building'] is not None)}",
          flush=True)
    # агент-дедупликатор (тот же код, что в приложении): дубли «точка + контур», корпуса, подписи тёзок
    streets = [(w["name"], LineString(w["xy"])) for w in ways_out if w.get("name") and len(w["xy"]) >= 2]
    hotels_out, dedup_log = dedup_resolve(hotels_out, lambda i: bpolys[i], lambda i: buildings_out[i].get("lv"), streets)
    for e in dedup_log:
        print("dedup:", dedup_describe(e), flush=True)

    peaks_out = []
    for p in col.peaks:
        if p["ele"] is None or p["ele"] < 250:
            continue
        x, y = proj.to_xy(p["lon"], p["lat"])
        peaks_out.append({"osm": p["osm"], "name": p["name"], "ele": p["ele"], "x": round(float(x), 1),
                          "y": round(float(y), 1), "lat": p["lat"], "lon": p["lon"]})

    # ---------------- запись ----------------
    def dump_gz(name, obj):
        with gzip.open(os.path.join(args.out, name), "wt", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))

    def dump(name, obj):
        with open(os.path.join(args.out, name), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)

    np.savez_compressed(
        os.path.join(args.out, "rasters.npz"),
        bld_h=bld_h.a, bld_id=bld_id, veg_h=veg.a, sea=sea_near.a,
        dtm_near=np.round(dtm_near * 10).astype(np.int32) / 10, dsm_near=np.round(dsm_near * 10).astype(np.int32) / 10,
        dem_far=np.round(dem_far).astype(np.int16), sea_far=sea_far,
    )
    dump_gz("buildings.json.gz", buildings_out)
    dump_gz("roads.json.gz", roads_doc)
    dump_gz("rails.json.gz", rails_out)
    dump_gz("pois.json.gz", pois_out)
    dump_gz("areas.json.gz", areas_out)
    dump("peaks.json", peaks_out)
    dump("hotels.json", hotels_out)
    sea_simpl = sea_ll.intersection(box(hb[0] - 0.08, hb[1] - 0.08, hb[2] + 0.08, hb[3] + 0.08)).simplify(0.0002)
    with open(os.path.join(args.out, "sea.geojson"), "w", encoding="utf-8") as f:
        json.dump({"type": "Feature", "properties": {"name": "Чёрное море"}, "geometry": mapping(sea_simpl)}, f)
    # ---------------- центр, аэропорт, поезда, ориентиры ----------------
    center = None
    for w in ways_out:
        if w.get("name") == "Навагинская улица" and w["cls"] == "pedestrian":
            pts = np.asarray(w["xy"])
            c = pts[len(pts) // 2]
            lon, lat = proj.to_lonlat(c[0], c[1])
            center = {"name": "Навагинская улица", "name_gen": "центра (Навагинская улица)", "lat": float(lat), "lon": float(lon)}
            break
    if center is None:
        mv = next((b for b in buildings_out if (b.get("name") or "").startswith("Морской вокзал")), None)
        if mv is not None:
            pts = np.asarray(mv["xy"]).reshape(-1, 2)
            lon, lat = proj.to_lonlat(pts[:, 0].mean(), pts[:, 1].mean())
            center = {"name": "Морской вокзал", "name_gen": "Морского вокзала (центр)", "lat": float(lat), "lon": float(lon)}
    stations = {p["osm"]: p for p in pois_out if p["cat"] == "station"}
    airport_station = next((osm for osm, p in stations.items() if "аэропорт" in (p.get("name") or "").lower()), None)
    train_routes = []
    for r in col.routes:
        if r["route"] != "train":
            continue
        coords = [col.stop_nodes[n] for n in r["stops"] if n in col.stop_nodes]
        if not coords:
            continue
        sxy = np.array([proj.to_xy(lon, lat) for lon, lat in coords], dtype=float)
        served = []
        for osm, p in stations.items():
            d = np.hypot(sxy[:, 0] - p["x"], sxy[:, 1] - p["y"])
            if d.min() <= 450:
                served.append(osm)
        if served:
            train_routes.append({"osm": r["osm"], "name": r.get("name"), "ref": r.get("ref"), "stop_osm": served})
    aero = next((a for a in col.areas if a["cat"] == "aerodrome" and (a["tags"].get("iata") == "AER" or a["tags"].get("name") == "Сочи")), None)
    airport = None
    if aero is not None:
        c = Polygon(aero["ring"]).centroid
        airport = {"name": "Международный аэропорт Сочи", "lat": c.y, "lon": c.x, "station_osm": airport_station}
    landmark_defs = [
        ("w51352173", "ЖК «Александрийский маяк»", None, 8),
        ("w51346173", None, None, 0),
        ("r9353455", "Зимний театр", None, 6),
    ]
    landmarks = []
    by_osm = {b["osm"]: b for b in buildings_out}
    for osm, name, h, maxkm in landmark_defs:
        b = by_osm.get(osm)
        if b is None or not name:
            continue
        pts = np.asarray(b["xy"]).reshape(-1, 2)
        landmarks.append({"name": name, "osm": osm, "x": float(pts[:, 0].mean()), "y": float(pts[:, 1].mean()),
                          "height": float(h or b["h"]), "height_src": "OSM " + b["h_src"], "radius": 20, "max_km": maxkm})
    tallest = max((b for b in buildings_out if b["h_src"] in {"osm_levels", "osm_height"} and b.get("name")),
                  key=lambda b: b["h"], default=None)
    if tallest is not None:
        pts = np.asarray(tallest["xy"]).reshape(-1, 2)
        landmarks.append({"name": f"высотка «{tallest['name']}» ({tallest['h']:.0f} м)", "osm": tallest["osm"],
                          "x": float(pts[:, 0].mean()), "y": float(pts[:, 1].mean()), "height": float(tallest["h"]),
                          "height_src": "OSM " + tallest["h_src"], "radius": 20, "max_km": 10})
    akhun = next((p for p in peaks_out if p["name"] == "Ахун"), None)
    if akhun is not None:
        landmarks.append({"name": "Смотровая башня на горе Ахун", "osm": akhun["osm"], "x": akhun["x"], "y": akhun["y"],
                          "height": 30.0, "height_src": "высота башни ≈30 м (справочно), отметка вершины — OSM ele",
                          "radius": 40, "max_km": 20, "absolute_ground": akhun["ele"]})

    dump("landmarks.json", landmarks)
    region = {
        "name": "Сочи — центральный район",
        "slug": os.path.basename(os.path.normpath(args.out)),
        "origin": {"lat": lat0, "lon": lon0},
        "hotels_bbox": hb,
        "extract_bbox": eb,
        "near": {"x0": nx0, "y0": ny0, "res": res, "nx": near_nx, "ny": near_ny},
        "dem_near": {"x0": dx0, "y0": dy0, "res": dres},
        "dem_far": {"x0": fx0, "y0": fy0, "res": fres},
        "timezone_offset": 3,
        "center": center,
        "airport": airport,
        "train_routes": train_routes,
        "built_at": dt.date.today().isoformat(),
        "sources": {
            "osm": {"title": "OpenStreetMap", "snapshot": args.snapshot, "license": "ODbL",
                    "url": "https://osm-pds.s3.amazonaws.com/ (planet PBF)"},
            "dem": {"title": "Copernicus DEM GLO-30 (DSM)", "snapshot": "2011–2015", "license": "Copernicus DEM licence",
                    "url": "https://copernicus-dem-30m.s3.amazonaws.com/"},
            "climate": {"title": "NOAA GHCN-Daily + NOAA OISST v2.1", "license": "public domain"},
        },
        "stats": {
            "buildings": len(buildings_out), "height_sources": dict(lv_src_count), "pois": len(pois_out),
            "roads": len(ways_out), "hotels": len(hotels_out), "areas": len(areas_out),
            "levels_imputation": impute_stats,
        },
    }
    dump("region.json", region)
    print("done", region["stats"], flush=True)


if __name__ == "__main__":
    main()
