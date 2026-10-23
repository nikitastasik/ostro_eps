"""Реестр характеристик: встроенные (YAML) и добавленные пользователями (БД).

Чтобы добавить характеристику, достаточно описать её в YAML (папка specs/) или
текстом через интерфейс — агент-компилятор превратит описание в такую же спецификацию.
"""

from __future__ import annotations

import copy
import threading
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Characteristic, utcnow

SPEC_DIR = Path(__file__).parent / "specs"

GROUPS = [
    {"key": "view", "title": "Вид из окна"},
    {"key": "atmosphere", "title": "Тишина и атмосфера"},
    {"key": "walk", "title": "Пляж, центр, транспорт"},
    {"key": "around", "title": "Что рядом"},
    {"key": "building", "title": "Здание"},
    {"key": "hotelier", "title": "Со слов отельера"},
    {"key": "custom", "title": "Добавленные характеристики"},
]
GROUP_TITLES = {g["key"]: g["title"] for g in GROUPS}

_cache: dict[str, dict] | None = None
_lock = threading.Lock()


def normalize(spec: dict) -> dict:
    s = copy.deepcopy(spec)
    s.setdefault("group", "custom")
    s.setdefault("scope", "hotel")
    s.setdefault("icon", "dot")
    s.setdefault("description", "")
    s.setdefault("value_fields", {})
    s.setdefault("extra_fields", {})
    s.setdefault("depends_on", [])
    s.setdefault("recipe", [])
    s.setdefault("confidence_threshold", 0.4)
    s.setdefault("ttl_days", 180)
    s.setdefault("invalidate_on", [])
    c = s.setdefault("conflict", {})
    c.setdefault("fields", [])
    c.setdefault("authority", [])
    c.setdefault("policy", "priority")
    q = s.get("question")
    if q is not None:
        q.setdefault("fields", list(s["value_fields"].keys()))
    srch = s.setdefault("search", {})
    srch.setdefault("phrases", [])
    srch.setdefault("criteria", [])
    srch.setdefault("variants", [])
    srch.setdefault("partial", [])
    srch.setdefault("numeric", [])
    return s


def load_builtin() -> list[dict]:
    specs = []
    for path in sorted(SPEC_DIR.glob("*.yaml")):
        with open(path, encoding="utf-8") as f:
            for spec in yaml.safe_load(f) or []:
                specs.append(normalize(spec))
    return specs


def sync_builtin(session: Session) -> int:
    """Записывает встроенные спецификации в БД (новые — добавляет, изменённые — обновляет)."""
    n = 0
    for spec in load_builtin():
        row = session.get(Characteristic, spec["key"])
        if row is None:
            session.add(Characteristic(key=spec["key"], title=spec["title"], group=spec["group"], scope=spec["scope"],
                                       description=spec["description"], spec=spec, origin="builtin", compiled_by="yaml"))
            n += 1
        elif row.origin == "builtin" and row.spec != spec:
            row.spec = spec
            row.title = spec["title"]
            row.group = spec["group"]
            row.scope = spec["scope"]
            row.description = spec["description"]
            row.version += 1
            row.updated_at = utcnow()
            n += 1
    session.flush()
    invalidate()
    return n


def invalidate() -> None:
    global _cache
    with _lock:
        _cache = None


def all_specs(session: Session, enabled_only: bool = True) -> dict[str, dict]:
    global _cache
    with _lock:
        if _cache is None:
            rows = session.scalars(select(Characteristic)).all()
            _cache = {r.key: {**normalize(r.spec), "_enabled": r.enabled, "_origin": r.origin, "_version": r.version,
                              "_compiled_by": r.compiled_by, "_created_from_text": r.created_from_text}
                      for r in rows}
        specs = _cache
    if enabled_only:
        return {k: v for k, v in specs.items() if v.get("_enabled", True)}
    return dict(specs)


def get_spec(session: Session, key: str) -> dict | None:
    return all_specs(session, enabled_only=False).get(key)


def ordered_keys(specs: dict[str, dict]) -> list[str]:
    """Порядок обработки с учётом зависимостей (этажность — раньше видов)."""
    order: list[str] = []
    seen: set[str] = set()

    def visit(k: str):
        if k in seen or k not in specs:
            return
        seen.add(k)
        for dep in specs[k].get("depends_on", []):
            visit(dep)
        order.append(k)

    for k in specs:
        visit(k)
    return order
