"""Оркестратор агентов: очередь запусков, журнал, фоновый воркер, актуализация.

Агенты и их роли:
  * Оркестратор — планирует, какие характеристики собирать и в каком порядке (зависимости);
  * Геоаналитик — виды, закат, шум, маршруты (рельеф + застройка);
  * Исследователь OSM — инфраструктура, часы работы, история здания, теги;
  * Климатолог — климат и море по месяцам;
  * Интервьюер — вопросы отельеру и разбор свободных ответов;
  * Арбитр — сводит источники, ловит противоречия, следит за сроком годности;
  * Консьерж — понимает запрос гостя и объясняет выбор (agents/concierge.py).
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
import time
import traceback

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import SessionLocal
from ..knowledge.registry import all_specs, get_spec, ordered_keys
from ..models import AgentRun, AgentStep, Fact, GeoOverride, Hotel, Question, ResolvedValue, utcnow
from ..region import get_region
from . import collector
from .context import HotelContext

log = logging.getLogger(__name__)

VIEW_KEYS = ["sea_view", "sunset_view", "mountain_view", "landmark_view", "window_outlook", "quietness"]


def _clean(obj):
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        v = float(obj)
        return None if np.isnan(v) else v
    if isinstance(obj, float) and obj != obj:
        return None
    if isinstance(obj, np.ndarray):
        return _clean(obj.tolist())
    if isinstance(obj, (dt.datetime, dt.date)):
        return obj.isoformat()
    return obj


class RunLogger:
    def __init__(self, session: Session, run: AgentRun, store: bool = True):
        self.session = session
        self.run = run
        self.run_id = run.id
        self.store = store
        self.idx = session.scalar(select(func.count(AgentStep.id)).where(AgentStep.run_id == run.id)) or 0
        self.counts: dict[str, int] = {}

    def step(self, agent: str, kind: str, message: str, char_key: str | None = None, payload: dict | None = None,
             duration_ms: int | None = None) -> None:
        self.counts[kind] = self.counts.get(kind, 0) + 1
        if not self.store:
            return
        self.session.add(AgentStep(run_id=self.run_id, idx=self.idx, agent=agent, kind=kind, char_key=char_key,
                                   message=message[:4000], payload=_clean(payload or {}), duration_ms=duration_ms))
        self.idx += 1


def create_run(session: Session, kind: str, hotel_id: str | None = None, char_keys: list[str] | None = None,
               trigger: str = "manual", params: dict | None = None) -> AgentRun:
    run = AgentRun(kind=kind, hotel_id=hotel_id, char_keys=char_keys or [], trigger=trigger, params=_clean(params or {}))
    session.add(run)
    session.flush()
    return run


# ----------------------------------------------------------------------------------------
def collect_hotel(session: Session, hotel: Hotel, keys: list[str] | None, logger: RunLogger, reason: str,
                  allow_questions: bool = True) -> dict:
    specs = all_specs(session)
    order = [k for k in ordered_keys(specs) if keys is None or k in keys]
    if keys and "building_levels" not in keys and any(k in VIEW_KEYS for k in order):
        pass  # этажность берём из текущего знания
    ctx = HotelContext(session, hotel, get_region(), logger)
    logger.step("Оркестратор", "plan", f"Отель «{hotel.name}»: собираю {len(order)} характеристик в порядке зависимостей",
                payload={"order": order})
    stats = {"ok": 0, "conflict": 0, "asked": 0, "unknown": 0, "stale": 0}
    for key in order:
        rv = collector.collect(ctx, specs[key], logger.run_id, logger, reason=reason, allow_questions=allow_questions)
        stats[rv.status] = stats.get(rv.status, 0) + 1
        session.flush()
    return stats


def _run_collect(session: Session, run: AgentRun, logger: RunLogger) -> str:
    hotel = session.get(Hotel, run.hotel_id)
    if hotel is None:
        raise ValueError(f"отель {run.hotel_id} не найден")
    stats = collect_hotel(session, hotel, run.char_keys or None, logger, reason=run.trigger)
    return ", ".join(f"{k}: {v}" for k, v in stats.items() if v)


def _run_collect_all(session: Session, run: AgentRun, logger: RunLogger) -> str:
    hotels = list(session.scalars(select(Hotel).order_by(Hotel.name)))
    keys = run.char_keys or None
    quiet = RunLogger(session, run, store=False)
    total: dict[str, int] = {}
    t0 = time.perf_counter()
    for i, h in enumerate(hotels):
        st = collect_hotel(session, h, keys, quiet, reason=run.trigger)
        for k, v in st.items():
            total[k] = total.get(k, 0) + v
        if i % 20 == 0:
            session.commit()
            logger.step("Оркестратор", "plan", f"Обработано {i + 1}/{len(hotels)} отелей")
    logger.step("Оркестратор", "result", f"Готово за {time.perf_counter() - t0:.0f} с: " +
                ", ".join(f"{k}: {v}" for k, v in total.items() if v), payload=total)
    return ", ".join(f"{k}: {v}" for k, v in total.items() if v)


def _run_answer(session: Session, run: AgentRun, logger: RunLogger) -> str:
    from .interviewer import answer_question
    from .simulator import simulate_answer

    q = session.get(Question, run.params["question_id"])
    if q is None:
        raise ValueError("вопрос не найден")
    text = run.params.get("text")
    who = run.params.get("answered_by", "hotelier")
    if not text:
        text = simulate_answer(session, q)
        who = "simulator"
        logger.step("Отельер (симуляция)", "result", f"Ответ: «{text}»", char_key=(q.char_keys or [None])[0])
    else:
        logger.step("Отельер", "result", f"Ответ: «{text}»", char_key=(q.char_keys or [None])[0])
    res = answer_question(session, q, text, answered_by=who, logger=logger)
    return "; ".join(f"{k}: {', '.join(v['fields'].keys()) or 'не разобрано'}" for k, v in res.items())


def _run_actualize(session: Session, run: AgentRun, logger: RunLogger) -> str:
    now = utcnow()
    stale = list(session.scalars(select(ResolvedValue).where(ResolvedValue.valid_until.is_not(None),
                                                             ResolvedValue.valid_until < now)))
    by_hotel: dict[str, list[str]] = {}
    for rv in stale:
        by_hotel.setdefault(rv.hotel_id, []).append(rv.char_key)
    logger.step("Арбитр", "plan", f"Найдено устаревших значений: {len(stale)} у {len(by_hotel)} отелей",
                payload={"stale": len(stale)})
    limit = int(run.params.get("limit", 50))
    n = 0
    for hotel_id, keys in list(by_hotel.items())[:limit]:
        hotel = session.get(Hotel, hotel_id)
        logger.step("Оркестратор", "plan", f"Перепроверяю «{hotel.name}»: {', '.join(keys)}", payload={"keys": keys})
        quiet = RunLogger(session, run, store=False)
        collect_hotel(session, hotel, keys, quiet, reason="stale", allow_questions=True)
        n += 1
        session.commit()
    return f"перепроверено отелей: {n}, устаревших значений было: {len(stale)}"


def _run_scenario(session: Session, run: AgentRun, logger: RunLogger) -> str:
    rd = get_region()
    radius = float(run.params.get("radius_m", 1200))
    reset_ids = run.params.get("reset_ids")
    if reset_ids:
        overrides = [session.get(GeoOverride, i) for i in reset_ids]
        what = f"снесены построенные здания ({len(overrides)})"
    else:
        ov = session.get(GeoOverride, run.params["override_id"])
        rd.apply_building_override(ov.id, ov.geometry["coordinates"][0], ov.height_m)
        overrides = [ov]
        what = f"«{ov.label}» ({ov.height_m:.0f} м)"
    centres = []
    for ov in overrides:
        if ov is None:
            continue
        ring = np.asarray(ov.geometry["coordinates"][0], dtype=float)
        cx, cy = rd.xy(float(ring[:, 0].mean()), float(ring[:, 1].mean()))
        centres.append((float(cx), float(cy)))
    hotels = []
    for h in session.scalars(select(Hotel)):
        b = h.building or {}
        if b.get("x") is None or not centres:
            continue
        d = min(float(np.hypot(b["x"] - cx, b["y"] - cy)) for cx, cy in centres)
        if d <= radius:
            hotels.append((d, h))
    hotels.sort(key=lambda t: t[0])
    logger.step("Оркестратор", "plan", f"Изменение среды: {what}. "
                f"Пересчитываю виды и шум для {len(hotels)} отелей в радиусе {radius:.0f} м",
                payload={"hotels": [h.id for _, h in hotels]})
    changed = 0
    for d, h in hotels:
        before = {k: (session.get(ResolvedValue, (h.id, k)).summary if session.get(ResolvedValue, (h.id, k)) else "")
                  for k in VIEW_KEYS}
        quiet = RunLogger(session, run, store=False)
        collect_hotel(session, h, VIEW_KEYS, quiet, reason="change:reset" if reset_ids else "change:building",
                      allow_questions=False)
        diffs = []
        for k in VIEW_KEYS:
            rv = session.get(ResolvedValue, (h.id, k))
            if rv and rv.summary != before[k]:
                diffs.append(k)
        if diffs:
            changed += 1
            logger.step("Арбитр", "result", f"«{h.name}» ({d:.0f} м): изменились {', '.join(diffs)}",
                        payload={"hotel_id": h.id, "keys": diffs})
        session.commit()
    return f"затронуто отелей: {len(hotels)}, изменилось знание у {changed}"


HANDLERS = {"collect": _run_collect, "collect_all": _run_collect_all, "answer": _run_answer,
            "actualize": _run_actualize, "scenario": _run_scenario}


def execute_run(run_id: int) -> None:
    session = SessionLocal()
    try:
        run = session.get(AgentRun, run_id)
        if run is None or run.status not in {"queued", "running"}:
            return
        run.status = "running"
        run.started_at = utcnow()
        session.commit()
        logger = RunLogger(session, run)
        try:
            summary = HANDLERS[run.kind](session, run, logger)
            run.status = "done"
            run.summary = summary or ""
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            run = session.get(AgentRun, run_id)
            run.status = "error"
            run.error = f"{exc.__class__.__name__}: {exc}"
            log.error("Запуск %s упал: %s", run_id, traceback.format_exc())
        run.finished_at = utcnow()
        session.commit()
    finally:
        session.close()


def run_now(session: Session, kind: str, **kw) -> AgentRun:
    """Создаёт запуск и сразу выполняет его в текущем потоке (для коротких задач)."""
    run = create_run(session, kind, **kw)
    session.commit()
    execute_run(run.id)
    session.expire_all()
    return session.get(AgentRun, run.id)


# ----------------------------------------------------------------------------------------
class Worker(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True, name="agents-worker")
        self.stop_event = threading.Event()
        self.last_actualize = time.time()

    def run(self) -> None:
        log.info("Воркер агентов запущен")
        while not self.stop_event.is_set():
            try:
                self.tick()
            except Exception:  # noqa: BLE001
                log.error("Ошибка воркера: %s", traceback.format_exc())
                time.sleep(2)
            self.stop_event.wait(0.7)

    def tick(self) -> None:
        with SessionLocal() as session:
            run = session.scalars(select(AgentRun).where(AgentRun.status == "queued").order_by(AgentRun.id).limit(1)).first()
            run_id = run.id if run else None
            if run is None and settings.hotelier_autoreply:
                q = session.scalars(select(Question).where(Question.status == "open").order_by(Question.id).limit(1)).first()
                if q is not None and (utcnow() - q.created_at).total_seconds() > 20:
                    r = create_run(session, "answer", hotel_id=q.hotel_id, char_keys=q.char_keys, trigger="autoreply",
                                   params={"question_id": q.id})
                    session.commit()
                    run_id = r.id
            if run_id is None and settings.actualize_interval_min > 0 and \
                    time.time() - self.last_actualize > settings.actualize_interval_min * 60:
                self.last_actualize = time.time()
                r = create_run(session, "actualize", trigger="schedule", params={"limit": 20})
                session.commit()
                run_id = r.id
        if run_id is not None:
            execute_run(run_id)


_worker: Worker | None = None


def start_worker() -> None:
    global _worker
    if _worker is None:
        _worker = Worker()
        _worker.start()


def reapply_overrides(session: Session) -> None:
    rd = get_region()
    for ov in session.scalars(select(GeoOverride).where(GeoOverride.active.is_(True))):
        rd.apply_building_override(ov.id, ov.geometry["coordinates"][0], ov.height_m)


def spec_or_404(session: Session, key: str) -> dict:
    spec = get_spec(session, key)
    if spec is None:
        raise KeyError(key)
    return spec


def fact_counts(session: Session) -> dict:
    rows = session.execute(select(Fact.source, func.count(Fact.id)).where(Fact.status == "active").group_by(Fact.source)).all()
    return {s: n for s, n in rows}
