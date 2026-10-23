"""Собирает климатические нормы для региона из открытых данных NOAA.

Источники (оба лежат в AWS Open Data, ключи не нужны):
  * GHCN-Daily, станция SOTCHI (RSM00037099): суточные TMAX/TMIN/PRCP.
    https://noaa-ghcn-pds.s3.amazonaws.com/csv/by_station/RSM00037099.csv
  * NOAA OISST v2.1 (спутниковая температура поверхности моря, сетка 0.25°):
    https://noaa-cdr-sea-surface-temp-optimum-interpolation-pds.s3.amazonaws.com/data/v2.1/avhrr/
    Берём ячейку 43.625N 39.625E (ближайшая к центру Сочи морская ячейка),
    по три дня каждого месяца (5, 15, 25) за 2015–2025.

Запуск (нужны h5py и numpy):
  python scripts/build_climate.py --ghcn RSM00037099.csv --oisst-dir oisst/ --out data/sochi/climate.json
"""

from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import glob
import json
import os

import numpy as np

MONTHS_RU = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август",
             "сентябрь", "октябрь", "ноябрь", "декабрь"]


def ghcn_normals(path: str, year_from: int, year_to: int) -> dict:
    tmax = collections.defaultdict(list)
    tmin = collections.defaultdict(list)
    prcp_days = collections.defaultdict(lambda: collections.defaultdict(int))
    prcp_sum = collections.defaultdict(lambda: collections.defaultdict(float))
    years_seen = set()
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            date = row["DATE"]
            year, month = int(date[:4]), int(date[4:6])
            if not (year_from <= year <= year_to):
                continue
            if row["Q_FLAG"]:
                continue  # отбрасываем значения, не прошедшие контроль качества
            el, val = row["ELEMENT"], float(row["DATA_VALUE"]) / 10.0
            if el == "TMAX":
                tmax[month].append(val)
            elif el == "TMIN":
                tmin[month].append(val)
            elif el == "PRCP":
                years_seen.add(year)
                prcp_sum[month][year] += val
                if val >= 1.0:
                    prcp_days[month][year] += 1
    out = {}
    for m in range(1, 13):
        days = [prcp_days[m][y] for y in sorted(prcp_sum[m])]
        sums = [prcp_sum[m][y] for y in sorted(prcp_sum[m])]
        out[m] = {
            "air_tmax_c": round(float(np.mean(tmax[m])), 1) if tmax[m] else None,
            "air_tmin_c": round(float(np.mean(tmin[m])), 1) if tmin[m] else None,
            "rain_days": round(float(np.mean(days)), 1) if days else None,
            "precip_mm": round(float(np.mean(sums)), 0) if sums else None,
            "n_days": len(tmax[m]),
        }
    return {"normals": out, "years": [min(years_seen), max(years_seen)] if years_seen else None}


def oisst_normals(folder: str, lat: float, lon: float) -> dict:
    import h5py

    per_month = collections.defaultdict(list)
    years = set()
    i = j = None
    for path in sorted(glob.glob(os.path.join(folder, "oisst-avhrr-v02r01.*.nc"))):
        stamp = os.path.basename(path).split(".")[1]
        day = dt.date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:8]))
        with h5py.File(path, "r") as f:
            if i is None:
                i = int(np.argmin(np.abs(f["lat"][:] - lat)))
                j = int(np.argmin(np.abs(f["lon"][:] - lon)))
            raw = f["sst"][0, 0, i, j]
            scale = float(f["sst"].attrs.get("scale_factor", [1.0])[0])
        if raw == -999:
            continue
        per_month[day.month].append(raw * scale)
        years.add(day.year)
    out = {}
    for m in range(1, 13):
        vals = np.array(per_month[m])
        out[m] = {
            "sea_c": round(float(vals.mean()), 1),
            "sea_c_p10": round(float(np.percentile(vals, 10)), 1),
            "sea_c_p90": round(float(np.percentile(vals, 90)), 1),
            "share_ge_20": round(float((vals >= 20).mean()), 2),
            "share_ge_22": round(float((vals >= 22).mean()), 2),
            "n": int(vals.size),
        }
    return {"normals": out, "years": [min(years), max(years)], "cell": [lat, lon]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ghcn", required=True)
    ap.add_argument("--oisst-dir", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    air = ghcn_normals(args.ghcn, 2005, 2025)
    sea = oisst_normals(args.oisst_dir, 43.625, 39.625)
    months = []
    for m in range(1, 13):
        a, s = air["normals"][m], sea["normals"][m]
        months.append({"month": m, "name": MONTHS_RU[m - 1], **a, **s})
    doc = {
        "station": {"id": "RSM00037099", "name": "SOTCHI", "lat": 43.58, "lon": 39.77,
                    "source": "NOAA GHCN-Daily", "years": air["years"],
                    "url": "https://noaa-ghcn-pds.s3.amazonaws.com/csv/by_station/RSM00037099.csv"},
        "sea": {"source": "NOAA OISST v2.1 (AVHRR), ячейка 0.25°", "cell": sea["cell"], "years": sea["years"],
                "url": "https://noaa-cdr-sea-surface-temp-optimum-interpolation-pds.s3.amazonaws.com/"},
        "built_at": dt.date.today().isoformat(),
        "months": months,
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    for m in months:
        print(m["month"], m["air_tmax_c"], m["air_tmin_c"], m["rain_days"], m["sea_c"], m["share_ge_20"])


if __name__ == "__main__":
    main()
