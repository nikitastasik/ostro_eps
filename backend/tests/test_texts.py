"""Корректор текстов: все тексты для пользователя в сиде без «0 м», «≈0 мин», непарных кавычек и т. п."""

import gzip
import json
from collections import Counter
from pathlib import Path

import pytest

from app.nlp.textlint import anomalies

SEED = Path(__file__).resolve().parents[2] / "data" / "seed" / "seed.json.gz"
FIELDS = [
    ("resolved_values", "summary"),
    ("facts", "summary"),
    ("questions", "text"),
    ("changes", "old_summary"),
    ("changes", "new_summary"),
    ("conflicts", "description"),
    ("agent_steps", "message"),
]


def test_linter_catches_known_problems():
    assert "«0 м»" in anomalies("до пляжа 0 м, рядом")
    assert "«≈0 мин»" in anomalies("≈0 мин пешком")
    assert "повтор слова" in anomalies("пляж «пляж» галечный")
    assert "десятичная точка" in anomalies("уверенность 0.85")
    assert "непарные «»" in anomalies("парк «Ривьера")
    assert "двойной пробел" in anomalies("до  пляжа")
    assert not anomalies("до пляжа 230 м, ≈3 мин пешком; уверенность 0,85; 10 м; источник OISST v2.1")


@pytest.mark.skipif(not SEED.exists(), reason="нет сида")
def test_seed_texts_are_clean():
    doc = json.load(gzip.open(SEED, "rt", encoding="utf-8"))
    found, examples = Counter(), {}
    for table, field in FIELDS:
        for row in doc.get(table, []):
            for a in anomalies(row.get(field) or ""):
                found[(table, a)] += 1
                examples.setdefault((table, a), row.get(field)[:160])
    assert not found, "\n".join(f"{n}× {t}/{a}: {examples[(t, a)]}" for (t, a), n in found.most_common())
