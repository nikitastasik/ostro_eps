"""Метрики качества: покрытие, доля вопросов отельеру, противоречия, точность на размеченных выборках."""

from __future__ import annotations

import datetime as dt
import threading
import time

import yaml
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import settings
from .geo.sun import solar_position, sunset_utc
from .knowledge.registry import all_specs, load_builtin
from .models import Conflict, Fact, Hotel, Question, ResolvedValue, SearchLog, utcnow
from .nlp.answer_parser import parse_answer
from .nlp.query_parser import parse_query
from .region import get_region

_cache: dict = {"at": 0.0, "data": None}
_lock = threading.Lock()


def _load(name: str) -> list:
    path = settings.data_dir / "eval" / name
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or []


def _same(expected, got) -> bool:
    if isinstance(expected, bool) or isinstance(got, bool):
        return bool(expected) == bool(got) and isinstance(got, bool) == isinstance(expected, bool)
    if isinstance(expected, (int, float)) and isinstance(got, (int, float)):
        return abs(float(expected) - float(got)) <= 0.01 * max(1.0, abs(float(expected)))
    if isinstance(expected, list) and isinstance(got, list):
        if expected and isinstance(expected[0], dict):
            if len(expected) != len(got):
                return False
            return all(all(str(g.get(k)) == str(v) for k, v in e.items()) for e, g in zip(expected, got))
        return sorted(map(str, expected)) == sorted(map(str, got))
    return str(expected).strip().lower() == str(got).strip().lower()


def eval_answers(name: str = "hotelier_answers.yaml") -> dict:
    specs = {s["key"]: s for s in load_builtin()}
    rows = _load(name)
    total = correct = extracted = wrong = 0
    per_key: dict[str, dict] = {}
    errors = []
    for r in rows:
        spec = specs.get(r["char_key"])
        if not spec:
            continue
        got = parse_answer(spec, r["answer"])["fields"]
        k = per_key.setdefault(r["char_key"], {"fields": 0, "correct": 0})
        for f, exp in r["expect"].items():
            total += 1
            k["fields"] += 1
            if f in got:
                extracted += 1
                if _same(exp, got[f]):
                    correct += 1
                    k["correct"] += 1
                else:
                    wrong += 1
                    errors.append({"answer": r["answer"], "field": f, "expected": exp, "got": got[f]})
            else:
                errors.append({"answer": r["answer"], "field": f, "expected": exp, "got": None})
        for f in got:
            if f not in r["expect"] and f not in {"reduction_db"}:
                pass
    return {
        "answers": len(rows), "fields": total,
        "accuracy": round(correct / total, 3) if total else None,
        "precision": round(correct / extracted, 3) if extracted else None,
        "recall": round(extracted / total, 3) if total else None,
        "per_characteristic": {k: round(v["correct"] / v["fields"], 2) for k, v in per_key.items()},
        "errors": errors[:12],
    }


def eval_queries(name: str = "queries.yaml") -> dict:
    specs = {s["key"]: s for s in load_builtin()}
    rows = _load(name)
    tp = fp = fn = exact = 0
    errors = []
    for r in rows:
        got = {c.key for c in parse_query(r["query"], specs).criteria}
        exp = set(r["expect"])
        tp += len(got & exp)
        fp += len(got - exp)
        fn += len(exp - got)
        exact += got == exp
        if got != exp:
            errors.append({"query": r["query"], "missed": sorted(exp - got), "extra": sorted(got - exp)})
    p = tp / (tp + fp) if tp + fp else 0

    rc = tp / (tp + fn) if tp + fn else 0
    return {"queries": len(rows), "precision": round(p, 3), "recall": round(rc, 3),
            "f1": round(2 * p * rc / (p + rc), 3) if p + rc else 0, "exact_match": round(exact / len(rows), 3) if rows else None,
            "errors": errors[:12]}


def eval_sun() -> dict:
    """Сверка солнечного модуля (алгоритм NOAA) с независимым расчётом PyEphem (data/eval/sun_reference.yaml)."""
    path = settings.data_dir / "eval" / "sun_reference.yaml"
    if not path.exists():
        return {"checks": []}
    with open(path, encoding="utf-8") as f:
        ref_doc = yaml.safe_load(f)
    rows = []
    for ref in ref_doc["rows"]:
        d = dt.date.fromisoformat(ref["date"])
        t = sunset_utc(d, 43.5855, 39.7231)
        _, az = solar_position(t, 43.5855, 39.7231)
        local = t + dt.timedelta(hours=3)
        rh, rm = map(int, ref["sunset"].split(":"))
        got_min = local.hour * 60 + local.minute + local.second / 60
        rows.append({"date": ref["date"], "sunset": local.strftime("%H:%M"), "ref_sunset": ref["sunset"],
                     "time_error_min": round(abs(got_min - (rh * 60 + rm)), 1), "azimuth": round(az, 1),
                     "ref_azimuth": ref["azimuth"], "azimuth_error_deg": round(abs(az - ref["azimuth"]), 2)})
    return {"reference": ref_doc.get("source"), "checks": rows,
            "max_time_error_min": max(r["time_error_min"] for r in rows),
            "max_azimuth_error_deg": max(r["azimuth_error_deg"] for r in rows)}


def eval_geo_gold(session: Session) -> dict:
    """Точность геохарактеристик против ручной разметки data/eval/geo_gold.yaml."""
    rows = _load("geo_gold.yaml") or []
    total = correct = 0
    errors = []
    for r in rows:
        rv = session.get(ResolvedValue, (r["hotel_id"], r["char_key"]))
        got = (rv.value or {}) if rv else {}
        for f, exp in (r.get("expect") or {}).items():
            total += 1
            g = got.get(f)
            ok = (abs(float(g) - float(exp)) <= 2) if f == "min_floor" and g is not None and exp is not None else _same(exp, g)
            correct += bool(ok)
            if not ok:
                errors.append({"hotel_id": r["hotel_id"], "char_key": r["char_key"], "field": f, "expected": exp, "got": g})
    return {"labels": len(rows), "fields": total, "accuracy": round(correct / total, 3) if total else None,
            "errors": errors[:12], "note": "разметка заполняется командой по фото и панорамам" if not rows else ""}


def eval_ranking(session: Session, name: str = "ranking_pairs.yaml") -> dict:
    """Согласие ранжирования с разметкой: доля пар «для запроса Q отель A выше B», где так и вышло."""
    from .agents.concierge import search

    rows = _load(name) or []
    if not rows:
        return {"pairs": 0}
    ok, errors, cache = 0, [], {}
    names = {h.id: h.label for h in session.scalars(select(Hotel))}
    for r in rows:
        q = r["query"]
        if q not in cache:
            res = search(session, q, limit=10_000, log=False)
            order = {}
            for i, x in enumerate(res["results"]):
                order[x["hotel"]["id"]] = i
                for o in x.get("others") or []:  # свёрнутые корпуса комплекса стоят там же
                    order[o["id"]] = i
            cache[q] = order
        pa, pb = cache[q].get(r["above"]), cache[q].get(r["below"])
        good = pa is not None and pb is not None and pa < pb
        ok += good
        if not good:
            errors.append({"query": q, "above": names.get(r["above"], r["above"]), "below": names.get(r["below"], r["below"]),
                           "pos_above": None if pa is None else pa + 1, "pos_below": None if pb is None else pb + 1})
    return {"pairs": len(rows), "agreement": round(ok / len(rows), 3), "errors": errors}


def _demand_coverage(session: Session) -> dict:
    from .agents.demand import coverage

    return coverage(session)


def compute_metrics(session: Session, force: bool = False) -> dict:
    with _lock:
        if not force and _cache["data"] is not None and time.time() - _cache["at"] < 30:
            return _cache["data"]
    specs = all_specs(session)
    n_hotels = session.scalar(select(func.count(Hotel.id))) or 1
    now = utcnow()
    rvs = list(session.scalars(select(ResolvedValue)))
    per: dict[str, dict] = {k: {"key": k, "title": s["title"], "group": s["group"], "known": 0, "conf_sum": 0.0,
                                "conflict": 0, "stale": 0, "asked": 0, "sources": {}} for k, s in specs.items()}
    for rv in rvs:
        p = per.get(rv.char_key)
        if p is None:
            continue
        if rv.status in {"ok", "conflict", "stale"} and rv.value:
            p["known"] += 1
            p["conf_sum"] += rv.confidence
            for s in rv.sources or []:
                p["sources"][s] = p["sources"].get(s, 0) + 1
        if rv.status == "conflict":
            p["conflict"] += 1
        if rv.valid_until and rv.valid_until < now:
            p["stale"] += 1
    q_pairs = set()
    q_answered = 0
    q_total = 0
    for q in session.scalars(select(Question)):
        q_total += 1
        q_answered += q.status == "answered"
        for k in q.char_keys or []:
            q_pairs.add((q.hotel_id, k))
            if k in per:
                per[k]["asked"] += 1
    per_list = []
    for p in per.values():
        per_list.append({**{k: v for k, v in p.items() if k != "conf_sum"},
                         "coverage": round(p["known"] / n_hotels, 3),
                         "avg_confidence": round(p["conf_sum"] / p["known"], 3) if p["known"] else 0.0,
                         "question_share": round(p["asked"] / n_hotels, 3)})
    pairs = n_hotels * len(specs)
    known_pairs = sum(p["known"] for p in per.values())
    open_data = sum(1 for rv in rvs if rv.value and any(s in {"geo", "osm", "climate", "osm_estimate"} for s in rv.sources or []))
    hotelier_only = sum(1 for rv in rvs if rv.value and rv.sources and all(s.startswith("hotelier") for s in rv.sources))
    c_rows = list(session.scalars(select(Conflict)))
    conflicts = {
        "detected": len(c_rows), "open": sum(1 for c in c_rows if c.status == "open"),
        "resolved_auto": sum(1 for c in c_rows if c.status == "resolved" and c.resolved_by == "arbiter"),
        "resolved_moderator": sum(1 for c in c_rows if c.resolved_by == "moderator"),
        "with_question": sum(1 for c in c_rows if c.question_id),
    }
    facts_by_source = dict(session.execute(select(Fact.source, func.count()).where(Fact.status == "active")
                                           .group_by(Fact.source)).all())
    region_stats = get_region().meta.get("stats", {})
    data = {
        "generated_at": now.isoformat() + "Z",
        "hotels": n_hotels, "characteristics": len(specs),
        "coverage": round(known_pairs / pairs, 3) if pairs else 0,
        "coverage_open_data_pairs": open_data, "hotelier_only_pairs": hotelier_only,
        "question_share": round(len(q_pairs) / pairs, 3) if pairs else 0,
        "questions": {"total": q_total, "answered": q_answered, "open": q_total - q_answered},
        "conflicts": conflicts,
        "stale": sum(p["stale"] for p in per.values()),
        "facts_by_source": facts_by_source,
        "per_characteristic": sorted(per_list, key=lambda x: (x["group"], -x["coverage"])),
        "searches": session.scalar(select(func.count(SearchLog.id))) or 0,
        "eval": {"answers": eval_answers(), "queries": eval_queries(),
                 "answers_dev": eval_answers("hotelier_answers_dev.yaml"), "queries_dev": eval_queries("queries_dev.yaml"),
                 "answers_holdout": eval_answers("hotelier_answers_holdout.yaml"),
                 "queries_holdout": eval_queries("queries_holdout.yaml"), "sun": eval_sun(),
                 "geo_gold": eval_geo_gold(session), "ranking": eval_ranking(session),
                 "demand": _demand_coverage(session),
                 "levels_imputation": region_stats.get("levels_imputation")},
        "data_sources": {"buildings": region_stats.get("buildings"), "height_sources": region_stats.get("height_sources"),
                         "pois": region_stats.get("pois"), "roads": region_stats.get("roads")},
    }
    with _lock:
        _cache.update({"at": time.time(), "data": data})
    return data
