"""Разбор тега OSM opening_hours (распространённое подмножество синтаксиса).

Поддерживается: 24/7, дни недели и диапазоны (Mo-Fr, Sa,Su), интервалы времени
(в т.ч. через полночь: 18:00-03:00 и 24:00), несколько интервалов через запятую,
правила через «;» и «,», off/closed, сезонные префиксы (May-Sep: ...), PH (игнорируется).
Неразобранное выражение возвращает None — агент помечает значение как «неизвестно»,
а не угадывает.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

DAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

_TIME = r"(\d{1,2}):(\d{2})"
_INTERVAL_RE = re.compile(rf"^{_TIME}\s*-\s*{_TIME}(\+)?$")
_OPEN_END_RE = re.compile(rf"^{_TIME}\+$")
_DAY_TOKEN_RE = re.compile(r"^(Mo|Tu|We|Th|Fr|Sa|Su)(?:\s*-\s*(Mo|Tu|We|Th|Fr|Sa|Su))?$")
_MONTH_PREFIX_RE = re.compile(
    r"^((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)(?:\s*\d{1,2})?"
    r"(?:\s*-\s*(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)(?:\s*\d{1,2})?)?"
    r"(?:\s*,\s*(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)(?:\s*-\s*(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec))?)*)"
    r"\s*:?\s+(.*)$"
)


@dataclass
class Schedule:
    raw: str
    always: bool = False
    # day index -> list of (start_min, end_min), end может быть > 1440 (работа после полуночи)
    week: dict[int, list[tuple[int, int]]] = field(default_factory=dict)
    months: set[int] | None = None  # сезонное ограничение (если задано одно на всё выражение)

    def intervals(self, day: int) -> list[tuple[int, int]]:
        return self.week.get(day, [])

    def open_at(self, day: int, minute: int) -> bool:
        if self.always:
            return True
        for s, e in self.intervals(day):
            if s <= minute < e:
                return True
        prev = (day - 1) % 7
        for _s, e in self.intervals(prev):
            if e > 1440 and minute < e - 1440:
                return True
        return False

    def night_days(self, minute: int = 60) -> int:
        """Сколько дней в неделю открыто в указанную минуту ночи (по умолчанию 01:00)."""
        return sum(1 for d in range(7) if self.open_at(d, minute))

    def latest_close(self) -> int | None:
        if self.always:
            return 1440 * 2
        ends = [e for d in range(7) for _, e in self.intervals(d)]
        return max(ends) if ends else None

    def summary(self) -> str:
        if self.always:
            return "круглосуточно"
        latest = self.latest_close()
        if latest is None:
            return "закрыто"
        n1 = self.night_days(60)
        if n1 >= 5:
            return f"ночью открыто ({n1} дн./нед.), до {fmt_minute(latest)}"
        return f"до {fmt_minute(latest)}"


def fmt_minute(m: int) -> str:
    m = m % 1440 if m != 1440 else 1440
    if m == 1440:
        return "24:00"
    return f"{m // 60:02d}:{m % 60:02d}"


def _parse_days(token: str) -> list[int] | None:
    token = token.strip()
    if not token:
        return list(range(7))
    days: list[int] = []
    for part in token.split(","):
        part = part.strip()
        if not part:
            continue
        if part == "PH" or part == "SH":
            continue
        m = _DAY_TOKEN_RE.match(part)
        if not m:
            return None
        a = DAYS.index(m.group(1))
        b = DAYS.index(m.group(2)) if m.group(2) else a
        if b >= a:
            days.extend(range(a, b + 1))
        else:
            days.extend(list(range(a, 7)) + list(range(0, b + 1)))
    return days


def _parse_times(token: str) -> list[tuple[int, int]] | None:
    out = []
    for part in token.split(","):
        part = part.strip()
        if not part:
            continue
        m = _INTERVAL_RE.match(part)
        if m:
            s = int(m.group(1)) * 60 + int(m.group(2))
            e = int(m.group(3)) * 60 + int(m.group(4))
            if e <= s:
                e += 1440
            out.append((s, e))
            continue
        m = _OPEN_END_RE.match(part)
        if m:
            s = int(m.group(1)) * 60 + int(m.group(2))
            out.append((s, s + 240))  # «открыто до последнего клиента» — консервативно 4 часа
            continue
        return None
    return out


def _months_of(prefix: str) -> set[int]:
    months: set[int] = set()
    for part in prefix.split(","):
        part = re.sub(r"\d", "", part).strip()
        if "-" in part:
            a, b = [p.strip() for p in part.split("-")]
            ia, ib = MONTHS.index(a), MONTHS.index(b)
            rng = range(ia, ib + 1) if ib >= ia else list(range(ia, 12)) + list(range(0, ib + 1))
            months.update(i + 1 for i in rng)
        elif part:
            months.add(MONTHS.index(part) + 1)
    return months


def parse(raw: str | None) -> Schedule | None:
    if not raw:
        return None
    text = raw.strip()
    if text in {"24/7", "Mo-Su 00:00-24:00", "00:00-24:00", "Mo-Su 00:00-00:00"}:
        return Schedule(raw=raw, always=True)
    sched = Schedule(raw=raw)
    # правила разделяются «;»; «,» внутри правила может разделять и дни, и интервалы, и правила
    rules = [r.strip() for r in text.split(";") if r.strip()]
    expanded: list[str] = []
    for r in rules:
        # «Mo-Fr 09:00-20:00, Sa-Su 10:00-18:00» -> два правила
        pieces = re.split(r",\s*(?=(?:Mo|Tu|We|Th|Fr|Sa|Su|PH)\b)", r)
        expanded.extend(p.strip() for p in pieces if p.strip())
    for rule in expanded:
        m = _MONTH_PREFIX_RE.match(rule)
        if m:
            try:
                sched.months = (sched.months or set()) | _months_of(m.group(1))
            except ValueError:
                return None
            rule = m.group(2).strip()
        rule = rule.replace("–", "-").strip()
        if rule.startswith("PH") or rule.startswith("SH"):
            continue
        # отделяем дни от времени
        mm = re.match(r"^((?:(?:Mo|Tu|We|Th|Fr|Sa|Su|PH)(?:\s*-\s*(?:Mo|Tu|We|Th|Fr|Sa|Su))?\s*,?\s*)*)(.*)$", rule)
        if not mm:
            return None
        day_part, time_part = mm.group(1).strip().rstrip(","), mm.group(2).strip()
        days = _parse_days(day_part)
        if days is None:
            return None
        if time_part.lower() in {"off", "closed"}:
            for d in days:
                sched.week[d] = []
            continue
        if time_part in {"24/7", "00:00-24:00"}:
            times = [(0, 1440)]
        else:
            times = _parse_times(time_part)
        if times is None or not times:
            return None
        for d in days:
            sched.week[d] = list(times)
    if not sched.week:
        return None
    if all(sched.week.get(d) == [(0, 1440)] for d in range(7)):
        sched.always = True
    return sched
