"""Регулярные растры в локальной метрической системе координат."""

from __future__ import annotations

import numpy as np


class Grid:
    """Растр: x0, y0 — координаты центра ячейки [0, 0] (нижний левый угол сетки), res — шаг, м.

    Индексация массива: a[row, col], row растёт с севера на юг не используется —
    row соответствует y (с юга на север), col соответствует x.
    """

    def __init__(self, array: np.ndarray, x0: float, y0: float, res: float, nodata: float | None = None):
        self.a = array
        self.x0 = float(x0)
        self.y0 = float(y0)
        self.res = float(res)
        self.ny, self.nx = array.shape
        self.nodata = nodata

    @property
    def extent(self) -> tuple[float, float, float, float]:
        return (self.x0, self.y0, self.x0 + (self.nx - 1) * self.res, self.y0 + (self.ny - 1) * self.res)

    def inside(self, x, y):
        x = np.asarray(x)
        y = np.asarray(y)
        xmin, ymin, xmax, ymax = self.extent
        return (x >= xmin) & (x <= xmax) & (y >= ymin) & (y <= ymax)

    def nearest(self, x, y, fill=0):
        """Значение ближайшей ячейки (для категориальных растров)."""
        x = np.asarray(x, dtype=float)
        y = np.asarray(y, dtype=float)
        c = np.rint((x - self.x0) / self.res).astype(np.int64)
        r = np.rint((y - self.y0) / self.res).astype(np.int64)
        ok = (c >= 0) & (c < self.nx) & (r >= 0) & (r < self.ny)
        out = np.full(x.shape, fill, dtype=self.a.dtype)
        out[ok] = self.a[r[ok], c[ok]]
        return out

    def bilinear(self, x, y, fill=np.nan):
        x = np.asarray(x, dtype=float)
        y = np.asarray(y, dtype=float)
        fc = (x - self.x0) / self.res
        fr = (y - self.y0) / self.res
        c0 = np.floor(fc).astype(np.int64)
        r0 = np.floor(fr).astype(np.int64)
        ok = (c0 >= 0) & (c0 < self.nx - 1) & (r0 >= 0) & (r0 < self.ny - 1)
        out = np.full(x.shape, fill, dtype=float)
        if not np.any(ok):
            return out
        c0o, r0o = c0[ok], r0[ok]
        tx = fc[ok] - c0o
        ty = fr[ok] - r0o
        a = self.a
        v00 = a[r0o, c0o].astype(float)
        v01 = a[r0o, c0o + 1].astype(float)
        v10 = a[r0o + 1, c0o].astype(float)
        v11 = a[r0o + 1, c0o + 1].astype(float)
        out[ok] = v00 * (1 - tx) * (1 - ty) + v01 * tx * (1 - ty) + v10 * (1 - tx) * ty + v11 * tx * ty
        return out

    def cell_of(self, x: float, y: float) -> tuple[int, int]:
        return int(round((y - self.y0) / self.res)), int(round((x - self.x0) / self.res))

    def cell_centers(self, r0: int, r1: int, c0: int, c1: int):
        """Координаты центров ячеек в прямоугольнике строк [r0, r1) и столбцов [c0, c1)."""
        cols = self.x0 + np.arange(c0, c1) * self.res
        rows = self.y0 + np.arange(r0, r1) * self.res
        return np.meshgrid(cols, rows)


def rasterize_polygon(
    grid: Grid, polygon, value, mode: str = "max", ids: np.ndarray | None = None, id_value: int | None = None
) -> int:
    """Прожигает полигон shapely (в метрах) в растр. Возвращает число затронутых ячеек."""
    import shapely

    minx, miny, maxx, maxy = polygon.bounds
    c0 = max(0, int(np.floor((minx - grid.x0) / grid.res)))
    c1 = min(grid.nx, int(np.ceil((maxx - grid.x0) / grid.res)) + 1)
    r0 = max(0, int(np.floor((miny - grid.y0) / grid.res)))
    r1 = min(grid.ny, int(np.ceil((maxy - grid.y0) / grid.res)) + 1)
    if c0 >= c1 or r0 >= r1:
        return 0
    xs, ys = grid.cell_centers(r0, r1, c0, c1)
    mask = shapely.contains_xy(polygon, xs, ys)
    if not mask.any():
        # маленький полигон меньше ячейки — прожигаем ячейку центроида
        cx, cy = polygon.centroid.x, polygon.centroid.y
        r, c = grid.cell_of(cx, cy)
        if 0 <= r < grid.ny and 0 <= c < grid.nx:
            mask = np.zeros(xs.shape, dtype=bool)
            if r0 <= r < r1 and c0 <= c < c1:
                mask[r - r0, c - c0] = True
    sub = grid.a[r0:r1, c0:c1]
    if mode == "max":
        upd = mask & (sub < value)
        sub[upd] = value
    else:
        upd = mask
        sub[upd] = value
    if ids is not None and id_value is not None:
        ids[r0:r1, c0:c1][upd] = id_value
    return int(upd.sum())
