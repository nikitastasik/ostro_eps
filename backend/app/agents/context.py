"""Контекст работы агента с одним отелем: доступ к данным, кешам и журналу."""

from __future__ import annotations

from functools import cached_property

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..geo.analysis import HotelGeo
from ..models import Hotel, ResolvedValue
from ..region import RegionData

TRUSTED_LEVEL_SOURCES = {"osm_levels", "osm_height", "osm_parts", "hotelier", "hotelier_confirmed", "osm"}


class HotelContext:
    def __init__(self, session: Session, hotel: Hotel, region: RegionData, logger=None):
        self.session = session
        self.hotel = hotel
        self.region = region
        self.log = logger
        self.cache: dict = {}

    # --- текущее знание по другим характеристикам ---
    def resolved(self, key: str) -> ResolvedValue | None:
        return self.session.get(ResolvedValue, (self.hotel.id, key))

    @cached_property
    def region_hotel(self) -> dict:
        b = self.hotel.building or {}
        return {"id": self.hotel.id, "x": b.get("x"), "y": b.get("y"), "building": b.get("idx")}

    def levels(self) -> tuple[int | None, str, float]:
        """Этажность для расчётов видов: (этажей, источник, уверенность)."""
        rv = self.resolved("building_levels")
        if rv is not None and rv.value and rv.value.get("levels"):
            src = rv.sources[0] if rv.sources else "unknown"
            return int(rv.value["levels"]), src, rv.confidence
        b = self.hotel.building or {}
        if b.get("levels"):
            return int(b["levels"]), b.get("levels_src", "osm"), 0.6
        return None, "none", 0.0

    @property
    def geo(self) -> HotelGeo:
        if "geo" not in self.cache:
            levels, src, _ = self.levels()
            self.cache["geo"] = HotelGeo(self.region, self.region_hotel, levels=levels, levels_src=src)
        return self.cache["geo"]

    def reset_geo(self) -> None:
        self.cache.pop("geo", None)

    def other_hotels(self) -> list[Hotel]:
        return list(self.session.scalars(select(Hotel)))
