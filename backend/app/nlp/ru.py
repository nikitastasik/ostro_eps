"""Утилиты разбора русского текста: нормализация, стемминг, поиск фраз, числа, месяцы."""

from __future__ import annotations

import re
from functools import lru_cache

import snowballstemmer

_stemmer = snowballstemmer.stemmer("russian")
SHORT_WORDS = {"на", "с", "со", "у", "до", "в", "во", "к", "ко", "без", "не", "по", "от", "из", "для", "за", "и", "а",
               "о", "об", "при", "над", "под", "нет", "да", "же", "ли", "то"}

WORD_RE = re.compile(r"[a-zа-я0-9]+")

MONTH_STEMS = [("январ", 1), ("феврал", 2), ("март", 3), ("апрел", 4), ("ма", 5), ("июн", 6), ("июл", 7),
               ("август", 8), ("сентябр", 9), ("октябр", 10), ("ноябр", 11), ("декабр", 12)]
MONTH_RE = re.compile(r"\b(январ|феврал|март|апрел|ма[йяюе]|июн|июл|август|сентябр|октябр|ноябр|декабр)[а-я]*\b")
SEASONS = {"лет": [6, 7, 8], "осен": [9, 10, 11], "зим": [12, 1, 2], "весн": [3, 4, 5], "бархатн": [9, 10]}

ORDINALS = {
    "перв": 1, "втор": 2, "трет": 3, "четверт": 4, "пят": 5, "шест": 6, "седьм": 7, "восьм": 8, "девят": 9,
    "десят": 10, "одиннадцат": 11, "двенадцат": 12, "тринадцат": 13, "четырнадцат": 14, "пятнадцат": 15,
    "шестнадцат": 16, "семнадцат": 17, "восемнадцат": 18, "девятнадцат": 19, "двадцат": 20,
}
CARDINALS = {
    "один": 1, "одна": 1, "одно": 1, "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5, "шесть": 6, "семь": 7,
    "восемь": 8, "девять": 9, "десять": 10, "одиннадцать": 11, "двенадцать": 12, "тринадцать": 13,
    "четырнадцать": 14, "пятнадцать": 15, "шестнадцать": 16, "семнадцать": 17, "восемнадцать": 18,
    "девятнадцать": 19, "двадцать": 20, "двадцати": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50, "сто": 100,
    "пяти": 5, "десяти": 10, "пятнадцати": 15, "трёх": 3, "трех": 3, "двух": 2, "четырёх": 4, "четырех": 4,
    "шести": 6, "семи": 7, "восьми": 8, "девяти": 9, "полчаса": 30,
}
FLOOR_COMPOUND = {  # «девятиэтажное», «пятиэтажка»
    "одноэтаж": 1, "двухэтаж": 2, "трехэтаж": 3, "четырехэтаж": 4, "пятиэтаж": 5, "шестиэтаж": 6, "семиэтаж": 7,
    "восьмиэтаж": 8, "девятиэтаж": 9, "десятиэтаж": 10, "двенадцатиэтаж": 12, "четырнадцатиэтаж": 14,
    "шестнадцатиэтаж": 16, "двадцатиэтаж": 20,
}


def norm(text: str) -> str:
    t = (text or "").lower().replace("ё", "е").replace("‑", "-").replace("–", "-").replace("—", "-")
    t = re.sub(r"[«»\"'`()\[\]{}]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


@lru_cache(maxsize=20000)
def stem(word: str) -> str:
    if word in SHORT_WORDS or word.isdigit():
        return word
    return _stemmer.stemWord(word)


def tokens(text: str) -> list[str]:
    return [stem(w) for w in WORD_RE.findall(norm(text))]


def phrase_tokens(phrase: str) -> list[str]:
    return [stem(w) if len(w) > 3 or w not in SHORT_WORDS else w for w in WORD_RE.findall(norm(phrase))]


def find_phrase(toks: list[str], ptoks: list[str], max_gap: int = 1) -> tuple[int, int] | None:
    """Ищет фразу (последовательность основ) в токенах с допуском пропусков. Возвращает (начало, конец)."""
    if not ptoks:
        return None

    def match(tok: str, p: str) -> bool:
        # короткие основы («мор», «гор», «тих») — только точное совпадение, иначе «морской» ≈ «море»
        if p in SHORT_WORDS or len(p) <= 3:
            return tok == p
        return tok.startswith(p)

    for i, tok in enumerate(toks):
        if not match(tok, ptoks[0]):
            continue
        j, pos = 1, i
        ok = True
        while j < len(ptoks):
            found = False
            for k in range(pos + 1, min(len(toks), pos + 2 + max_gap)):
                if match(toks[k], ptoks[j]):
                    pos = k
                    found = True
                    break
            if not found:
                ok = False
                break
            j += 1
        if ok:
            return i, pos
    return None


def split_clauses(text: str) -> list[str]:
    parts = re.split(r"[.;!?\n]+|,\s*(?=[а-яa-z])|\s+(?:но|а также|плюс|причем|причём)\s+", norm(text))
    return [p.strip() for p in parts if p and p.strip()]


def months_in(text: str) -> list[int]:
    t = norm(text)
    out: list[int] = []
    for m in MONTH_RE.finditer(t):
        st = m.group(1)
        for prefix, num in MONTH_STEMS:
            if st.startswith(prefix):
                if prefix == "ма" and not re.match(r"ма[йяюе]", st):
                    continue
                out.append(num)
                break
    rng = re.search(r"с\s+(\w+)\s+(?:по|до)\s+(\w+)", t)
    if rng and len(out) >= 2:
        a, b = out[0], out[1]
        out = list(range(a, b + 1)) if b >= a else list(range(a, 13)) + list(range(1, b + 1))
    for st, ms in SEASONS.items():
        if re.search(rf"\b{st}\w*", t) and not out:
            out.extend(ms)
    return sorted(set(out))


def number_before(text: str, unit_re: str) -> float | None:
    """Число перед единицей измерения: «10 минут», «1,5 км», «пять минут»."""
    t = norm(text)
    m = re.search(rf"(\d+(?:[.,]\d+)?)\s*(?:{unit_re})", t)
    if m:
        return float(m.group(1).replace(",", "."))
    m = re.search(rf"\b([а-я]+)\s+(?:{unit_re})", t)
    if m and m.group(1) in CARDINALS:
        return float(CARDINALS[m.group(1)])
    return None


def ordinal_value(word: str) -> int | None:
    w = norm(word)
    m = re.match(r"^(\d+)(?:-?[а-я]{1,3})?$", w)
    if m:
        return int(m.group(1))
    for st, n in sorted(ORDINALS.items(), key=lambda kv: -len(kv[0])):
        if w.startswith(st):
            return n
    return CARDINALS.get(w)


def has_any(text: str, stems: list[str]) -> bool:
    toks = tokens(text)
    for s in stems:
        if find_phrase(toks, phrase_tokens(s)) is not None:
            return True
    return False


# ---------------------------------------------------------------- фразы для пользователя: расстояния и время
NEAR_M = 40  # ближе этого объект «прямо у» отеля: точность контуров OSM всё равно около 10–20 м


def dec(x: float, digits: int = 1) -> str:
    """Десятичная запятая: 0.85 → «0,85»."""
    return f"{x:.{digits}f}".replace(".", ",")


def dist_text(m: float) -> str:
    """Расстояние для текста: «80 м», «1,2 км». Округляем до 10 м и не пишем «0 м»."""
    if m >= 1000:
        return f"{dec(m / 1000)} км"
    return f"{max(10, round(m / 10) * 10):.0f} м"


def walk_min_text(minutes: float, suffix: str = " пешком") -> str:
    """«≈5 мин пешком», «меньше минуты пешком»."""
    return ("меньше минуты" if minutes < 1 else f"≈{minutes:.0f} мин") + suffix


def there_back_text(to: float, back: float) -> str:
    """«≈5 мин туда, ≈6 мин обратно» или «меньше минуты в обе стороны»."""
    if to < 1 and back < 1:
        return "меньше минуты в обе стороны"
    return f"{walk_min_text(to, ' туда')}, {walk_min_text(back, ' обратно')}"


def walk_to_text(place_gen: str, meters: float, minutes: float, near: str) -> str:
    """«до пляжа 230 м, ≈3 мин пешком»; ближе NEAR_M — фраза near («прямо у пляжа (первая линия)»)."""
    if meters < NEAR_M:
        return near
    return f"до {place_gen} {dist_text(meters)}, {walk_min_text(minutes)}"
