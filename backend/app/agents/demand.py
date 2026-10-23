"""Польза вопроса отельеру: насколько ответ «да» поднимет отель в выдаче по запросам гостей.

Модель спроса — запросы из журнала поисков плюс «демо-спрос»: примеры с главной страницы и размеченные
выборки запросов (настроечная и dev; отложенная не используется). Для каждого запроса консьерж оценивает все
отели той же функцией, что в поиске. Польза вопроса (отель h, характеристика k) считается в закрытой форме:
вклад пожелания k меняется с текущего на conf·1 + (1 − conf)·PRIOR (ответ «да» с уверенностью ответа
отельера), балл отеля пересчитывается, и польза — это сумма прироста позиций в пределах топ-30 по всем
запросам спроса, где есть k.
"""

from __future__ import annotations

import bisect
import threading
import time
from dataclasses import dataclass, field

import yaml
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..knowledge.registry import all_specs
from ..models import Hotel, Question, ResolvedValue, SearchLog
from . import concierge

ANSWER_CONF = 0.8  # уверенность факта из ответа отельера (см. interviewer: 0,8 × (0,6 + 0,4 × качество разбора))
TOP = 30           # позиции дальше топ-30 для гостя равноценны
TTL_S = 120

_lock = threading.Lock()
_cache: dict = {"key": None, "at": 0.0, "model": None}


@dataclass
class DemandQuery:
    text: str
    source: str  # search | demo
    weight: float = 1.0
    keys: list[str] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)       # отель → балл (без округления)
    parts: dict[str, dict[str, tuple]] = field(default_factory=dict)  # отель → ключ → (w, em, mult, total_w)
    sorted_scores: list[float] = field(default_factory=list)      # по возрастанию, для поиска места


def _demo_texts() -> list[str]:
    out = list(concierge.EXAMPLES)
    for name in ("queries.yaml", "queries_dev.yaml"):
        path = settings.data_dir / "eval" / name
        if path.exists():
            with open(path, encoding="utf-8") as f:
                out += [r["query"] for r in yaml.safe_load(f) or []]
    return out


def _build(session: Session) -> list[DemandQuery]:
    specs = all_specs(session)
    hotels = list(session.scalars(select(Hotel)))
    texts: dict[str, DemandQuery] = {}
    for t in _demo_texts():
        texts.setdefault(t.strip().lower(), DemandQuery(t, "demo"))
    for s in session.scalars(select(SearchLog)):
        k = (s.query or "").strip().lower()
        if not k:
            continue
        if k in texts and texts[k].source == "search":
            texts[k].weight += 1
        else:
            texts[k] = DemandQuery(s.query, "search")
    rvs: dict[tuple[str, str], ResolvedValue] = {(rv.hotel_id, rv.char_key): rv for rv in session.scalars(select(ResolvedValue))}
    out = []
    for dq in texts.values():
        pq = concierge.understand(dq.text, specs)
        groups = concierge._group(pq.criteria)
        if not groups:
            continue
        dq.keys = list(groups)
        month = next((c.month for c in pq.criteria if c.month), None)
        for h in hotels:
            r = concierge.evaluate_hotel(h, specs, groups, rvs, {}, month)
            tw = r["_total_w"] or 1.0
            dq.scores[h.id] = 100 * sum(n["contrib"] for n in r["needs"]) / tw
            dq.parts[h.id] = {n["key"]: (n["w"], n["em"], n["mult"], tw) for n in r["needs"]}
        dq.sorted_scores = sorted(dq.scores.values())
        out.append(dq)
    return out


def model(session: Session) -> list[DemandQuery]:
    """Спрос с оценками всех отелей; пересчитывается, когда меняются знания или журнал поисков."""
    key = (session.scalar(select(func.count(SearchLog.id))), session.scalar(select(func.max(ResolvedValue.updated_at))),
           session.scalar(select(func.count(Hotel.id))))
    with _lock:
        if _cache["key"] == key and time.time() - _cache["at"] < TTL_S:
            return _cache["model"]
    m = _build(session)
    with _lock:
        _cache.update(key=key, at=time.time(), model=m)
    return m


def _rank(dq: DemandQuery, score: float) -> int:
    """Место отеля с таким баллом: 1 + число отелей с баллом строго выше."""
    return 1 + len(dq.sorted_scores) - bisect.bisect_right(dq.sorted_scores, score)


def utility(session: Session, q: Question, demand: list[DemandQuery] | None = None) -> dict:
    """Польза вопроса: прирост позиций в топ-30 по запросам спроса при ответе «да» и лучший пример."""
    demand = model(session) if demand is None else demand
    total, n_q, best = 0.0, 0, None
    for dq in demand:
        ks = [k for k in q.char_keys or [] if k in dq.keys]
        parts = dq.parts.get(q.hotel_id)
        if not ks or parts is None:
            continue
        n_q += 1
        old = dq.scores[q.hotel_id]
        new = old
        for k in ks:
            w, em, mult, tw = parts[k]
            em_yes = max(em, ANSWER_CONF + (1 - ANSWER_CONF) * concierge.PRIOR)
            new += 100 * w * (em_yes - em) * mult / tw
        before = _rank(dq, old)
        after = _rank(dq, new)  # свой старый балл ниже нового и в счёт «выше» не попадает
        gain = (min(before, TOP + 1) - min(after, TOP + 1)) * dq.weight
        total += gain
        if gain > 0 and (best is None or gain > best["gain"]):
            best = {"query": dq.text, "before": before, "after": after, "demo": dq.source == "demo", "gain": gain}
    return {"score": round(total, 1), "queries": n_q, "example": best}


def explain(u: dict) -> str:
    if not u["queries"]:
        return "Ответ пока не влияет на запросы гостей из журнала и демо-спроса"
    s = f"Ответ влияет на {u['queries']} {_queries_word(u['queries'])} гостей"
    ex = u.get("example")
    if ex:
        s += (f", например «{ex['query']}»{' (демо-спрос)' if ex['demo'] else ''}. При ответе «да» отель поднимется "
              f"с {ex['before']}-го на {ex['after']}-е место")
    else:
        s += ", но место в топ-30 от ответа не изменится"
    return s


def _queries_word(n: int) -> str:
    from ..tools.base import plural

    return plural(n, "запрос", "запроса", "запросов")


def ranked_open_questions(session: Session, hotel_id: str | None = None, limit: int | None = None) -> list[tuple]:
    """Открытые вопросы, отсортированные по пользе: [(вопрос, польза)]."""
    demand = model(session)
    stmt = select(Question).where(Question.status == "open")
    if hotel_id:
        stmt = stmt.where(Question.hotel_id == hotel_id)
    rows = [(q, utility(session, q, demand)) for q in session.scalars(stmt)]
    rows.sort(key=lambda t: (-t[1]["score"], -t[1]["queries"], t[0].id))
    return rows[:limit] if limit else rows


def coverage(session: Session) -> dict:
    """Сколько пользы (прироста позиций по спросу) дают лучшие 20% вопросов."""
    rows = ranked_open_questions(session)
    total = sum(u["score"] for _, u in rows)
    if not rows or total <= 0:
        return {"questions": len(rows), "top_share": None}
    top = rows[: max(1, len(rows) // 5)]
    demand = model(session)
    return {"questions": len(rows), "top_n": len(top), "top_share": round(sum(u["score"] for _, u in top) / total, 3),
            "useful": sum(1 for _, u in rows if u["score"] > 0), "demand_queries": len(demand),
            "demo_queries": sum(1 for d in demand if d.source == "demo")}
