"""Агент-дедупликатор над готовыми данными региона, без сети.

Склеивает дубли отелей в data/<регион>/hotels.json (точка и контур одного отеля), связывает корпуса
комплексов и подписывает тёзок. Тот же код вызывает scripts/build_region.py при пересборке данных.

    python scripts/dedup_hotels.py [--region sochi]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.agents.dedup import describe, resolve  # noqa: E402
from app.region import RegionData  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--region", default="sochi")
args = ap.parse_args()

folder = ROOT / "data" / args.region
rd = RegionData(folder)
kept, log = resolve(rd.hotels, lambda i: rd.buildings[i].poly, lambda i: rd.buildings[i].levels, rd.streets)
with open(folder / "hotels.json", "w", encoding="utf-8") as f:
    json.dump(kept, f, ensure_ascii=False, indent=1)
print(f"отелей: {len(rd.hotels)} → {len(kept)}")
for e in log:
    print("-", describe(e))
