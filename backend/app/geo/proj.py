"""Локальная метрическая проекция (равнопромежуточная вокруг центра региона).

Для региона ~100 км ошибка масштаба < 0.5 %, этого достаточно для видимости,
шума и пешеходных маршрутов, и не нужен pyproj/GDAL.
"""

from __future__ import annotations

import math

import numpy as np


class LocalProjection:
    def __init__(self, lat0: float, lon0: float):
        self.lat0 = lat0
        self.lon0 = lon0
        phi = math.radians(lat0)
        self.k_lat = 111132.954 - 559.822 * math.cos(2 * phi) + 1.175 * math.cos(4 * phi)
        self.k_lon = 111412.84 * math.cos(phi) - 93.5 * math.cos(3 * phi)

    def to_xy(self, lon, lat):
        lon = np.asarray(lon, dtype=float)
        lat = np.asarray(lat, dtype=float)
        return (lon - self.lon0) * self.k_lon, (lat - self.lat0) * self.k_lat

    def to_lonlat(self, x, y):
        x = np.asarray(x, dtype=float)
        y = np.asarray(y, dtype=float)
        return x / self.k_lon + self.lon0, y / self.k_lat + self.lat0


def azimuth_of(dx: float, dy: float) -> float:
    """Азимут вектора (от севера по часовой), градусы."""
    return math.degrees(math.atan2(dx, dy)) % 360.0


COMPASS_8 = ["С", "СВ", "В", "ЮВ", "Ю", "ЮЗ", "З", "СЗ"]
COMPASS_8_LONG = ["север", "северо-восток", "восток", "юго-восток", "юг", "юго-запад", "запад", "северо-запад"]
COMPASS_8_GEN = [
    "северной",
    "северо-восточной",
    "восточной",
    "юго-восточной",
    "южной",
    "юго-западной",
    "западной",
    "северо-западной",
]

COMPASS_8_ACC = [
    "северную",
    "северо-восточную",
    "восточную",
    "юго-восточную",
    "южную",
    "юго-западную",
    "западную",
    "северо-западную",
]
COMPASS_8_NOM = [
    "северная",
    "северо-восточная",
    "восточная",
    "юго-восточная",
    "южная",
    "юго-западная",
    "западная",
    "северо-западная",
]


def compass(az: float) -> str:
    return COMPASS_8[int(((az % 360) + 22.5) // 45) % 8]


def compass_index(az: float) -> int:
    return int(((az % 360) + 22.5) // 45) % 8
