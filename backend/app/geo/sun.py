"""Положение Солнца по алгоритму NOAA (Solar Calculator, точность ~0.01° для 1800–2100 гг.)."""

from __future__ import annotations

import datetime as dt
import math

import numpy as np

SUNSET_ELEVATION = -0.833  # центр диска на геометрическом горизонте с учётом рефракции и радиуса диска


def _julian_day(when: dt.datetime) -> float:
    if when.tzinfo is not None:
        when = when.astimezone(dt.timezone.utc).replace(tzinfo=None)
    a = (14 - when.month) // 12
    y = when.year + 4800 - a
    m = when.month + 12 * a - 3
    jdn = when.day + (153 * m + 2) // 5 + 365 * y + y // 4 - y // 100 + y // 400 - 32045
    frac = (when.hour - 12) / 24 + when.minute / 1440 + when.second / 86400
    return jdn + frac


def _sun_terms(jd: float) -> tuple[float, float]:
    """Склонение (град) и уравнение времени (мин)."""
    jc = (jd - 2451545.0) / 36525.0
    l0 = (280.46646 + jc * (36000.76983 + jc * 0.0003032)) % 360
    m = 357.52911 + jc * (35999.05029 - 0.0001537 * jc)
    e = 0.016708634 - jc * (0.000042037 + 0.0000001267 * jc)
    mr = math.radians(m)
    c = (
        math.sin(mr) * (1.914602 - jc * (0.004817 + 0.000014 * jc))
        + math.sin(2 * mr) * (0.019993 - 0.000101 * jc)
        + math.sin(3 * mr) * 0.000289
    )
    true_long = l0 + c
    omega = 125.04 - 1934.136 * jc
    app_long = true_long - 0.00569 - 0.00478 * math.sin(math.radians(omega))
    mean_obliq = 23 + (26 + (21.448 - jc * (46.815 + jc * (0.00059 - jc * 0.001813))) / 60) / 60
    obliq = mean_obliq + 0.00256 * math.cos(math.radians(omega))
    decl = math.degrees(math.asin(math.sin(math.radians(obliq)) * math.sin(math.radians(app_long))))
    vy = math.tan(math.radians(obliq / 2)) ** 2
    l0r = math.radians(l0)
    eq_time = 4 * math.degrees(
        vy * math.sin(2 * l0r)
        - 2 * e * math.sin(mr)
        + 4 * e * vy * math.sin(mr) * math.cos(2 * l0r)
        - 0.5 * vy * vy * math.sin(4 * l0r)
        - 1.25 * e * e * math.sin(2 * mr)
    )
    return decl, eq_time


def solar_position(when_utc: dt.datetime, lat: float, lon: float) -> tuple[float, float]:
    """Возвращает (высота над горизонтом, азимут от севера по часовой), градусы.

    Высота без поправки на рефракцию (геометрическая).
    """
    decl, eq_time = _sun_terms(_julian_day(when_utc))
    minutes = when_utc.hour * 60 + when_utc.minute + when_utc.second / 60
    tst = (minutes + eq_time + 4 * lon) % 1440
    ha = tst / 4 - 180 if tst / 4 >= 0 else tst / 4 + 180
    latr, declr, har = math.radians(lat), math.radians(decl), math.radians(ha)
    cos_zen = math.sin(latr) * math.sin(declr) + math.cos(latr) * math.cos(declr) * math.cos(har)
    zen = math.degrees(math.acos(max(-1.0, min(1.0, cos_zen))))
    denom = math.cos(latr) * math.sin(math.radians(zen))
    if abs(denom) < 1e-9:
        az = 180.0
    else:
        cos_az = ((math.sin(latr) * math.cos(math.radians(zen))) - math.sin(declr)) / denom
        acos_az = math.degrees(math.acos(max(-1.0, min(1.0, cos_az))))
        az = (acos_az + 180) % 360 if ha > 0 else (540 - acos_az) % 360
    return 90 - zen, az


def sunset_utc(day: dt.date, lat: float, lon: float) -> dt.datetime | None:
    """Время заката (UTC) для даты; None для полярного дня/ночи."""
    noon = dt.datetime(day.year, day.month, day.day, 12, 0)
    decl, eq_time = _sun_terms(_julian_day(noon))
    latr, declr = math.radians(lat), math.radians(decl)
    cos_ha = math.cos(math.radians(90 - SUNSET_ELEVATION)) / (math.cos(latr) * math.cos(declr)) - math.tan(latr) * math.tan(declr)
    if not -1 <= cos_ha <= 1:
        return None
    ha = math.degrees(math.acos(cos_ha))
    minutes = 720 - 4 * (lon - ha) - eq_time
    return dt.datetime(day.year, day.month, day.day) + dt.timedelta(minutes=minutes)


def sunset_path(day: dt.date, lat: float, lon: float, minutes_before: int = 80, step_min: int = 4):
    """Траектория Солнца перед закатом: список (минут_до_заката, высота, азимут)."""
    t_set = sunset_utc(day, lat, lon)
    if t_set is None:
        return []
    out = []
    for m in range(minutes_before, -1, -step_min):
        t = t_set - dt.timedelta(minutes=m)
        el, az = solar_position(t, lat, lon)
        out.append((m, el, az))
    return out


def monthly_sunsets(lat: float, lon: float, year: int = 2026, tz_hours: int = 3) -> list[dict]:
    """Для 15-го числа каждого месяца: азимут и местное время заката, путь Солнца."""
    out = []
    for month in range(1, 13):
        day = dt.date(year, month, 15)
        t_set = sunset_utc(day, lat, lon)
        _, az = solar_position(t_set, lat, lon)
        path = sunset_path(day, lat, lon)
        local = t_set + dt.timedelta(hours=tz_hours)
        out.append(
            {
                "month": month,
                "azimuth": round(az, 1),
                "local_time": local.strftime("%H:%M"),
                "path": [(m, round(el, 2), round(a, 2)) for m, el, a in path],
            }
        )
    return out


def angle_diff(a: float | np.ndarray, b: float | np.ndarray):
    """Разница азимутов в диапазоне [-180, 180)."""
    return (np.asarray(a) - np.asarray(b) + 180.0) % 360.0 - 180.0
