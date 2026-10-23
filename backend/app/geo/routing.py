"""Пешеходная и автомобильная маршрутизация по графу OSM с учётом рельефа.

Время пешком — по функции Тоблера (скорость зависит от уклона), поэтому путь
«к морю под горку» и «обратно в гору» различается. На маршруте считаем набор
высоты, лестницы, переходы через крупные дороги и способ пересечения железной дороги.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field

import numpy as np
import shapely
from shapely.geometry import LineString

WALK_CLASSES = {
    "footway", "path", "pedestrian", "steps", "living_street", "residential", "service", "unclassified",
    "tertiary", "tertiary_link", "secondary", "secondary_link", "primary", "primary_link", "trunk",
    "trunk_link", "track", "cycleway", "corridor", "bridleway", "road", "platform",
}
MAJOR_ROADS = {"trunk", "trunk_link", "primary", "primary_link", "secondary", "secondary_link"}
DRIVE_SPEED = {  # км/ч, городские условия без пробок
    "motorway": 80, "motorway_link": 50, "trunk": 60, "trunk_link": 40, "primary": 45, "primary_link": 35,
    "secondary": 38, "secondary_link": 30, "tertiary": 32, "tertiary_link": 28, "unclassified": 30,
    "residential": 22, "living_street": 10, "service": 12, "road": 20,
}
ROAD_NAMES_RU = {
    "trunk": "магистраль", "trunk_link": "съезд магистрали", "primary": "главная улица",
    "primary_link": "съезд", "secondary": "крупная улица", "secondary_link": "съезд",
}


def tobler_kmh(slope: float) -> float:
    s = max(-0.6, min(0.6, slope))
    return 6.0 * math.exp(-3.5 * abs(s + 0.05))


@dataclass
class Route:
    nodes: list[int]
    length_m: float
    minutes_to: float
    minutes_back: float
    climb_to_m: float
    climb_back_m: float
    max_slope_pct: float
    steps_segments: int
    crossings: list[dict] = field(default_factory=list)
    rail: dict | None = None
    coords: list[tuple[float, float]] = field(default_factory=list)
    elevations: list[float] = field(default_factory=list)


class WalkGraph:
    def __init__(self):
        self.node_index: dict[int, int] = {}
        self.xy: list[tuple[float, float]] = []
        self.z: np.ndarray | None = None
        self.adj: list[list[tuple[int, int]]] = []  # node -> [(nbr, edge_id)]
        self.edges: list[dict] = []  # edge: u, v, len, way, cls, steps, bridge, tunnel, crossing
        self.node_roads: dict[int, set[str]] = {}
        self.node_road_names: dict[int, str] = {}
        self.crossing_nodes: dict[int, dict] = {}
        self.rail_tree = None
        self.rail_lines: list[LineString] = []
        self.coords_arr: np.ndarray | None = None

    # ------------------------------------------------------------------
    @classmethod
    def build(cls, rd) -> "WalkGraph":
        g = cls()
        roads = rd.roads
        g.crossing_nodes = {int(k): v for k, v in roads.get("crossing_nodes", {}).items()}
        interp: dict[int, float] = {}
        for wi, w in enumerate(roads["ways"]):
            c = w["cls"]
            nodes = w["nodes"]
            xy = w["xy"]
            if c in MAJOR_ROADS or c in {"motorway", "motorway_link"}:
                for n in nodes:
                    g.node_roads.setdefault(n, set()).add(c)
                    if w.get("name"):
                        g.node_road_names.setdefault(n, w["name"])
            if c not in WALK_CLASSES or not w.get("walk", True):
                continue
            idx = []
            for n, (x, y) in zip(nodes, xy):
                if n not in g.node_index:
                    g.node_index[n] = len(g.xy)
                    g.xy.append((x, y))
                    g.adj.append([])
                idx.append(g.node_index[n])
            if w.get("bridge") or w.get("tunnel"):
                # отметки на мостах/в тоннелях — линейно между концами (DEM там врёт)
                interp[idx[0]] = interp.get(idx[0], math.nan)
                for k in idx[1:-1]:
                    interp[k] = math.inf  # пометка «интерполировать»
            for a, b in zip(idx[:-1], idx[1:]):
                (x1, y1), (x2, y2) = g.xy[a], g.xy[b]
                e = {
                    "u": a, "v": b, "len": math.hypot(x2 - x1, y2 - y1), "way": wi, "cls": c,
                    "steps": c == "steps", "bridge": bool(w.get("bridge")), "tunnel": bool(w.get("tunnel")),
                    "crossing": w.get("footway") == "crossing", "name": w.get("name"),
                }
                eid = len(g.edges)
                g.edges.append(e)
                g.adj[a].append((b, eid))
                g.adj[b].append((a, eid))
        arr = np.asarray(g.xy, dtype=float).reshape(-1, 2)
        g.coords_arr = arr
        z = rd.ground(arr[:, 0], arr[:, 1]).astype(float)
        # интерполяция по мостам/тоннелям
        for w in roads["ways"]:
            if not (w.get("bridge") or w.get("tunnel")) or w["cls"] not in WALK_CLASSES:
                continue
            ids = [g.node_index.get(n) for n in w["nodes"]]
            if any(i is None for i in ids) or len(ids) < 3:
                continue
            xy = np.asarray([g.xy[i] for i in ids])
            seg = np.hypot(np.diff(xy[:, 0]), np.diff(xy[:, 1]))
            cum = np.concatenate([[0], np.cumsum(seg)])
            if cum[-1] <= 0:
                continue
            z0, z1 = z[ids[0]], z[ids[-1]]
            for k, i in enumerate(ids[1:-1], start=1):
                z[i] = z0 + (z1 - z0) * cum[k] / cum[-1]
        g.z = z
        # компоненты связности: старт маршрута берём только в крупных (изолированные дорожки
        # внутри закрытых территорий не связаны с городом)
        comp = np.full(len(g.xy), -1, dtype=np.int64)
        sizes = []
        for s0 in range(len(g.xy)):
            if comp[s0] >= 0:
                continue
            cid = len(sizes)
            stack = [s0]
            comp[s0] = cid
            n = 0
            while stack:
                u = stack.pop()
                n += 1
                for v, _ in g.adj[u]:
                    if comp[v] < 0:
                        comp[v] = cid
                        stack.append(v)
            sizes.append(n)
        g.comp = comp
        g.comp_size = np.asarray(sizes)
        g.main = g.comp_size[comp] >= 300
        # железная дорога для определения способа пересечения
        lines = []
        for r in rd.rails:
            if r.get("tunnel"):
                continue
            pts = np.asarray(r["xy"], dtype=float).reshape(-1, 2)
            if len(pts) >= 2:
                lines.append(LineString(pts))
        g.rail_lines = lines
        g.rail_tree = shapely.STRtree(lines) if lines else None
        return g

    # ------------------------------------------------------------------
    def nearest_node(self, x: float, y: float, max_dist: float = 250.0, main_only: bool = True) -> int | None:
        if self.coords_arr is None or len(self.coords_arr) == 0:
            return None
        d = np.hypot(self.coords_arr[:, 0] - x, self.coords_arr[:, 1] - y)
        if main_only:
            d = np.where(self.main, d, np.inf)
        i = int(np.argmin(d))
        return i if d[i] <= max_dist else None

    def nodes_within(self, x: float, y: float, radius: float) -> np.ndarray:
        d = np.hypot(self.coords_arr[:, 0] - x, self.coords_arr[:, 1] - y)
        return np.nonzero(d <= radius)[0]

    def edge_time(self, eid: int, frm: int) -> float:
        e = self.edges[eid]
        to = e["v"] if frm == e["u"] else e["u"]
        dz = float(self.z[to] - self.z[frm])
        length = max(e["len"], 0.5)
        slope = dz / length
        if e["steps"]:
            slope = max(-0.6, min(0.6, slope if abs(slope) > 0.15 else math.copysign(0.3, slope or 1)))
        t = length / (tobler_kmh(slope) / 3.6)
        if e["steps"]:
            t *= 1.25
        return t

    def shortest(self, src: int, targets: set[int], max_time: float = 3600.0) -> tuple[list[int], list[int]] | None:
        """Дейкстра от src до ближайшей вершины из targets по времени. Возвращает (узлы, рёбра)."""
        if src in targets:
            return [src], []
        dist = {src: 0.0}
        prev: dict[int, tuple[int, int]] = {}
        pq = [(0.0, src)]
        while pq:
            t, u = heapq.heappop(pq)
            if t > dist.get(u, math.inf):
                continue
            if u in targets:
                nodes, edges = [u], []
                while u != src:
                    p, e = prev[u]
                    nodes.append(p)
                    edges.append(e)
                    u = p
                return nodes[::-1], edges[::-1]
            if t > max_time:
                break
            for v, eid in self.adj[u]:
                nt = t + self.edge_time(eid, u)
                if self.edges[eid]["crossing"]:
                    nt += 8
                if nt < dist.get(v, math.inf):
                    dist[v] = nt
                    prev[v] = (u, eid)
                    heapq.heappush(pq, (nt, v))
        return None

    # ------------------------------------------------------------------
    def describe(self, nodes: list[int], edges: list[int]) -> Route:
        length = sum(self.edges[e]["len"] for e in edges)
        t_to = t_back = 0.0
        climb_to = climb_back = 0.0
        max_slope = 0.0
        steps_ways = set()
        for k, eid in enumerate(edges):
            a, b = nodes[k], nodes[k + 1]
            t_to += self.edge_time(eid, a)
            t_back += self.edge_time(eid, b)
            dz = float(self.z[b] - self.z[a])
            if dz > 0:
                climb_to += dz
            else:
                climb_back += -dz
            e = self.edges[eid]
            if e["len"] > 15:
                max_slope = max(max_slope, abs(dz) / e["len"])
            if e["steps"]:
                steps_ways.add(e["way"])
        crossings = self._crossings(nodes, edges)
        rail = self._rail(nodes, edges)
        coords = [self.xy[n] for n in nodes]
        return Route(nodes=nodes, length_m=length, minutes_to=t_to / 60, minutes_back=t_back / 60,
                     climb_to_m=climb_to, climb_back_m=climb_back, max_slope_pct=max_slope * 100,
                     steps_segments=len(steps_ways), crossings=crossings, rail=rail, coords=coords,
                     elevations=[float(self.z[n]) for n in nodes])

    def _crossings(self, nodes: list[int], edges: list[int]) -> list[dict]:
        inv = {v: k for k, v in self.node_index.items()}
        found: list[dict] = []
        for k, n in enumerate(nodes):
            osm_id = inv.get(n)
            if osm_id is None:
                continue
            roads = self.node_roads.get(osm_id)
            if not roads:
                continue
            if k == 0 or k == len(nodes) - 1:
                continue
            prev_major = self.edges[edges[k - 1]]["cls"] in MAJOR_ROADS
            next_major = self.edges[edges[k]]["cls"] in MAJOR_ROADS
            if prev_major and next_major:
                continue  # идём вдоль дороги по её тротуару, а не через неё
            explicit = self.crossing_nodes.get(osm_id)
            on_crossing_way = self.edges[edges[k - 1]]["crossing"] or self.edges[edges[k]]["crossing"]
            if prev_major != next_major and not (explicit or on_crossing_way):
                continue  # вышли на тротуар крупной улицы или ушли с него — не пересечение
            # иначе: размеченный переход или путь «подходит» к крупной дороге и продолжается
            # по другую сторону — это пересечение проезжей части
            road = sorted(roads, key=lambda c: ["trunk", "primary", "secondary"].index(c.split("_")[0])
                          if c.split("_")[0] in ("trunk", "primary", "secondary") else 9)[0]
            x, y = self.xy[n]
            if any(math.hypot(x - c["x"], y - c["y"]) < 35 for c in found):
                continue
            found.append({
                "x": x, "y": y, "road_class": road, "road": self.node_road_names.get(osm_id) or ROAD_NAMES_RU.get(road, "дорога"),
                "signals": bool(explicit and explicit.get("signals")),
            })
        return found

    def _rail(self, nodes: list[int], edges: list[int]) -> dict | None:
        if self.rail_tree is None or not edges:
            return None
        for k, eid in enumerate(edges):
            a, b = nodes[k], nodes[k + 1]
            seg = LineString([self.xy[a], self.xy[b]])
            hits = self.rail_tree.query(seg, predicate="intersects")
            if len(hits) == 0:
                continue
            e = self.edges[eid]
            if e["tunnel"]:
                kind = "underpass"
            elif e["bridge"]:
                kind = "bridge"
            else:
                kind = "level"
            # соседние рёбра того же пути тоже могут быть тоннелем/мостом
            for j in (k - 1, k + 1):
                if 0 <= j < len(edges):
                    ej = self.edges[edges[j]]
                    if kind == "level" and ej["tunnel"]:
                        kind = "underpass"
                    if kind == "level" and ej["bridge"]:
                        kind = "bridge"
            x, y = self.xy[a]
            return {"kind": kind, "x": x, "y": y}
        return None


class DriveGraph:
    def __init__(self):
        self.node_index: dict[int, int] = {}
        self.xy: list[tuple[float, float]] = []
        self.adj: list[list[tuple[int, float, float]]] = []  # (nbr, time_s, len_m)
        self.coords_arr: np.ndarray | None = None

    @classmethod
    def build(cls, rd) -> "DriveGraph":
        g = cls()
        for w in rd.roads["ways"]:
            c = w["cls"]
            if c not in DRIVE_SPEED or not w.get("drive", True):
                continue
            speed = (w.get("maxspeed") or DRIVE_SPEED[c])
            speed = min(speed, DRIVE_SPEED[c] + 10)
            idx = []
            for n, (x, y) in zip(w["nodes"], w["xy"]):
                if n not in g.node_index:
                    g.node_index[n] = len(g.xy)
                    g.xy.append((x, y))
                    g.adj.append([])
                idx.append(g.node_index[n])
            oneway = w.get("oneway", 0)
            for a, b in zip(idx[:-1], idx[1:]):
                (x1, y1), (x2, y2) = g.xy[a], g.xy[b]
                length = math.hypot(x2 - x1, y2 - y1)
                t = length / (speed / 3.6)
                if oneway >= 0:
                    g.adj[a].append((b, t, length))
                if oneway <= 0:
                    g.adj[b].append((a, t, length))
        g.coords_arr = np.asarray(g.xy, dtype=float).reshape(-1, 2)
        return g

    def nearest_node(self, x: float, y: float, max_dist: float = 400.0) -> tuple[int | None, float]:
        d = np.hypot(self.coords_arr[:, 0] - x, self.coords_arr[:, 1] - y)
        i = int(np.argmin(d))
        return (i if d[i] <= max_dist else None), float(d[i])

    def travel(self, src: int, dst: int, max_time: float = 4 * 3600) -> tuple[float, float] | None:
        dist = {src: (0.0, 0.0)}
        pq = [(0.0, 0.0, src)]
        while pq:
            t, length, u = heapq.heappop(pq)
            if u == dst:
                return t, length
            if t > dist.get(u, (math.inf, 0))[0] or t > max_time:
                continue
            for v, dt, dl in self.adj[u]:
                nt = t + dt
                if nt < dist.get(v, (math.inf, 0))[0]:
                    dist[v] = (nt, length + dl)
                    heapq.heappush(pq, (nt, length + dl, v))
        return None
