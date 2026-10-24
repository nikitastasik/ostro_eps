"""Агент-дедупликатор: склейка «точка + контур», корпуса комплекса, тёзки сети."""

from shapely.geometry import LineString, box

from app.agents.dedup import resolve

POLYS = {1: box(0, 0, 20, 20), 2: box(100, 0, 120, 20), 3: box(2000, 0, 2020, 20)}
LEVELS = {1: 9, 2: 5, 3: 4}


def _h(i, osm, name, x, y, building=None, **kw):
    return {"id": f"osm-{osm}", "osm": osm, "name": name, "x": x, "y": y, "building": building, "tags": kw.pop("tags", {}), **kw}


def _resolve(hotels, streets=None):
    return resolve(hotels, POLYS.get, LEVELS.get, streets)


def test_point_and_contour_6m_are_one_hotel():
    way = _h(1, "w10", "Антика", 10, 10, building=1, tags={"building": "yes"})
    node = _h(2, "n20", "Антика", 10, 16, building=1, stars=3, tags={"stars": "3", "internet_access": "wlan"})
    kept, log = _resolve([node, way])
    assert len(kept) == 1
    h = kept[0]
    assert h["osm"] == "w10" and h["stars"] == 3 and h["tags"]["internet_access"] == "wlan"
    assert h["merged_from"][0]["osm"] == "n20" and h["merged_from"][0]["dist_m"] == 6
    assert log[0]["type"] == "merge"


def test_chain_2km_apart_is_not_merged_and_gets_street_labels():
    a = _h(1, "n1", "Grand Way", 10, 10, building=1, phone="+7 1")
    b = _h(2, "n2", "Grand Way", 2010, 10, building=3, phone="+7 2")
    streets = [("Северная улица", LineString([(0, 30), (40, 30)])), ("Нагорная улица", LineString([(1990, -20), (2030, -20)]))]
    kept, log = _resolve([a, b], streets)
    assert len(kept) == 2
    assert {h["hint"] for h in kept} == {"Северная ул.", "Нагорная ул."}
    assert not any(e["type"] == "merge" for e in log)


def test_corpora_of_one_complex_are_linked_not_merged():
    a = _h(1, "w1", "Жемчужина", 10, 10, building=1, stars=4, tags={"building": "hotel"})
    b = _h(2, "w2", "Жемчужина", 110, 10, building=2, stars=4, tags={"building": "yes"})
    kept, log = _resolve([a, b])
    assert len(kept) == 2
    assert {h["complex"]["label"] for h in kept} == {"корпус 9 эт.", "корпус 5 эт."}
    assert len({h["complex"]["id"] for h in kept}) == 1


def test_conflicting_tags_do_not_make_a_complex():
    a = _h(1, "w1", "Жемчужина", 10, 10, building=1, website="https://a.ru")
    b = _h(2, "w2", "Жемчужина", 110, 10, building=2, website="https://b.ru")
    kept, _ = _resolve([a, b])
    assert not any(h.get("complex") for h in kept) and all(h.get("hint") for h in kept)


def test_resolve_is_idempotent_and_order_independent():
    hs = [_h(1, "w10", "Антика", 10, 10, building=1), _h(2, "n20", "Антика", 10, 16, building=1, stars=3)]
    k1, _ = _resolve(hs)
    k2, _ = _resolve(list(reversed(hs)))
    k3, _ = _resolve(k1)
    assert k1 == k2 == k3
