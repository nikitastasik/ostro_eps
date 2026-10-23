"""Пожелание к бронированию: готовый текст для отеля из совета консьержа и пожеланий гостя.

Текст строится детерминированно из той же оценки, что в поиске и карточке (concierge.fit): сторона и этаж —
из совета по номеру, причины — из выполненных пожеланий по виду и тишине, всё, чего система не знает,
превращается в вопрос к отелю. С ключом LLM формулировки можно отшлифовать, но факты сверяются, и при
расхождении остаётся текст правил.
"""

from __future__ import annotations

import json
import re

from sqlalchemy.orm import Session

from .. import llm
from ..geo.analysis import facade_acc
from ..knowledge.registry import all_specs
from . import concierge

REASON = {
    "sea_view": "видно море",
    "mountain_view": "видны горы",
    "landmark_view": "видны городские ориентиры",
    "window_outlook": "открытый вид без стены напротив",
}
# просьба, если отель это умеет (по данным), и вопрос, если неизвестно
ASK = {
    "quietness": ("Важна тишина ночью.", "Насколько тихо ночью в номерах с этой стороны?"),
    "blackout_curtains": ("Прошу номер с плотными шторами.", None),
    "workspace": ("Нужен рабочий стол и стабильный Wi‑Fi.", None),
    "pets_policy": ("Едем с питомцем — по вашим данным, это возможно.", None),
    "kids_facilities": ("Едем с ребёнком — пригодится детская кроватка.", None),
    "accessibility": ("Нужен номер, доступный для гостя на коляске.", None),
    "elevator": ("Важен лифт.", None),
    "soundproofing": ("Важна шумоизоляция окон.", None),
}


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " и " + items[-1]


def _question(spec: dict) -> str:
    q = ((spec.get("question") or {}).get("text") or "").split("?")[0].strip()
    q = q[:1].lower() + q[1:] if q else f"как у вас с этим: {spec['title'].lower()}"
    return f"Подскажите, пожалуйста: {q}?"


def _room_phrase(room: dict) -> str:
    s = "номер"
    if room.get("category"):
        s += f" категории «{room['category']}»"
    s += f" с окнами на {facade_acc(room['facade'])} сторону"
    if room.get("min_floor"):
        s += f" не ниже {room['min_floor']}-го этажа"
    return s


def build(session: Session, hotel_id: str, text: str) -> dict | None:
    f = concierge.fit(session, hotel_id, text)
    if f is None:
        return None
    r, specs = f["result"], all_specs(session)
    needs, room = r["needs"], r["room"]
    ok = [n for n in needs if n["status"] in {"match", "conditional"}]
    seen = [REASON[n["key"]] for n in ok if n["key"] in REASON]
    for n in ok:
        if n["key"] == "sunset_view":
            seen.append("виден закат над морем" if n.get("over_sea") else "виден закат")
    parts = ["Здравствуйте!"]
    if room:
        ask = "Прошу, если возможно, " + _room_phrase(room)
        if r["hotel"].get("corpus"):
            ask += f" ({r['hotel']['corpus']})"
        parts.append(ask + (f" — по нашим данным, оттуда {_join(seen)}." if seen else "."))
    questions = []
    for n in needs:
        known, unknown_q = ASK.get(n["key"], (None, None))
        if n["status"] in {"match", "conditional"} and known:
            parts.append(known)
        elif n["status"] == "unknown" and n["key"] in specs:
            questions.append(unknown_q or _question(specs[n["key"]]))
    parts += questions
    parts.append("Спасибо!")
    text_rules = " ".join(parts)
    method, out = "rules", text_rules
    if llm.enabled():
        polished = _polish(text_rules, room)
        if polished:
            method, out = "llm", polished
    reasons = [
        {"title": n["title"], "status": n["status"], "text": n["text"], "sources": n["sources"], "confidence": n["confidence"]}
        for n in needs
        if n["status"] != "unknown"
    ]
    return {
        "hotel": r["hotel"],
        "query": f["query"]["text"],
        "text": out,
        "text_rules": text_rules,
        "method": method,
        "reasons": reasons,
        "questions": questions,
        "room": room,
        "share_text": (room or {}).get("share_text"),
    }


def _polish(text: str, room: dict | None) -> str | None:
    """LLM шлифует стиль. Факты (сторона, этаж, вопросы) обязаны сохраниться, иначе остаётся текст правил."""
    res = llm.chat_json(
        "Ты помогаешь гостю написать отелю короткое вежливое пожелание к бронированию. Перепиши текст живым языком, "
        "сохранив все факты: сторону здания, этаж, просьбы и вопросы. Ничего не добавляй от себя. "
        'Ответ — JSON {"text": "..."}.',
        json.dumps({"text": text}, ensure_ascii=False),
        max_tokens=500,
    )
    out = (res or {}).get("text")
    if not isinstance(out, str) or not out.strip():
        return None
    must = []
    if room:
        must.append(facade_acc(room["facade"])[:5])
        if room.get("min_floor"):
            must.append(str(room["min_floor"]))
    if any(m.lower() not in out.lower() for m in must) or out.count("?") < text.count("?"):
        return None
    return re.sub(r"\s+", " ", out).strip()
