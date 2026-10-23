"""Корректор текстов для пользователя: находит «0 м», «≈0 мин», непарные кавычки, десятичную точку и т. п.

Используется в тестах: все тексты фактов из сида и тексты поиска/сравнения должны проходить без замечаний.
"""

from __future__ import annotations

import re

CHECKS = [
    ("«≈0 мин»", re.compile(r"≈\s?0 мин")),
    ("«0 м»", re.compile(r"(?<![\d,.])0 м\b")),
    ("«0-го этажа»", re.compile(r"(?<!\d)0-го этаж")),
    ("двойной пробел", re.compile(r"\S {2,}\S")),
    ("десятичная точка", re.compile(r"(?<![\w.])\d+\.\d+(?![\w.])")),
    ("повтор слова", re.compile(r"\b(\w{3,})\s+[«„\"]\1\b", re.IGNORECASE)),
]


def anomalies(text: str) -> list[str]:
    """Список замечаний к тексту (пустой — всё в порядке)."""
    if not text:
        return []
    out = [name for name, rx in CHECKS if rx.search(text)]
    if text.count("«") != text.count("»"):
        out.append("непарные «»")
    if text.count("„") != text.count("“"):
        out.append("непарные „“")
    if text.count('"') % 2:
        out.append('непарные "')
    return out
