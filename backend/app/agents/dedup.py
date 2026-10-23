"""Агент-дедупликатор (entity resolution) для списка отелей региона.

В OSM один отель нередко нанесён дважды — точкой и контуром здания, а сети и комплексы дают тёзок.
Агент:
  * склеивает объекты с одинаковым нормализованным именем, если они ближе MERGE_M, относятся к одному
    зданию или точка одного лежит в контуре (у контура) здания другого. Остаётся запись с геометрией
    здания; из второй переносятся звёзды, сайт, телефон, адрес и недостающие теги;
  * связывает в комплекс корпуса с одним именем в MERGE_M…COMPLEX_M друг от друга, если теги это
    подтверждают (сайт, почта, оператор, улица, звёзды) и ничему не противоречат;
  * остальным тёзкам даёт подпись по адресу или ближайшей улице: «Grand Way · Навагинская ул.».

Результат детерминирован: обход и выбор основной записи не зависят от порядка во входе.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from urllib.parse import urlparse

from shapely.geometry import LineString, Point, Polygon

from ..tools.base import plural, quote

MERGE_M = 40.0     # ближе — один и тот же отель
CONTOUR_M = 15.0   # «точка у контура здания»
COMPLEX_M = 300.0  # корпуса одного комплекса
STREET_M = 300.0   # подпись тёзки по улице не дальше этого
MERGED_FIELDS = {"stars": "звёзды", "website": "сайт", "phone": "телефон", "address": "адрес"}
FIELD_TAGS = {"stars", "website", "contact:website", "phone", "contact:phone", "addr:street", "addr:housenumber"}
KIND_KEPT = {"точка": "оставлена точка", "здание": "оставлено здание", "контур": "оставлен контур",
             "территория": "оставлена территория", "объект": "оставлен объект"}
STREET_ABBR = [("улица", "ул."), ("переулок", "пер."), ("проспект", "просп."), ("набережная", "наб."),
               ("бульвар", "бул."), ("площадь", "пл."), ("проезд", "пр."), ("шоссе", "ш.")]


def norm_name(name: str | None) -> str:
    t = (name or "").lower().replace("ё", "е")
    return re.sub(r"[^a-zа-я0-9]+", " ", t).strip()


def osm_kind(h: dict) -> str:
    osm = h.get("osm") or ""
    if osm.startswith("w"):
        return "здание" if (h.get("tags") or {}).get("building") else "контур"
    return {"n": "точка", "r": "территория"}.get(osm[:1], "объект")


def short_street(s: str) -> str:
    for full, abbr in STREET_ABBR:
        s = re.sub(rf"\b{full}\b", abbr, s, flags=re.IGNORECASE)
    return s


def _domain(v: str | None) -> str:
    v = (v or "").strip().lower()
    if not v:
        return ""
    if "@" in v:
        return v.split("@")[-1]
    host = urlparse(v if "//" in v else "//" + v).netloc
    return host.removeprefix("www.")


def _identity(h: dict) -> dict:
    t = h.get("tags") or {}
    return {
        "сайт": _domain(h.get("website") or t.get("website") or t.get("contact:website")),
        "почта": _domain(t.get("email") or t.get("contact:email")),
        "оператор": norm_name(t.get("operator") or t.get("brand")),
        "улица": norm_name(t.get("addr:street")),
        "звёзды": h.get("stars"),
    }


def tags_confirm(a: dict, b: dict) -> tuple[bool, list[str]]:
    """Подтверждают ли теги, что два объекта — корпуса одного комплекса: есть совпадение и нет противоречий."""
    ia, ib = _identity(a), _identity(b)
    if any(ia[k] and ib[k] and ia[k] != ib[k] for k in ("сайт", "почта", "оператор", "звёзды")):
        return False, []
    same = [k for k in ia if ia[k] and ia[k] == ib[k]]
    return bool(same), same


def _nearest_streets(h: dict, streets: list[tuple[str, LineString]], k: int = 2) -> list[str]:
    """Ближайшие улицы с разными названиями (не дальше STREET_M), в сокращённой записи."""
    pt = Point(h["x"], h["y"])
    best: dict[str, float] = {}
    for nm, ln in streets:
        d = ln.distance(pt)
        if d <= STREET_M and d < best.get(nm, math.inf):
            best[nm] = d
    return [short_street(nm) for nm, _ in sorted(best.items(), key=lambda kv: (kv[1], kv[0]))[:k]]


def _dist(a: dict, b: dict) -> float:
    return math.hypot(a["x"] - b["x"], a["y"] - b["y"])


class _DSU:
    def __init__(self, ids):
        self.p = {i: i for i in ids}

    def find(self, i):
        while self.p[i] != i:
            self.p[i] = self.p[self.p[i]]
            i = self.p[i]
        return i

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)


def resolve(hotels: list[dict], building_poly: Callable[[int], Polygon | None],
            building_levels: Callable[[int], int | None] | None = None,
            streets: list[tuple[str, LineString]] | None = None) -> tuple[list[dict], list[dict]]:
    """Склейка, комплексы, подписи тёзок. Возвращает (отели, журнал решений)."""
    hotels = sorted((dict(h) for h in hotels), key=lambda h: h["id"])
    groups: dict[str, list[dict]] = {}
    for h in hotels:
        groups.setdefault(norm_name(h["name"]), []).append(h)

    def near_contour(pt_h: dict, bld_h: dict) -> bool:
        idx = bld_h.get("building")
        poly = building_poly(idx) if idx is not None else None
        return poly is not None and poly.distance(Point(pt_h["x"], pt_h["y"])) <= CONTOUR_M

    def rank(h: dict):
        idx = h.get("building")
        poly = building_poly(idx) if idx is not None else None
        inside = poly is not None and poly.contains(Point(h["x"], h["y"]))
        has_geom = (h.get("osm") or "")[:1] in {"w", "r"} and idx is not None
        return (not has_geom, not inside, -len(h.get("tags") or {}), h["id"])

    log: list[dict] = []
    # ---- 1. склейка дублей
    dsu = _DSU([h["id"] for h in hotels])
    for g in groups.values():
        for i, a in enumerate(g):
            for b in g[i + 1:]:
                same_bld = a.get("building") is not None and a.get("building") == b.get("building")
                # «точка в контуре здания»: только для объекта-точки, два разных здания рядом — это корпуса
                pt_in = (a["osm"][:1] == "n" and near_contour(a, b)) or (b["osm"][:1] == "n" and near_contour(b, a))
                if _dist(a, b) <= MERGE_M or same_bld or pt_in:
                    dsu.union(a["id"], b["id"])
    clusters: dict[str, list[dict]] = {}
    for h in hotels:
        clusters.setdefault(dsu.find(h["id"]), []).append(h)
    kept: list[dict] = []
    for members in clusters.values():
        members.sort(key=rank)
        keep = members[0]
        merged = list(keep.get("merged_from") or [])
        for other in members[1:]:
            moved = []
            for f, title in MERGED_FIELDS.items():
                if not keep.get(f) and other.get(f):
                    keep[f] = other[f]
                    moved.append(title)
            tags = dict(keep.get("tags") or {})
            extra = sorted(k for k in (other.get("tags") or {}) if k not in tags)
            for k in extra:
                tags[k] = other["tags"][k]
            keep["tags"] = tags
            moved += [k for k in extra if k not in FIELD_TAGS]
            merged.append({"id": other["id"], "osm": other.get("osm"), "kind": osm_kind(other),
                           "dist_m": round(_dist(keep, other)), "moved": moved})
            merged.extend(other.get("merged_from") or [])
        if merged:
            keep["merged_from"] = merged
            log.append({"type": "merge", "hotel_id": keep["id"], "name": keep["name"], "kind": osm_kind(keep),
                        "osm": keep.get("osm"), "merged": merged})
        kept.append(keep)

    # ---- 2. корпуса одного комплекса
    kept.sort(key=lambda h: h["id"])
    groups = {}
    for h in kept:
        h.pop("complex", None)
        h.pop("hint", None)
        groups.setdefault(norm_name(h["name"]), []).append(h)
    cdsu = _DSU([h["id"] for h in kept])
    evidence: dict[str, list[str]] = {}
    for g in groups.values():
        for i, a in enumerate(g):
            for b in g[i + 1:]:
                d = _dist(a, b)
                ok, same = tags_confirm(a, b)
                if MERGE_M < d <= COMPLEX_M and ok:
                    cdsu.union(a["id"], b["id"])
                    evidence[cdsu.find(a["id"])] = sorted(set(evidence.get(cdsu.find(a["id"]), []) + same))
    complexes: dict[str, list[dict]] = {}
    for h in kept:
        complexes.setdefault(cdsu.find(h["id"]), []).append(h)
    for cid, members in complexes.items():
        if len(members) < 2:
            continue
        levels = {h["id"]: (building_levels(h["building"]) if building_levels and h.get("building") is not None else None)
                  for h in members}
        distinct = len({levels[h["id"]] for h in members}) == len(members) and None not in levels.values()
        members.sort(key=lambda h: (-(levels[h["id"]] or 0), h["id"]))
        for i, h in enumerate(members, 1):
            if distinct:
                label = f"корпус {levels[h['id']]} эт."
            elif h.get("address"):
                label = f"корпус на {short_street(h['address'])}"
            else:
                label = f"корпус {i}"
            h["complex"] = {"id": f"cx-{cid}", "label": label}
        log.append({"type": "complex", "complex_id": f"cx-{cid}", "name": members[0]["name"],
                    "members": [{"id": h["id"], "label": h["complex"]["label"]} for h in members],
                    "evidence": evidence.get(cid, [])})

    # ---- 3. подписи тёзкам: адрес или ближайшая улица
    for g in groups.values():
        visible = {}
        for h in g:
            visible.setdefault(h["complex"]["id"] if h.get("complex") else h["id"], h)
        if len(visible) < 2:
            continue
        hints = {}
        for key, h in visible.items():
            near = _nearest_streets(h, streets or [])
            hints[key] = short_street(h["address"]) if h.get("address") else (near[0] if near else "без адреса")
        dup = {v for v in hints.values() if list(hints.values()).count(v) > 1}
        for key in sorted(hints):
            h = visible[key]
            if hints[key] in dup and not h.get("address"):
                # одна и та же ближайшая улица у двух тёзок — добавляем вторую, как пишут перекрёстки
                near = _nearest_streets(h, streets or [])
                if len(near) > 1:
                    hints[key] = f"{near[0]} / {near[1]}"
        for h in g:
            key = h["complex"]["id"] if h.get("complex") else h["id"]
            if not h.get("complex"):
                h["hint"] = hints[key]
        log.append({"type": "namesakes", "name": g[0]["name"], "labels": {k: hints[k] for k in sorted(hints)}})
    return kept, log


def display_name(name: str, corpus: str | None = None, hint: str | None = None) -> str:
    extra = corpus or hint
    return f"{name} · {extra}" if extra else name


def describe(entry: dict) -> str:
    """Текст шага журнала агента для записи из журнала resolve()."""
    if entry["type"] == "merge":
        parts = [f"{m['kind']} {m['osm']} ({m['dist_m']} м)" for m in entry["merged"]]
        moved = sorted({x for m in entry["merged"] for x in m["moved"]})
        n = len(entry["merged"]) + 1
        return (f"Склеены {n} {plural(n, 'объект', 'объекта', 'объектов')} OSM {quote(entry['name'])}: {entry['kind']} {entry['osm']} и "
                + ", ".join(parts) + f"; {KIND_KEPT[entry['kind']]}" + (f", перенесено: {', '.join(moved)}" if moved else ""))
    if entry["type"] == "complex":
        labels = ", ".join(m["label"] for m in entry["members"])
        ev = ", ".join(entry["evidence"])
        return f"{quote(entry['name'])} — корпуса одного комплекса ({labels}); совпадают: {ev}"
    labels = "; ".join(entry["labels"].values())
    return f"{quote(entry['name'])} — разные отели с одним именем, подписи по адресу или ближайшей улице: {labels}"
