"""Метрики качества на размеченных выборках (data/eval).

python scripts/evaluate.py          # правила
python scripts/evaluate.py --llm    # то же через LLM (нужен LLM_API_KEY в окружении)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

ap = argparse.ArgumentParser()
ap.add_argument("--llm", action="store_true", help="разбирать запросы и ответы через LLM")
args = ap.parse_args()
if not args.llm:
    os.environ["LLM_API_KEY"] = ""

import yaml  # noqa: E402

from app.evaluation import _same, eval_answers, eval_queries, eval_sun  # noqa: E402
from app.knowledge.registry import load_builtin  # noqa: E402


def llm_queries(name: str) -> dict:
    from app.agents.concierge import llm_parse

    specs = {s["key"]: s for s in load_builtin()}
    rows = yaml.safe_load(open(ROOT / "data" / "eval" / name, encoding="utf-8"))
    tp = fp = fn = 0
    for r in rows:
        pq = llm_parse(r["query"], specs)
        got = {c.key for c in pq.criteria} if pq else set()
        exp = set(r["expect"])
        tp += len(got & exp)
        fp += len(got - exp)
        fn += len(exp - got)
    p = tp / (tp + fp) if tp + fp else 0
    rc = tp / (tp + fn) if tp + fn else 0
    return {
        "queries": len(rows),
        "precision": round(p, 3),
        "recall": round(rc, 3),
        "f1": round(2 * p * rc / (p + rc), 3) if p + rc else 0,
    }


def llm_answers(name: str) -> dict:
    from app.agents.interviewer import parse_with_llm

    specs = {s["key"]: s for s in load_builtin()}
    rows = yaml.safe_load(open(ROOT / "data" / "eval" / name, encoding="utf-8"))
    total = correct = 0
    for r in rows:
        spec = specs[r["char_key"]]
        q = (spec.get("question") or {}).get("text", "")
        got = (parse_with_llm(spec, q, r["answer"]) or {"fields": {}})["fields"]
        for f, exp in r["expect"].items():
            total += 1
            correct += f in got and _same(exp, got[f])
    return {"answers": len(rows), "fields": total, "accuracy": round(correct / total, 3) if total else None}


def strip(d: dict) -> dict:
    return {k: v for k, v in d.items() if k not in {"errors", "per_characteristic", "checks"}}


out = {
    "rules": {
        "answers_tuning": strip(eval_answers("hotelier_answers.yaml")),
        "answers_dev": strip(eval_answers("hotelier_answers_dev.yaml")),
        "answers_holdout": strip(eval_answers("hotelier_answers_holdout.yaml")),
        "queries_tuning": strip(eval_queries("queries.yaml")),
        "queries_dev": strip(eval_queries("queries_dev.yaml")),
        "queries_holdout": strip(eval_queries("queries_holdout.yaml")),
    },
    "sun_vs_pyephem": strip(eval_sun()),
}
if args.llm:
    out["llm"] = {
        "answers_holdout": llm_answers("hotelier_answers_holdout.yaml"),
        "queries_holdout": llm_queries("queries_holdout.yaml"),
    }
print(json.dumps(out, ensure_ascii=False, indent=2))
