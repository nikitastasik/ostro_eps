"""Имитация отельера (по условию задачи отельера можно имитировать).

У каждого отеля детерминированная «скрытая правда» (зерно — id отеля). Отельер
отвечает свободным текстом в разных формулировках, иногда неполно, а иногда
приукрашивает (например, «море видно из всех номеров», хотя по расчёту только
с 5-го этажа), — так в демо проявляются противоречия источников.
Если подключена LLM, она переписывает ответ живым языком по той же правде.
"""

from __future__ import annotations

import hashlib
import json
import random

from sqlalchemy.orm import Session

from .. import llm
from ..geo.proj import COMPASS_8, COMPASS_8_LONG
from ..models import Hotel, Question, ResolvedValue
from ..tools.base import plural

# «окна на запад»: отельер называет сторону словом, а не сокращением фасада
FACADE_DIR = dict(zip(COMPASS_8, COMPASS_8_LONG))


def _rng(hotel_id: str, salt: str) -> random.Random:
    seed = int(hashlib.sha256(f"{hotel_id}:{salt}".encode()).hexdigest()[:12], 16)
    return random.Random(seed)


def _rv(session: Session, hotel_id: str, key: str) -> dict | None:
    rv = session.get(ResolvedValue, (hotel_id, key))
    if rv is None or not rv.value:
        return None
    return rv.value


def hidden_truth(session: Session, hotel: Hotel) -> dict:
    r = _rng(hotel.id, "truth")
    big = (hotel.building or {}).get("levels") or 4
    is_hotel = hotel.kind == "hotel"
    levels_known = (hotel.building or {}).get("levels")
    t = {
        "levels": levels_known if levels_known else r.choice([2, 3, 3, 4, 5]),
        "pets": {"allowed": r.random() < (0.35 if is_hotel else 0.5)},
        "curtains": r.choices(["all", "some", "none"], [0.5, 0.2, 0.3])[0],
        "windows": r.choices(["standard", "soundproof", "triple"], [0.6, 0.3, 0.1])[0],
        "desk": r.random() < (0.7 if is_hotel else 0.45),
        "wifi": r.choice([20, 30, 50, 60, 100, 150, 200]),
        "coworking": is_hotel and r.random() < 0.25,
        "crib": r.random() < 0.7,
        "kids_menu": is_hotel and r.random() < 0.5,
        "kids_club": is_hotel and big >= 5 and r.random() < 0.35,
        "elevator": big >= 5 or r.random() < 0.15,
        "wheelchair": r.choices(["yes", "limited", "no"], [0.25, 0.25, 0.5])[0],
        "own_parking": r.random() < 0.55,
        "parking_fee": r.choice([0, 0, 300, 500, 700]),
        "year": r.choice([None, None, 1936, 1952, 1968, 1975, 1987, 2004, 2012, 2016]),
        "exaggerates": r.random() < 0.35,
    }
    if t["pets"]["allowed"]:
        t["pets"].update({"animals": r.choice([["dog", "cat"], ["dog"], ["cat"], ["dog", "cat", "bird"]]),
                          "max_weight": r.choice([5, 7, 10, 15, 25]), "fee": r.choice([0, 500, 1000, 1500]),
                          "unit": r.choice(["night", "stay"])})
    return t


def _pick(r: random.Random, *variants: str) -> str:
    return r.choice(variants)


def rule_answer(session: Session, hotel: Hotel, key: str, question: Question) -> str:
    t = hidden_truth(session, hotel)
    r = _rng(hotel.id, f"answer:{key}:{question.id}")
    if key == "pets_policy":
        p = t["pets"]
        if not p["allowed"]:
            return _pick(r, "К сожалению, с животными не размещаем.", "Нет, с питомцами нельзя.",
                         "Животных не принимаем, извините.")
        names = {"dog": "собак", "cat": "кошек", "bird": "птиц"}
        animals = " и ".join(names[a] for a in p["animals"])
        fee = "бесплатно" if p["fee"] == 0 else f"доплата {p['fee']} руб {'в сутки' if p['unit'] == 'night' else 'за весь период проживания'}"
        return _pick(r, f"Да, принимаем {animals} весом до {p['max_weight']} кг, {fee}.",
                     f"Можно! Берём {animals}, но не более {p['max_weight']} кг. {fee.capitalize()}.",
                     f"Размещаем {animals} до {p['max_weight']} килограмм, {fee}. Нужен ветпаспорт.")
    if key == "blackout_curtains":
        return {"all": _pick(r, "Да, во всех номерах плотные блэкаут-шторы.", "Есть, блэкаут во всех номерах."),
                "some": _pick(r, "Блэкаут-шторы есть только в номерах категории люкс.", "В части номеров есть плотные шторы, уточняйте при бронировании."),
                "none": _pick(r, "Нет, обычные шторы, но есть жалюзи.", "Плотных штор нет.")}[t["curtains"]]
    if key == "soundproofing":
        return {"standard": _pick(r, "Стоят обычные пластиковые стеклопакеты.", "Обычные стеклопакеты, специальной шумоизоляции нет."),
                "soundproof": _pick(r, "Окна шумозащитные, со стороны проспекта поставили шумоизоляцию.", "Да, у нас звукоизоляция окон."),
                "triple": _pick(r, "Тройные стеклопакеты во всех номерах.", "Окна с тройным стеклопакетом.")}[t["windows"]]
    if key == "workspace":
        desk = "в номерах есть рабочий стол" if t["desk"] else "отдельного рабочего стола в номерах нет"
        cw = "есть лаунж-зона для работы" if t["coworking"] else "коворкинга нет"
        return f"{desk.capitalize()}, Wi-Fi около {t['wifi']} Мбит/с, {cw}."
    if key == "kids_facilities":
        parts = ["детскую кроватку предоставим бесплатно" if t["crib"] else "детских кроваток нет",
                 "детское меню есть" if t["kids_menu"] else "детского меню нет",
                 "есть детский клуб с аниматорами" if t["kids_club"] else "детского клуба нет"]
        return (", ".join(parts))[:1].upper() + (", ".join(parts))[1:] + "."
    if key == "elevator":
        return "Да, лифт есть." if t["elevator"] else _pick(r, "Лифта нет.", "Нет, лифта нет, но поможем с багажом.")
    if key == "accessibility":
        return {"yes": "Есть пандус и номер для гостей с инвалидностью.", "limited": "Частично: пандус есть, но в номера только по лестнице.",
                "no": "К сожалению, отель не приспособлен для колясок, на входе ступеньки."}[t["wheelchair"]]
    if key == "car_access":
        if not t["own_parking"]:
            return "Своей парковки нет, рядом городская платная. Подъехать к входу можно, чтобы выгрузить багаж."
        fee = "бесплатно" if t["parking_fee"] == 0 else f"{t['parking_fee']} рублей в сутки"
        return f"Своя парковка есть, {fee}. Подъехать к входу можно."
    if key == "building_levels":
        lv = f"{t['levels']} {plural(t['levels'], 'этаж', 'этажа', 'этажей')}"
        return _pick(r, f"В здании {lv}.", f"У нас {lv}, номера на всех этажах кроме первого.")
    if key == "building_history":
        if t["year"]:
            return f"Здание построено в {t['year']} году." + (" Это памятник архитектуры." if t["year"] < 1960 and r.random() < 0.5 else "")
        return "Точный год постройки не знаем, здание реконструировали в 2015 году."
    if key == "room_orientation":
        sea = _rv(session, hotel.id, "sea_view") or {}
        best = sea.get("best_facade")
        noise = _rv(session, hotel.id, "quietness") or {}
        quiet = noise.get("quiet_side")
        parts = []
        if best:
            mf = sea.get("min_floor") or 2
            top = max(mf, (hotel.building or {}).get("levels") or mf)
            floors = f"{mf}–{top} этажи" if top > mf else f"с {mf} этажа"
            parts.append(f"Делюкс с видом на море — окна на {FACADE_DIR.get(best, best)}, {floors}")
        side = f" (окна на {FACADE_DIR.get(quiet, quiet)})" if quiet and quiet != best else ""
        parts.append(f"Стандарт — во двор{side}")
        parts.append("Семейный — на улицу")
        return "; ".join(parts) + "."
    if key in {"sea_view", "sunset_view", "mountain_view"}:
        v = _rv(session, hotel.id, key) or {}
        noun = {"sea_view": "море", "sunset_view": "закат", "mountain_view": "горы"}[key]
        if question.reason == "conflict" and v.get("available") and r.random() < 0.7:
            return _pick(r, f"Да, вы правы: {noun} видно только начиная с {v.get('min_floor') or 2} этажа.",
                         f"Уточняю: {noun} видно с {v.get('min_floor') or 2} этажа, ниже закрывают соседние дома.")
        if t["exaggerates"]:
            return _pick(r, f"Конечно, {noun} видно из всех номеров!", f"Да, из всех номеров отличный вид на {noun}.")
        if not v.get("available"):
            return f"Нет, {noun} из номеров не видно."
        return f"Да, {noun} видно начиная с {v.get('min_floor') or 2} этажа, окна на {FACADE_DIR.get(v.get('best_facade'), 'море')}."
    if key == "construction_nearby":
        return "Рядом стройки нет." if r.random() < 0.7 else "Да, через дорогу строят жилой дом, днём бывает шумно."
    return _pick(r, "Да, есть.", "Нет, такого нет.", "Да.")


def simulate_answer(session: Session, question: Question) -> str:
    hotel = session.get(Hotel, question.hotel_id)
    key = (question.char_keys or ["?"])[0]
    base = rule_answer(session, hotel, key, question)
    if llm.enabled():
        res = llm.chat_json(
            "Ты отельер небольшой гостиницы в Сочи. Перепиши ответ гостю живым разговорным языком, сохранив ВСЕ факты "
            "без изменений и не добавляя новых. Верни JSON {\"answer\": \"...\"}.",
            json.dumps({"question": question.text, "facts_answer": base}, ensure_ascii=False), max_tokens=300,
            temperature=0.7)
        if res and isinstance(res.get("answer"), str) and len(res["answer"]) > 5:
            return res["answer"]
    return base
