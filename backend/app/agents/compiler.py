"""Агент-компилятор: превращает текстовое описание новой характеристики в спецификацию.

Пример: «есть ли круглосуточная аптека в 10 минутах пешком» →
  recipe: osm.poi_walk {categories: [pharmacy], max_minutes: 10, filter: 24_7}.
Если в открытых данных такой информации нет (например, «есть ли в номере чайник»),
характеристика становится вопросом отельеру (hotelier.ask).
С LLM компилятор понимает свободные формулировки; без неё работают правила.
"""

from __future__ import annotations

import json
import re

from .. import llm
from ..knowledge.categories import AREA_CATEGORIES, POI_CATEGORIES, title
from ..knowledge.registry import normalize
from ..nlp.ru import find_phrase, norm, number_before, phrase_tokens, tokens
from ..tools.base import load_all

GENERIC_TOOLS = ["osm.poi_nearby", "osm.poi_walk", "osm.area_nearby", "geo.target_view", "climate.month_metric",
                 "osm.hotel_tag", "hotelier.ask"]
STOP = {"есть", "ли", "в", "на", "и", "или", "с", "у", "для", "по", "от", "до", "рядом", "отеле", "отель", "номере",
        "номер", "номерах", "гостинице", "гостиница", "можно", "чтобы", "где", "как", "какой", "какая", "сколько",
        "минут", "минутах", "пешком", "метрах", "метров", "км", "пределах", "радиусе", "ближайший", "ближайшая",
        "ближайшее", "должен", "должна", "должно", "быть", "чтоб", "хочу", "нужен", "нужна", "нужно"}
TRANSLIT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t",
                     "u", "f", "h", "c", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"]))


def slug(text: str) -> str:
    t = "".join(TRANSLIT.get(ch, ch) for ch in norm(text))
    t = re.sub(r"[^a-z0-9]+", "_", t).strip("_")
    return t[:40] or "custom"


def _detect_categories(text: str) -> tuple[list[str], list[str]]:
    toks = tokens(text)
    poi, area = [], []
    for cat, (_, _, stems) in POI_CATEGORIES.items():
        if any(find_phrase(toks, phrase_tokens(s.strip())) is not None for s in stems):
            poi.append(cat)
    for cat, (_, _, stems) in AREA_CATEGORIES.items():
        if any(find_phrase(toks, phrase_tokens(s.strip())) is not None for s in stems):
            area.append(cat)
    # уточнения перекрывают общие категории
    if "kids_hospital" in poi and "hospital" in poi:
        poi.remove("hospital")
    if "nightclub" in poi and "bar" in poi and "бар" not in norm(text).split():
        poi.remove("bar")
    if "atm" in poi and "bank" in poi and not re.search(r"\bбанк(а|у|ом|и|ов)?\b", norm(text)):
        poi.remove("bank")
    if "dog_park" in poi and "park" in area:
        area.remove("park")
    if "playground" in poi and "playground" in area:
        area.remove("playground")
    return poi, area


def _phrases(text: str) -> list[str]:
    words = [w for w in re.findall(r"[а-яa-z]+", norm(text)) if w not in STOP and len(w) >= 4]
    out = []
    for i in range(len(words)):
        out.append(words[i])
        if i + 1 < len(words):
            out.append(f"{words[i]} {words[i + 1]}")
    return out[:8]


def compile_rules(text: str) -> dict:
    t = norm(text)
    poi, area = _detect_categories(text)
    flt = "24_7" if re.search(r"круглосуточн|24/7|24 часа", t) else "night" if re.search(r"ночью|ночн|после полуночи|поздно", t) else None
    minutes = number_before(t, r"мин\w*")
    meters = number_before(t, r"м\b|метр\w*")
    km = number_before(t, r"км\b|километр\w*")
    radius = meters or (km * 1000 if km else None)
    if radius is None:
        radius = 300 if re.search(r"рядом|под боком|в двух шагах", t) else 500 if "шагов" in t else 700
    cats = poi + area
    base_title = text.strip().rstrip("?").strip()
    base_title = base_title[0].upper() + base_title[1:] if base_title else "Новая характеристика"
    if len(base_title) > 70:
        base_title = base_title[:67] + "…"
    if cats:
        cat_names = ", ".join(title(c) for c in cats)
        if poi and (minutes or "пешк" in t):
            recipe = [{"tool": "osm.poi_walk", "params": {"categories": poi, "max_minutes": minutes or 10, "filter": flt}}]
            fields = {"available": {"type": "bool", "title": f"{cat_names.capitalize()} в пешей доступности"},
                      "minutes_to": {"type": "number", "title": "Минут пешком"},
                      "walk_m": {"type": "number", "title": "Метров пешком"}}
            descr = f"Пеший путь до ближайшего объекта ({cat_names}) не дольше {minutes or 10:g} мин"
        elif poi:
            recipe = [{"tool": "osm.poi_nearby", "params": {"categories": poi, "radius_m": radius, "filter": flt}}]
            fields = {"available": {"type": "bool", "title": f"{cat_names.capitalize()} рядом"},
                      "count": {"type": "int", "title": "Сколько в радиусе"},
                      "nearest_m": {"type": "number", "title": "До ближайшего, м"}}
            descr = f"Объекты «{cat_names}» в радиусе {radius:g} м по данным OSM"
        else:
            recipe = [{"tool": "osm.area_nearby", "params": {"categories": area, "radius_m": radius}}]
            fields = {"available": {"type": "bool", "title": f"{cat_names.capitalize()} рядом"},
                      "count": {"type": "int", "title": "Сколько в радиусе"},
                      "nearest_m": {"type": "number", "title": "До ближайшего, м"}}
            descr = f"Площадные объекты «{cat_names}» в радиусе {radius:g} м по данным OSM"
        if flt:
            descr += ", " + ("только круглосуточные" if flt == "24_7" else "открытые ночью")
        spec = {"title": base_title, "description": descr, "scope": "hotel", "group": "custom", "icon": "pin",
                "value_fields": fields, "recipe": recipe, "ttl_days": 90, "confidence_threshold": 0.35,
                "conflict": {"fields": [], "authority": ["osm"], "policy": "computed_wins"},
                "search": {"phrases": _phrases(text), "criteria": [{"field": "available", "op": "eq", "value": True}]}}
        return spec
    q = text.strip().rstrip("?")
    if re.match(r"(?i)^(есть ли|можно ли|какой|какая|какие|сколько|как|где|предоставляете|принимаете)", q):
        q = q[0].upper() + q[1:] + "?"
    else:
        q = f"Есть ли у вас: {q[0].lower() + q[1:]}?"
    spec = {"title": base_title, "description": "Этой информации нет в открытых данных — агент спрашивает отельера.",
            "scope": "hotel", "group": "custom", "icon": "chat",
            "value_fields": {"available": {"type": "bool", "title": base_title}},
            "recipe": [{"tool": "hotelier.ask", "when": "always_once"}], "ttl_days": 365, "confidence_threshold": 0.5,
            "conflict": {"fields": [{"field": "available", "type": "bool"}], "authority": ["hotelier_confirmed", "hotelier"],
                         "policy": "priority"},
            "question": {"text": q, "fields": ["available"]},
            "search": {"phrases": _phrases(text), "criteria": [{"field": "available", "op": "eq", "value": True}]}}
    return spec


def _validate(spec: dict) -> list[str]:
    errs = []
    tools = load_all()
    for step in spec.get("recipe", []):
        if step.get("tool") not in GENERIC_TOOLS or step.get("tool") not in tools:
            errs.append(f"инструмент {step.get('tool')} недоступен для новых характеристик")
        cats = (step.get("params") or {}).get("categories") or []
        for c in cats:
            if c not in POI_CATEGORIES and c not in AREA_CATEGORIES:
                errs.append(f"неизвестная категория {c}")
    fields = spec.get("value_fields") or {}
    if not fields:
        errs.append("нет полей значения")
    for c in (spec.get("search") or {}).get("criteria", []):
        if c.get("field") not in fields:
            errs.append(f"критерий поиска по неизвестному полю {c.get('field')}")
    if not spec.get("title"):
        errs.append("нет названия")
    return errs


def compile_llm(text: str) -> dict | None:
    if not llm.enabled():
        return None
    tools = load_all()
    catalog = [tools[t].describe() for t in GENERIC_TOOLS if t in tools]
    cats = {c: v[0] for c, v in {**POI_CATEGORIES, **AREA_CATEGORIES}.items()}
    system = (
        "Ты агент-компилятор характеристик отелей. По текстовому описанию составь спецификацию характеристики, "
        "которую агенты смогут вычислять для каждого отеля. Используй ТОЛЬКО инструменты из каталога и категории из "
        "справочника. Если ответ нельзя получить из открытых данных, используй hotelier.ask (вопрос отельеру) с "
        "when=always_once и полем question. Верни JSON: {\"title\", \"description\", \"scope\": \"hotel\", "
        "\"value_fields\": {имя: {\"type\": bool|int|number|text, \"title\"}}, \"recipe\": [{\"tool\", \"params\", \"when\"?}], "
        "\"question\"?: {\"text\", \"fields\"}, \"search\": {\"phrases\": [...], \"criteria\": [{\"field\", \"op\", \"value\"}]}, "
        "\"ttl_days\"}. Поля value_fields должны совпадать с тем, что возвращает инструмент: osm.poi_nearby и "
        "osm.area_nearby — available, count, nearest_m; osm.poi_walk — available, minutes_to, walk_m; "
        "geo.target_view — available, min_floor; hotelier.ask — любые поля из question.fields."
    )
    res = llm.chat_json(system, json.dumps({"text": text, "tools": catalog, "categories": cats}, ensure_ascii=False),
                        max_tokens=900)
    if not res or not isinstance(res, dict):
        return None
    res.setdefault("group", "custom")
    res.setdefault("icon", "pin")
    res.setdefault("conflict", {"fields": [], "authority": ["hotelier_confirmed", "hotelier", "osm", "geo"],
                                "policy": "priority"})
    if _validate(res):
        return None
    return res


def compile_text(text: str, existing_keys: set[str]) -> tuple[dict, str, list[str]]:
    spec = compile_llm(text)
    method = "llm"
    if spec is None:
        spec = compile_rules(text)
        method = "rules"
    errs = _validate(spec)
    key = "c_" + slug(spec.get("title") or text)
    base, i = key, 2
    while key in existing_keys:
        key = f"{base}_{i}"
        i += 1
    spec["key"] = key
    return normalize(spec), method, errs
