"""Разбор свободного ответа отельера по полям характеристики (правила).

Если подключена LLM, сначала пробуем её (см. agents/interviewer.py), а правила
остаются запасным вариантом и эталоном для сравнения на размеченной выборке.
"""

from __future__ import annotations

import re

from .ru import FLOOR_COMPOUND, find_phrase, norm, ordinal_value, phrase_tokens, split_clauses, tokens

FIELD_KEYWORDS: dict[tuple[str, str], list[str]] = {
    ("kids_facilities", "crib"): ["кроват", "люльк", "манеж"],
    ("kids_facilities", "kids_menu"): ["меню", "детское питан"],
    ("kids_facilities", "kids_club"): ["клуб", "игров", "аниматор", "детская комнат"],
    ("workspace", "desk"): ["стол", "рабочее мест", "рабочая зон"],
    ("workspace", "coworking"): ["коворкинг", "лаунж", "переговорн", "бизнес-центр", "бизнес центр"],
    ("car_access", "own_parking"): ["парковк", "стоянк", "паркинг", "парковочн"],
    ("car_access", "car_to_door"): ["подъехать", "подъезд", "ко входу", "к входу", "высадить", "к крыльцу"],
    ("blackout_curtains", "available"): ["штор", "блэкаут", "blackout", "затемн", "жалюзи", "рольставн"],
    ("elevator", "available"): ["лифт"],
    ("building_history", "heritage"): ["памятник", "наследи", "охраня"],
    ("sea_view", "available"): ["мор"],
    ("sunset_view", "available"): ["закат"],
    ("mountain_view", "available"): ["гор"],
    ("construction_nearby", "sites_300m"): ["строй", "строительств"],
}

POSITIVE = [
    "да",
    "конечно",
    "разумеется",
    "есть",
    "можно",
    "разрешено",
    "разрешаем",
    "принимаем",
    "размещаем",
    "допускается",
    "рады",
    "welcome",
    "friendly",
    "петфрендли",
    "имеется",
    "предоставляем",
    "предусмотрен",
    "оборудован",
    "установлен",
    "виден",
    "видно",
    "открывается",
]
NEGATIVE_RE = re.compile(r"\b(нет|не|ни|без|отсутству\w*|нельзя|запрещ\w*|невозможно|увы|к сожалению)\b")
QUANTIFIER_RE = re.compile(r"\bне\s+(более|больше|дороже|выше|ниже|менее|меньше|тяжелее|старше|далее|дальше)\b")

COMPASS = [
    ("северо-восток", "СВ"),
    ("северо-запад", "СЗ"),
    ("юго-восток", "ЮВ"),
    ("юго-запад", "ЮЗ"),
    ("север", "С"),
    ("восток", "В"),
    ("юг", "Ю"),
    ("запад", "З"),
]
SIDE_WORDS = [
    ("мор", "sea"),
    ("двор", "courtyard"),
    ("улиц", "street"),
    ("проспект", "street"),
    ("дорог", "street"),
    ("гор", "mountains"),
    ("парк", "park"),
    ("сад", "park"),
    ("город", "city"),
    ("бассейн", "pool"),
]
ANIMALS = [
    ("собак", "dog"),
    ("собач", "dog"),
    ("пес", "dog"),
    ("псов", "dog"),
    ("щен", "dog"),
    ("кош", "cat"),
    ("кот", "cat"),
    ("птиц", "bird"),
    ("попуга", "bird"),
    ("грызун", "rodent"),
    ("хомя", "rodent"),
    ("кролик", "rodent"),
    ("шиншилл", "rodent"),
]
STYLES = [
    "модерн",
    "ампир",
    "конструктивизм",
    "сталинск",
    "классицизм",
    "неоклассицизм",
    "барокко",
    "эклектик",
    "модернизм",
    "хай-тек",
    "готик",
]


def _polarity(text: str) -> bool | None:
    t = QUANTIFIER_RE.sub(" ", norm(text))
    neg = bool(NEGATIVE_RE.search(t))
    toks = tokens(t)
    pos = any(find_phrase(toks, phrase_tokens(p)) is not None for p in POSITIVE)
    if neg and not re.search(r"\bне только\b", t):
        return False
    if pos:
        return True
    return None


def global_polarity(text: str) -> bool | None:
    t = norm(text)
    first = split_clauses(t)[0] if split_clauses(t) else t
    if re.match(r"^(нет|к сожалению|увы|нельзя|не\s)", first):
        return False
    if re.match(r"^(да|конечно|разумеется|можно|есть|принимаем|разрешено)", first):
        return True
    return _polarity(first)


def money(text: str) -> float | None:
    t = norm(text)
    if re.search(r"\bбесплатн|без доплат|без оплат|ничего не стоит|не берем|не берём|доплаты нет|доплат[аы]? не|оплаты нет", t):
        return 0.0
    m = re.search(r"(\d[\d\s]{0,7}(?:[.,]\d+)?)\s*(?:₽|руб|р\b|р\.|рублей|rub)", t)
    if m:
        return float(m.group(1).replace(" ", "").replace(",", "."))
    m = re.search(r"(\d+)\s*(?:тыс|т\.р)", t)
    if m:
        return float(m.group(1)) * 1000
    return None


def min_floor(text: str) -> int | None:
    t = norm(text)
    if re.search(r"(со|с|из) (всех|любого|любых)\s+(этаж|номер)|все номера|из всех номеров|во всех номерах", t):
        return 1
    m = re.search(r"начиная с\s+(\d+|[а-я]+)", t)
    if m:
        v = ordinal_value(m.group(1))
        if v:
            return v
    m = re.search(r"(?:с|со|начиная с|от)\s+(\d+|[а-я]+)[-\s]*(?:го|ого|его)?\s*этаж", t)
    if m:
        v = ordinal_value(m.group(1))
        if v:
            return v
    m = re.search(r"(\d+)\s*[-–]\s*(\d+)\s*этаж", t)
    if m:
        return int(m.group(1))
    m = re.search(r"(?:выше|выше чем)\s+(\d+|[а-я]+)", t)
    if m:
        v = ordinal_value(m.group(1))
        if v:
            return v + 1
    m = re.search(r"\b(\d+|[а-я]+)\s+этаж", t)
    if m:
        v = ordinal_value(m.group(1))
        if v and v < 40:
            return v
    return None


def levels(text: str) -> int | None:
    t = norm(text)
    for st, n in FLOOR_COMPOUND.items():
        if st in t:
            return n
    m = re.search(r"(\d+)\s*(?:-?\s*)?этаж", t)
    if m:
        return int(m.group(1))
    m = re.search(r"\b([а-я]+)\s+этаж", t)
    if m:
        v = ordinal_value(m.group(1))
        if v:
            return v
    return None


def facade(text: str) -> str | None:
    t = norm(text)
    for word, code in COMPASS:
        if word in t:
            return code
    return None


def side(text: str) -> str | None:
    toks = tokens(text)
    for st, code in SIDE_WORDS:
        if any(tok.startswith(st) for tok in toks):
            return code
    return None


def room_categories(text: str) -> list[dict]:
    out = []
    for part in re.split(r"[;\n]|(?<=[а-я0-9)])\.\s+", text):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^\s*[«\"]?([A-Za-zА-Яа-яЁё0-9\s\-+]+?)[»\"]?\s*(?:[-–—:]|выходят|смотрят|окна)\s*(.*)$", part)
        if not m:
            continue
        name = m.group(1).strip(" -")
        rest = m.group(2)
        if len(name) < 3 or len(name) > 40:
            continue
        cat = {"name": name, "side": side(rest), "facade": facade(rest)}
        fl = re.search(r"(\d+)\s*[-–]\s*(\d+)\s*этаж", norm(rest))
        if fl:
            cat["floors"] = [int(fl.group(1)), int(fl.group(2))]
        elif min_floor(rest):
            cat["floors"] = [min_floor(rest), None]
        if cat["side"] or cat["facade"]:
            out.append(cat)
    return out


def parse_field(char_key: str, field: str, ftype: dict, answer: str, clauses: list[str]) -> object | None:
    t = norm(answer)
    typ = ftype.get("type", "text")
    # специальные поля
    if field in {"fee_rub", "parking_fee_rub"}:
        return money(answer)
    if field == "max_weight_kg":
        m = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:кг|килограм)", t)
        return float(m.group(1).replace(",", ".")) if m else None
    if field == "wifi_mbps":
        m = re.search(r"(\d+)\s*(?:мбит|mbit|mbps|мб/с|мегабит|мб в сек)", t)
        return float(m.group(1)) if m else None
    if field == "levels":
        return levels(answer)
    if field == "min_floor":
        return min_floor(answer)
    if field == "year_built":
        m = re.search(r"\b(1[7-9]\d{2}|20[0-2]\d)\b", t)
        return int(m.group(1)) if m else None
    if field in {"minutes_to", "station_minutes"}:
        m = re.search(r"(\d+)\s*мин", t)
        return float(m.group(1)) if m else None
    if field == "reduction_db":
        m = re.search(r"(\d+)\s*(?:дб|децибел)", t)
        return float(m.group(1)) if m else None
    if field == "fee_unit":
        if re.search(r"(в|за) (сутки|ночь|день)|посуточн|в сут|/\s*сут|/\s*ноч|сутк", t):
            return "night"
        if re.search(r"за (весь|все|проживание|пребывание)|единоразов|разово|за период", t):
            return "stay"
        return None
    if field == "animals":
        found = []
        toks = tokens(answer)
        for st, code in ANIMALS:
            if any(tok.startswith(st) for tok in toks) and code not in found:
                found.append(code)
        return found or None
    if field == "windows":
        if re.search(r"тройн|трехкамер|трёхкамер", t):
            return "triple"
        if re.search(r"шумоизол|шумозащит|звукоизол|звуконепрониц", t):
            hit = next((c for c in clauses if re.search(r"шумо|звуко", c)), t)
            neg = re.search(r"(нет|без|не)\s+\w*\s*(шумо|звуко)", t) or _polarity(hit) is False
            return "standard" if neg else "soundproof"
        if re.search(r"обычн|стандартн|стеклопакет|пластиков", t):
            return "standard"
        return None
    if field == "wheelchair":
        if re.search(r"частичн|ограничен|не везде|только (на )?первом", t):
            return "limited"
        if re.search(r"(нет|без|отсутств)\s+\w*\s*(пандус|лифт)|не приспособлен|недоступ|только по лестниц|ступеньк", t):
            return "no"
        if re.search(r"пандус|доступн|без порог|без ступен|адаптирован|приспособлен|номер для (людей|гостей) с инвалид", t):
            return "yes"
        return None
    if field == "rooms":
        if re.search(r"во всех|все номера|в каждом", t):
            return "all"
        if re.search(r"в част|в некоторых|только в|не во всех|в номерах категори", t):
            return "some"
        return None
    if field in {"best_facade", "quiet_side"}:
        return facade(answer) or side(answer)
    if field == "style":
        for st in STYLES:
            if st in t:
                m = re.search(rf"([а-я]*\s*{st}[а-я]*)", t)
                return m.group(1).strip() if m else st
        return None
    if field == "categories":
        cats = room_categories(answer)
        return cats or None
    if field == "sites_300m":
        pol = _polarity(answer)
        if pol is None:
            return None
        return 1 if pol else 0
    if typ == "bool":
        kws = FIELD_KEYWORDS.get((char_key, field)) or ftype.get("keywords")
        if kws:
            hits = [c for c in clauses if any(k.strip() in c for k in kws)]
            if not hits:
                return None
            pol = _polarity(hits[0])
            if pol is None:
                # «Нет, только лёгкие шторы»: отрицание в начале ответа относится к вопросу
                g = global_polarity(answer)
                pol = g if g is False and hits[0] != clauses[0] else None
            # упоминание без отрицания («кроватку дадим», «памятник архитектуры») — это «да»
            return True if pol is None else pol
        return None  # основное поле без ключевых слов — см. глобальную полярность
    if typ in {"int", "number"}:
        m = re.search(r"(\d+(?:[.,]\d+)?)", t)
        return float(m.group(1).replace(",", ".")) if m else None
    if typ == "enum":
        for v in ftype.get("values", []):
            if str(v).lower() in t:
                return v
        return None
    if typ == "text":
        return answer.strip()[:300] or None
    return None


def parse_answer(spec: dict, answer: str, fields: list[str] | None = None) -> dict:
    """Возвращает {"fields": {...}, "missing": [...], "confidence": 0..1}."""
    vf = {**spec.get("value_fields", {}), **spec.get("extra_fields", {})}
    q = spec.get("question") or {}
    wanted = fields or q.get("fields") or list(vf.keys())
    clauses = split_clauses(answer)
    out: dict = {}
    for f in wanted:
        if f not in vf:
            continue
        v = parse_field(spec["key"], f, vf[f], answer, clauses)
        if v is not None:
            out[f] = v
    # основное логическое поле — по общей тональности ответа
    primary = next(
        (
            f
            for f in wanted
            if vf.get(f, {}).get("type") == "bool" and (spec["key"], f) not in FIELD_KEYWORDS and not vf[f].get("keywords")
        ),
        None,
    )
    if primary and primary not in out:
        pol = global_polarity(answer)
        if pol is not None:
            out[primary] = pol
    # перечислены условия (какие животные, вес) без отрицания — значит, разрешено
    if primary == "allowed" and "allowed" not in out and ("animals" in out or "max_weight_kg" in out):
        out["allowed"] = True
    # согласованность: если запрещено — остальные поля не важны
    if out.get("allowed") is False:
        out = {"allowed": False}
    if spec["key"] == "sea_view" and "min_floor" in out and "available" not in out:
        out["available"] = True
    if spec["key"] == "sunset_view" and "min_floor" in out and "available" not in out:
        out["available"] = True
    if spec["key"] == "soundproofing" and "windows" in out:
        out["reduction_db"] = {"standard": 25, "soundproof": 32, "triple": 36}[out["windows"]]
    missing = [f for f in wanted if f in vf and f not in out]
    conf = len([f for f in wanted if f in out]) / max(1, len([f for f in wanted if f in vf]))
    return {"fields": out, "missing": missing, "confidence": round(conf, 2)}
