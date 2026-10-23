"""Предварительный прогон агентов по всем отелям региона → data/seed/seed.json.gz.

Так демо открывается сразу с собранным знанием, а жюри может перезапустить сбор
по любому отелю из интерфейса и увидеть работу агентов вживую.

  python scripts/precompute.py            # всё с нуля во временной SQLite
  python scripts/precompute.py --answers 0.6
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

ap = argparse.ArgumentParser()
ap.add_argument("--db", default="/tmp/ostrovok_precompute.db")
ap.add_argument("--answers", type=float, default=0.6, help="доля отелей, где отельер (симулятор) ответил на вопросы")
ap.add_argument("--limit", type=int, default=0)
args = ap.parse_args()

if os.path.exists(args.db):
    os.remove(args.db)
os.environ["DATABASE_URL"] = f"sqlite:///{args.db}"
os.environ["WORKER_ENABLED"] = "false"
os.environ["SEED_ON_START"] = "false"
os.environ.setdefault("LLM_API_KEY", "")

from sqlalchemy import select  # noqa: E402

from app.agents import orchestrator  # noqa: E402
from app.agents.concierge import search  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.knowledge.registry import sync_builtin  # noqa: E402
from app.models import Hotel, Question  # noqa: E402
from app.seed import dump_seed, load_hotels, log_dedup  # noqa: E402

init_db()
with SessionLocal() as s:
    sync_builtin(s)
    print("hotels:", load_hotels(s))
    log_dedup(s)
    s.commit()
    hotels = list(s.scalars(select(Hotel).order_by(Hotel.id)))
    if args.limit:
        hotels = hotels[: args.limit]
    t0 = time.time()
    for i, h in enumerate(hotels):
        run = orchestrator.run_now(s, "collect", hotel_id=h.id, trigger="seed")
        if run.status != "done":
            print("  ERROR", h.name, run.error)
        if i % 10 == 0:
            print(f"collect {i + 1}/{len(hotels)} {time.time() - t0:.0f}s", flush=True)
    rnd = random.Random(42)
    answered_hotels = {h.id for h in hotels if rnd.random() < args.answers}
    for round_ in range(2):
        qs = list(s.scalars(select(Question).where(Question.status == "open").order_by(Question.id)))
        for q in qs:
            if q.hotel_id not in answered_hotels:
                continue
            if round_ == 1 and q.reason != "conflict":
                continue
            if round_ == 1 and rnd.random() < 0.4:
                continue  # часть уточняющих вопросов оставляем открытыми для демо
            orchestrator.run_now(
                s, "answer", hotel_id=q.hotel_id, char_keys=q.char_keys, trigger="simulator", params={"question_id": q.id}
            )
        print(f"answers round {round_ + 1}: done", flush=True)
    for query in [
        "тихий номер с видом на закат недалеко от моря",
        "с собакой, чтобы рядом был парк для выгула",
        "с детьми: детская площадка рядом и пляж без лестниц",
        "вид на горы, тихо, обязательно парковка",
        "в гуще событий, бары рядом и аптека, которая работает ночью",
        "первая линия и не переходить дорогу до пляжа",
        "хочу видеть закат над морем в октябре и ещё искупаться",
        "выспаться после ночного перелёта: плотные шторы и тишина",
    ]:
        search(s, query)
    s.commit()
    out = ROOT / "data" / "seed" / "seed.json.gz"
    out.parent.mkdir(parents=True, exist_ok=True)
    print("dump:", dump_seed(s, out), "->", out, f"{out.stat().st_size / 1e6:.1f} MB")
    print(f"total {time.time() - t0:.0f}s")
