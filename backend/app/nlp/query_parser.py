"""Разбор запроса гостя в критерии поиска по правилам.

Словарь фраз берётся из спецификаций характеристик (search.phrases / variants),
поэтому новая характеристика сразу становится доступной для поиска.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from .ru import find_phrase, months_in, norm, number_before, phrase_tokens, split_clauses, tokens

IMPORTANT_RE = re.compile(r"обязательн|главное|важно|непременно|точно|чтобы был|принципиальн|только с")
OPTIONAL_RE = re.compile(r"желательн|хорошо бы|неплохо бы|было бы|по возможности|если получится|можно и")
UNITS = {"min": r"мин\w*", "m": r"м\b|метр\w*", "km": r"км\b|километр\w*"}


@dataclass
class Criterion:
    key: str
    field: str
    op: str
    value: object
    soft: float | None = None
    weight: float = 1.0
    required: bool = False
    month: int | None = None
    month_field: str | None = None
    label: str = ""
    source_text: str = ""
    score: float | None = None  # для частичного совпадения

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class ParsedQuery:
    text: str
    criteria: list[Criterion] = field(default_factory=list)
    months: list[int] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)
    parser: str = "rules"

    def as_dict(self) -> dict:
        return {"text": self.text, "criteria": [c.as_dict() for c in self.criteria], "months": self.months,
                "unmatched": self.unmatched, "parser": self.parser}


NEGATIONS = {"без", "не", "нет", "никаких", "кроме"}


def _match_any(toks: list[str], phrases: list[str]) -> str | None:
    """Самая длинная из фраз, найденная в токенах и не стоящая под отрицанием («без ночных клубов»)."""
    best = None
    for p in phrases:
        pt = phrase_tokens(p)
        pos = find_phrase(toks, pt)
        if pos is None:
            continue
        negated = pt[0] not in NEGATIONS and any(t in NEGATIONS for t in toks[max(0, pos[0] - 2):pos[0]])
        if negated:
            continue
        if best is None or len(p) > len(best):
            best = p
    return best


def parse_query(text: str, specs: dict[str, dict]) -> ParsedQuery:
    pq = ParsedQuery(text=text)
    pq.months = months_in(text)
    clauses = split_clauses(text) or [norm(text)]
    by_key: dict[str, list[Criterion]] = {}
    for clause in clauses:
        toks = tokens(clause)
        weight = 2.0 if IMPORTANT_RE.search(clause) else 0.5 if OPTIONAL_RE.search(clause) else 1.0
        required = bool(IMPORTANT_RE.search(clause))
        matched_any = False
        for key, spec in specs.items():
            srch = spec.get("search") or {}
            base_hit = _match_any(toks, srch.get("phrases", []))
            if not base_hit and _match_any(toks, srch.get("numeric_anchors", [])) and any(
                    number_before(clause, UNITS[n["unit"]]) is not None for n in srch.get("numeric", [])):
                base_hit = clause
            variant_hits = []
            for var in srch.get("variants", []):
                hit = _match_any(toks, var.get("phrases", []))
                if hit and (not var.get("requires_base") or base_hit):
                    variant_hits.append((var, hit))
            if not base_hit and not variant_hits:
                continue
            matched_any = True
            crits: list[Criterion] = []
            month_field = srch.get("month_field")
            base_label = srch.get("label") or spec["title"]
            if base_hit and not any(v.get("replace_base") for v, _ in variant_hits):
                for c in srch.get("criteria", []):
                    crits.append(Criterion(key=key, field=c["field"], op=c["op"], value=c["value"], soft=c.get("soft"),
                                           label=base_label, source_text=base_hit, month_field=month_field))
            for var, hit in variant_hits:
                vm = var.get("month_field", month_field)
                for c in var.get("criteria", []):
                    crits = [x for x in crits if x.field != c["field"]]
                    crits.append(Criterion(key=key, field=c["field"], op=c["op"], value=c["value"], soft=c.get("soft"),
                                           label=var.get("label") or base_label, source_text=hit, month_field=vm))
            # числа в той же фразе: «до моря 5 минут», «не дальше 300 м»
            for num in srch.get("numeric", []):
                unit = num["unit"]
                val = number_before(clause, UNITS[unit])
                if val is None:
                    continue
                if unit == "km":
                    val *= 1000
                crits = [x for x in crits if x.field != num["field"]]
                crits.append(Criterion(key=key, field=num["field"], op=num["op"], value=val, soft=val * 0.5,
                                       label=srch.get("label") or spec["title"], source_text=clause))
            for c in crits:
                c.weight = weight
                c.required = required
                if c.month_field and pq.months:
                    c.month = pq.months[0]
            partial = srch.get("partial", [])
            if partial and crits:
                crits[0].score = None
            by_key.setdefault(key, [])
            existing = {(x.field, x.op) for x in by_key[key]}
            by_key[key].extend(c for c in crits if (c.field, c.op) not in existing)
        if not matched_any:
            pq.unmatched.append(clause)
    # «тихо» и «в гуще событий» одновременно — оставляем оба: ранжирование покажет компромисс
    for crits in by_key.values():
        pq.criteria.extend(crits)
    return pq


def criteria_from_dicts(items: list[dict], specs: dict[str, dict]) -> list[Criterion]:
    """Проверка критериев, пришедших от LLM или из интерфейса (редактирование чипов)."""
    out = []
    ops = {"eq", "ne", "le", "ge", "lt", "gt", "in", "contains", "nonempty"}
    for it in items or []:
        key = it.get("key")
        spec = specs.get(key)
        if not spec:
            continue
        fields = {**spec.get("value_fields", {}), **spec.get("extra_fields", {})}
        fld = it.get("field")
        if fld not in fields:
            continue
        op = it.get("op", "eq")
        if op not in ops:
            continue
        month = it.get("month")
        out.append(Criterion(key=key, field=fld, op=op, value=it.get("value"), soft=it.get("soft"),
                             weight=float(it.get("weight", 1.0) or 1.0), required=bool(it.get("required", False)),
                             month=int(month) if month else None,
                             month_field=it.get("month_field") or (spec.get("search") or {}).get("month_field"),
                             label=it.get("label") or spec["title"], source_text=it.get("source_text", "")))
    return out
