"""Инструменты климатолога: нормы по месяцам из открытых данных NOAA."""

from __future__ import annotations

from ..geo.analysis import MONTHS_NOM, month_ranges
from .base import ToolResult, tool

AGENT = "Климатолог"
MONTHS_PREP = [
    "январе",
    "феврале",
    "марте",
    "апреле",
    "мае",
    "июне",
    "июле",
    "августе",
    "сентябре",
    "октябре",
    "ноябре",
    "декабре",
]


@tool(
    "climate.monthly",
    "Климат по месяцам",
    AGENT,
    ["NOAA GHCN-Daily (метеостанция Сочи)", "NOAA OISST v2.1"],
    "Температура воздуха, моря и дождливые дни по месяцам; месяцы купального сезона (море ≥ 20 °C).",
)
def monthly(ctx, params) -> ToolResult:
    c = ctx.region.climate
    months = c["months"]
    swim = [m["month"] for m in months if m["sea_c"] >= 20]
    warm = [m["month"] for m in months if m["sea_c"] >= 22]
    by_month = {
        str(m["month"]): {
            "sea_c": m["sea_c"],
            "air_tmax_c": m["air_tmax_c"],
            "air_tmin_c": m["air_tmin_c"],
            "rain_days": m["rain_days"],
            "share_ge_20": m["share_ge_20"],
        }
        for m in months
    }
    hottest = max(months, key=lambda m: m["sea_c"])
    driest = min((m for m in months if m["month"] in swim), key=lambda m: m["rain_days"] or 99, default=None)
    value = {"swim_months": swim, "warm_sea_months": warm, "by_month": by_month}
    s = f"Море теплее 20 °C: {month_ranges(swim)}; теплее всего в {MONTHS_PREP[hottest['month'] - 1]} (≈{hottest['sea_c']:.0f} °C)"
    if driest:
        s += f"; меньше всего дождливых дней в купальный сезон — в {MONTHS_PREP[driest['month'] - 1]} ({driest['rain_days']:.0f})"
    tail = [m for m in months if m["month"] in swim and m["share_ge_20"] < 0.95]
    if tail:
        t = max(tail, key=lambda m: m["month"])
        s += (
            f". В {MONTHS_PREP[t['month'] - 1]} ещё купаются: море в среднем {t['sea_c']:.0f} °C, "
            f"тёплым (≥ 20 °C) оно бывает в {round(t['share_ge_20'] * 100)}% дней"
        )
    detail = (
        f"Воздух: {c['station']['source']}, станция {c['station']['name']} ({c['station']['years'][0]}–{c['station']['years'][1]}); "
        f"море: {c['sea']['source']} ({c['sea']['years'][0]}–{c['sea']['years'][1]})"
    )
    return ToolResult(
        value,
        s,
        0.85,
        "climate",
        detail,
        ttl_days=3650,
        evidence={
            "note": "значения одинаковы для всего региона (одна станция и одна ячейка спутниковых данных)",
            "months": [MONTHS_NOM[m - 1] for m in swim],
        },
    )


@tool(
    "climate.month_metric",
    "Климатический показатель месяца",
    AGENT,
    ["NOAA GHCN-Daily", "NOAA OISST v2.1"],
    "Значение показателя (sea_c, air_tmax_c, air_tmin_c, rain_days) для заданного месяца.",
    generic=True,
    params_schema={"metric": "sea_c | air_tmax_c | air_tmin_c | rain_days", "month": "1..12"},
)
def month_metric(ctx, params) -> ToolResult:
    c = ctx.region.climate
    m = int(params.get("month", 7))
    metric = params.get("metric", "sea_c")
    row = next(x for x in c["months"] if x["month"] == m)
    val = row.get(metric)
    return ToolResult(
        {"value": val, "month": m, "metric": metric},
        f"{MONTHS_NOM[m - 1].capitalize()}: {metric} = {val}",
        0.8,
        "climate",
        "NOAA GHCN-Daily / OISST",
        ttl_days=3650,
    )
