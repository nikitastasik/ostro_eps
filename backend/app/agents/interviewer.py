"""Агент-интервьюер: формулирует вопросы отельеру и раскладывает ответы по полям."""

from __future__ import annotations

import datetime as dt
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import llm
from ..knowledge.registry import get_spec
from ..models import Fact, Hotel, Question, utcnow
from ..nlp.answer_parser import parse_answer
from . import arbiter

ANIMALS_RU = {"dog": "собаки", "cat": "кошки", "bird": "птицы", "rodent": "грызуны"}
SIDE_RU = {"sea": "на море", "courtyard": "во двор", "street": "на улицу", "mountains": "на горы", "park": "на парк",
           "city": "на город", "pool": "на бассейн"}
WINDOWS_RU = {"standard": "обычные стеклопакеты (≈−25 дБ)", "soundproof": "шумозащитные окна (≈−32 дБ)",
              "triple": "тройные стеклопакеты (≈−36 дБ)"}
WHEEL_RU = {"yes": "доступен для гостей на коляске", "limited": "доступен частично", "no": "не приспособлен для коляски"}


def _yn(v) -> str:
    return "есть" if v else "нет"


def render_summary(spec: dict, f: dict) -> str:
    k = spec["key"]
    p = "Со слов отельера: "
    if k == "pets_policy":
        if f.get("allowed") is False:
            return p + "с животными не размещают"
        parts = ["можно с животными"]
        if f.get("animals"):
            parts[0] += " (" + ", ".join(ANIMALS_RU.get(a, a) for a in f["animals"]) + ")"
        if f.get("max_weight_kg"):
            parts.append(f"до {f['max_weight_kg']:g} кг")
        if f.get("fee_rub") is not None:
            unit = {"night": " в сутки", "stay": " за проживание"}.get(f.get("fee_unit"), "")
            parts.append("без доплаты" if f["fee_rub"] == 0 else f"доплата {f['fee_rub']:g} ₽{unit}")
        return p + ", ".join(parts)
    if k == "blackout_curtains":
        if not f.get("available"):
            return p + "плотных штор нет"
        return p + "блэкаут-шторы есть" + {"all": " во всех номерах", "some": " в части номеров"}.get(f.get("rooms"), "")
    if k == "soundproofing":
        return p + WINDOWS_RU.get(f.get("windows"), "тип окон не указан")
    if k == "workspace":
        parts = []
        if "desk" in f:
            parts.append(f"рабочий стол в номере: {_yn(f['desk'])}")
        if f.get("wifi_mbps"):
            parts.append(f"Wi‑Fi ≈{f['wifi_mbps']:g} Мбит/с")
        if "coworking" in f:
            parts.append(f"коворкинг: {_yn(f['coworking'])}")
        return p + "; ".join(parts)
    if k == "kids_facilities":
        names = {"crib": "детская кроватка", "kids_menu": "детское меню", "kids_club": "детский клуб"}
        return p + "; ".join(f"{names[x]}: {_yn(f[x])}" for x in names if x in f)
    if k == "elevator":
        return p + ("лифт есть" if f.get("available") else "лифта нет")
    if k == "accessibility":
        return p + WHEEL_RU.get(f.get("wheelchair"), "нет данных")
    if k == "room_orientation":
        cats = f.get("categories") or []
        parts = []
        for c in cats:
            side = SIDE_RU.get(c.get("side"), "") or (f"на {c['facade']}" if c.get("facade") else "")
            fl = c.get("floors")
            fl_txt = f" ({fl[0]}–{fl[1]} эт.)" if fl and fl[1] else (f" (с {fl[0]} эт.)" if fl else "")
            parts.append(f"{c['name']} — {side}{fl_txt}")
        return p + "; ".join(parts)
    if k == "building_levels":
        return p + f"{f.get('levels')} этажей"
    if k == "building_history":
        parts = []
        if f.get("year_built"):
            parts.append(f"здание {f['year_built']} года")
        if f.get("heritage"):
            parts.append("памятник архитектуры")
        if f.get("style"):
            parts.append(f"стиль: {f['style']}")
        return p + ", ".join(parts)
    if k in {"sea_view", "sunset_view", "mountain_view"}:
        what = {"sea_view": "море видно", "sunset_view": "закат виден", "mountain_view": "горы видны"}[k]
        if f.get("available") is False:
            return p + what.replace("видно", "не видно").replace("виден", "не виден").replace("видны", "не видны")
        s = p + what
        if f.get("min_floor") == 1:
            s += " из всех номеров"
        elif f.get("min_floor"):
            s += f" с {f['min_floor']}-го этажа"
        side = f.get("best_facade")
        if side:
            s += " (" + (SIDE_RU.get(side) or f"сторона {side}") + ")"
        return s
    if k == "car_access":
        parts = []
        if "own_parking" in f:
            parts.append("своя парковка " + ("есть" if f["own_parking"] else "отсутствует"))
        if f.get("parking_fee_rub") is not None:
            parts.append("бесплатно" if f["parking_fee_rub"] == 0 else f"{f['parking_fee_rub']:g} ₽/сутки")
        if "car_to_door" in f:
            parts.append("подъехать к входу " + ("можно" if f["car_to_door"] else "нельзя"))
        return p + ", ".join(parts)
    if k == "construction_nearby":
        return p + ("рядом идёт стройка" if f.get("sites_300m") else "строек рядом нет")
    vf = {**spec.get("value_fields", {}), **spec.get("extra_fields", {})}
    parts = []
    for key, v in f.items():
        title = vf.get(key, {}).get("title", key)
        if isinstance(v, bool):
            v = "да" if v else "нет"
        parts.append(f"{title}: {v}")
    return p + "; ".join(parts)


def _hypothesis_text(spec: dict, hypothesis: str | None) -> str:
    q = (spec.get("question") or {}).get("text") or f"Расскажите, пожалуйста: {spec['title'].lower()}?"
    if hypothesis:
        h = hypothesis.rstrip(".")
        return f"По нашему расчёту: {h[0].lower() + h[1:]}. Подтвердите или уточните, пожалуйста. {q}"
    return q


def ask_hotelier(session: Session, hotel: Hotel, spec: dict, reason: str = "missing", hypothesis: str | None = None,
                 run_id: int | None = None) -> tuple[Question, bool]:
    for q in session.scalars(select(Question).where(Question.hotel_id == hotel.id, Question.status == "open")):
        if spec["key"] in (q.char_keys or []):
            return q, False
    q = Question(hotel_id=hotel.id, char_keys=[spec["key"]], text=_hypothesis_text(spec, hypothesis), reason=reason,
                 context={"hypothesis": hypothesis, "fields": (spec.get("question") or {}).get("fields")}, run_id=run_id)
    session.add(q)
    session.flush()
    return q, True


def ask_clarification(session: Session, hotel_id: str, spec: dict, primary: Fact, other: Fact, fld: dict, a, b,
                      run_id: int | None = None) -> Question | None:
    for q in session.scalars(select(Question).where(Question.hotel_id == hotel_id, Question.status == "open")):
        if spec["key"] in (q.char_keys or []):
            return None
    base = (spec.get("question") or {}).get("text") or f"Уточните, пожалуйста: {spec['title'].lower()}?"
    src = arbiter.SOURCE_RU
    if other.source in {"hotelier", "hotelier_confirmed"}:
        text = (f"Наш расчёт ({src.get(primary.source, primary.source)}) показывает: {primary.summary.rstrip('.')}. "
                f"В вашем ответе: {other.summary.replace('Со слов отельера: ', '').rstrip('.')}. "
                f"Помогите разобраться — {base[0].lower() + base[1:]}")
    else:
        text = (f"Источники расходятся: {src.get(primary.source, primary.source)} — {primary.summary.rstrip('.')}, "
                f"{src.get(other.source, other.source)} — {other.summary.rstrip('.')}. {base}")
    q = Question(hotel_id=hotel_id, char_keys=[spec["key"]], text=text, reason="conflict",
                 context={"facts": [primary.id, other.id], "field": fld["field"], "values": [a, b],
                          "fields": (spec.get("question") or {}).get("fields")}, run_id=run_id)
    session.add(q)
    session.flush()
    return q


def parse_with_llm(spec: dict, question: str, answer: str) -> dict | None:
    vf = {**spec.get("value_fields", {}), **spec.get("extra_fields", {})}
    fields = (spec.get("question") or {}).get("fields") or list(vf.keys())
    schema = {f: {"type": vf[f].get("type"), "title": vf[f].get("title"), "values": vf[f].get("values")} for f in fields if f in vf}
    system = (
        "Ты агент, который раскладывает свободный ответ отельера по полям анкеты. Отвечай только JSON вида "
        '{"fields": {...}, "unclear": [...]}. Заполняй только то, что явно сказано в ответе; не додумывай. '
        "Типы: bool — true/false; int/number — число; enum — одно из values; list — массив значений из values; "
        "facade — одна из сторон С, СВ, В, ЮВ, Ю, ЮЗ, З, СЗ или sea/courtyard/street/mountains; "
        "months — массив номеров месяцев. Для categories верни массив объектов {name, side, facade, floors}."
    )
    user = json.dumps({"characteristic": spec["title"], "question": question, "fields": schema, "answer": answer},
                      ensure_ascii=False)
    res = llm.chat_json(system, user)
    if not res or not isinstance(res.get("fields"), dict):
        return None
    out = {k: v for k, v in res["fields"].items() if k in vf and v is not None}
    missing = [f for f in fields if f in vf and f not in out]
    return {"fields": out, "missing": missing, "confidence": round(len(out) / max(1, len(schema)), 2)}


def answer_question(session: Session, question: Question, text: str, answered_by: str = "hotelier",
                    logger=None, use_llm: bool = True) -> dict:
    """Принимает ответ, раскладывает по полям, создаёт факты и пересобирает знание."""
    now = utcnow()
    question.answer_text = text
    question.answered_by = answered_by
    question.answered_at = now
    # закрываем вопрос до пересборки знания: иначе арбитр решит, что уточнение уже задано
    question.status = "answered"
    session.flush()
    hotel = session.get(Hotel, question.hotel_id)
    result = {}
    for key in question.char_keys or []:
        spec = get_spec(session, key)
        if spec is None:
            continue
        fields = (question.context or {}).get("fields")
        parsed = parse_with_llm(spec, question.text, text) if use_llm and llm.enabled() else None
        method = "llm"
        if parsed is None or not parsed["fields"]:
            parsed = parse_answer(spec, text, fields)
            method = "rules"
        if logger:
            logger.step("Интервьюер", "llm" if method == "llm" else "decision",
                        f"Ответ разобран ({'LLM' if method == 'llm' else 'правила'}): "
                        f"{json.dumps(parsed['fields'], ensure_ascii=False)}"
                        + (f"; не удалось извлечь: {', '.join(parsed['missing'])}" if parsed["missing"] else ""),
                        char_key=key, payload=parsed)
        result[key] = {**parsed, "method": method}
        if not parsed["fields"]:
            continue
        source = "hotelier_confirmed" if answered_by == "hotelier_confirmed" else "hotelier"
        conf = 0.8 * (0.6 + 0.4 * parsed["confidence"]) * (1.0 if method == "rules" else 1.05)
        fact = Fact(hotel_id=hotel.id, char_key=key, value=parsed["fields"], summary=render_summary(spec, parsed["fields"]),
                    source=source, source_detail="ответ отельера в кабинете" + (" (симуляция)" if answered_by == "simulator" else ""),
                    method="hotelier.answer", confidence=min(0.9, conf), observed_at=now,
                    valid_until=now + dt.timedelta(days=int(spec.get("ttl_days", 365))),
                    evidence={"question_id": question.id, "question": question.text, "answer": text, "parse_method": method,
                              "missing": parsed["missing"], "answered_by": answered_by},
                    run_id=getattr(logger, "run_id", None))
        session.add(fact)
        session.flush()
        for f in session.scalars(select(Fact).where(Fact.hotel_id == hotel.id, Fact.char_key == key, Fact.status == "active",
                                                    Fact.source.in_(["hotelier", "hotelier_confirmed"]), Fact.id != fact.id)):
            f.status = "superseded"
        session.flush()
        rv = arbiter.resolve(session, hotel.id, spec, run_id=getattr(logger, "run_id", None), logger=logger,
                             reason="hotelier_answer")
        if logger:
            logger.step("Арбитр", "result", f"Итог: {rv.summary} (уверенность {rv.confidence:.2f}, статус {rv.status})",
                        char_key=key, payload={"status": rv.status, "confidence": rv.confidence})
    question.parsed = result
    question.parse_method = next((v["method"] for v in result.values()), None)
    question.status = "answered"
    session.flush()
    return result
