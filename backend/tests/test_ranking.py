"""Доля подходящих номеров, статус «с условием» и ранжирование на синтетических отелях."""

from app.agents.concierge import _why_above, evaluate_hotel, room_share, share_factor
from app.knowledge.registry import load_builtin, normalize
from app.models import Hotel, ResolvedValue
from app.nlp.query_parser import Criterion

SPECS = {s["key"]: normalize(s) for s in load_builtin()}
FACADES = [{"key": "З", "length_m": 20.0}, {"key": "В", "length_m": 20.0}, {"key": "С", "length_m": 10.0},
           {"key": "Ю", "length_m": 10.0}]


def _hotel(hid, sunset_floor_w, levels=10):
    h = Hotel(id=hid, name=hid, kind="hotel", lat=43.6, lon=39.7, building={"facades": FACADES, "levels": levels})
    by = {f["key"]: {"available": False, "months": [], "min_floor": None} for f in FACADES}
    by["З"] = {"available": True, "months": list(range(1, 13)), "over_sea_months": list(range(1, 13)),
               "min_floor": sunset_floor_w}
    rv = ResolvedValue(hotel_id=hid, char_key="sunset_view", value={"available": True, "by_facade": by,
                                                                     "min_floor": sunset_floor_w},
                       confidence=0.8, sources=["geo"], status="ok", summary="закат")
    return h, {(hid, "sunset_view"): rv}


GROUPS = {"sunset_view": [Criterion("sunset_view", "available", "eq", True, label="Закат из окна")]}


def test_room_share_formula():
    lengths = {"З": 20.0, "В": 20.0, "С": 10.0, "Ю": 10.0}
    # запад с 7-го из 10 этажей: 20 × 4 / (60 × 10)
    assert abs(room_share(lengths, 10, {"З": 7, "В": None}) - 80 / 600) < 1e-9
    assert room_share(lengths, 10, {"З": 1, "В": 1, "С": 1, "Ю": 1}) == 1.0
    assert room_share(lengths, 10, {"З": 11}) == 0.0


def test_share_factor_is_monotone_and_bounded():
    xs = [share_factor(x / 20) for x in range(21)]
    assert xs == sorted(xs) and abs(xs[0] - 0.7) < 1e-9 and abs(xs[-1] - 1.0) < 1e-9
    assert share_factor(None) == 1.0


def test_sunset_from_first_floor_ranks_above_seventh():
    low_h, low_rv = _hotel("low", 1)
    high_h, high_rv = _hotel("high", 7)
    a = evaluate_hotel(low_h, SPECS, GROUPS, low_rv, {})
    b = evaluate_hotel(high_h, SPECS, GROUPS, high_rv, {})
    assert a["score"] > b["score"]
    assert a["share"] > b["share"] and abs(b["share"] - 80 / 600) < 1e-3
    assert "с 1-го этажа, а не с 7-го" in _why_above(a, b)


def test_statuses():
    one_side, rv = _hotel("x", 1)
    r = evaluate_hotel(one_side, SPECS, GROUPS, rv, {})
    need = r["needs"][0]
    # закат только с одной стороны — подходит, но с условием, и в тексте есть доля номеров
    assert need["status"] == "conditional" and "% номеров" in need["text"]
    h, rv = _hotel("all", 1)
    for f in FACADES:
        rv[("all", "sunset_view")].value["by_facade"][f["key"]] = {"available": True, "months": list(range(1, 13)),
                                                                    "min_floor": 1}
    assert evaluate_hotel(h, SPECS, GROUPS, rv, {})["needs"][0]["status"] == "match"
    h, rv = _hotel("none", 1)
    rv[("none", "sunset_view")].value = None
    assert evaluate_hotel(h, SPECS, GROUPS, rv, {})["needs"][0]["status"] == "unknown"
