import datetime as dt
from pathlib import Path

import numpy as np
import yaml

from app.geo.opening_hours import parse
from app.geo.raster import Grid
from app.geo.sun import solar_position, sunset_utc
from app.geo.views import CLS_SEA, ViewEngine

DATA = Path(__file__).resolve().parents[2] / "data"


def test_sun_matches_pyephem_reference():
    ref = yaml.safe_load(open(DATA / "eval" / "sun_reference.yaml", encoding="utf-8"))
    for row in ref["rows"]:
        d = dt.date.fromisoformat(row["date"])
        t = sunset_utc(d, 43.5855, 39.7231)
        _, az = solar_position(t, 43.5855, 39.7231)
        local = t + dt.timedelta(hours=3)
        h, m = map(int, row["sunset"].split(":"))
        assert abs((local.hour * 60 + local.minute + local.second / 60) - (h * 60 + m)) <= 2.0
        assert abs(az - row["azimuth"]) <= 0.5


def test_opening_hours():
    assert parse("24/7").always
    s = parse("Mo-Su 10:00-02:00")
    assert s.night_days(60) == 7 and s.open_at(0, 23 * 60)
    s = parse("Mo-Fr 09:00-20:00; Sa 10:00-18:00; Su off")
    assert not s.open_at(6, 12 * 60) and s.open_at(0, 12 * 60)
    assert parse("May-Sep: Mo-Su 10:00-02:00").months == {5, 6, 7, 8, 9}
    assert parse("какая-то ерунда") is None


class FakeRegion:
    """Плоская площадка на высоте 10 м, море южнее y = -200 м, стена 15 м в 80–100 м к югу от окна."""

    def __init__(self, wall: bool = True):
        res = 5.0
        n = 801  # 4 км
        x0 = y0 = -2000.0
        bld_h = np.zeros((n, n), dtype=np.uint8)
        bld_id = np.full((n, n), -1, dtype=np.int32)
        sea = np.zeros((n, n), dtype=np.uint8)
        ys = y0 + np.arange(n) * res
        sea[ys < -200, :] = 1
        if wall:
            rows = (ys >= -100) & (ys <= -80)
            bld_h[rows, :] = 15
            bld_id[rows, :] = 1
        dtm = np.full((161, 161), 10.0, dtype=np.float32)
        dtm_y = -2000 + np.arange(161) * 25
        dtm[dtm_y < -200, :] = 0.0
        far = np.zeros((200, 200), dtype=np.float32)
        sea_far = np.ones((200, 200), dtype=np.uint8)
        far_y = -60000 + np.arange(200) * 600
        sea_far[far_y >= -200, :] = 0
        far[far_y >= -200, :] = 10
        self.rasters = {
            "bld_h": Grid(bld_h, x0, y0, res),
            "bld_id": Grid(bld_id, x0, y0, res),
            "veg_h": Grid(np.zeros((n, n), dtype=np.uint8), x0, y0, res),
            "sea": Grid(sea, x0, y0, res),
            "dtm_near": Grid(dtm, -2000, -2000, 25),
            "dem_far": Grid(far, -60000, -60000, 600),
            "sea_far": Grid(sea_far, -60000, -60000, 600),
        }


def _min_floor_with_sea(region) -> int | None:
    eng = ViewEngine(region)
    az = np.arange(150, 211, 1.0)  # окно на юг ±30°
    res = eng.analyse_point(0.0, 0.0, 10.0, list(range(1, 11)), az, own_id=None)
    for r in res:
        if r["sea_vis"].sum() >= 15:
            return r["floor"]
    return None


def test_wall_blocks_sea_until_sixth_floor():
    # глаз на этаже f: 10 + 1.6 + 3(f-1); край стены на 25 м закрывает море, пока глаз ниже ~26 м
    assert _min_floor_with_sea(FakeRegion(wall=True)) == 6


def test_no_wall_sea_from_first_floor():
    assert _min_floor_with_sea(FakeRegion(wall=False)) == 1


def test_sea_is_skyline_without_obstacles():
    eng = ViewEngine(FakeRegion(wall=False))
    res = eng.analyse_point(0.0, 0.0, 10.0, [3], np.array([180.0]), own_id=None)[0]
    assert int(res["sky_cls"][0]) == CLS_SEA
    assert res["horizon"][0] < 0  # горизонт моря ниже уровня глаз
