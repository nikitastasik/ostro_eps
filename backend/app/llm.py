"""Клиент OpenAI-совместимого API (OpenAI, YandexGPT через совместимый шлюз, vLLM, Ollama и т. п.).

LLM в системе только переводит между естественным языком и структурами:
разбирает запросы гостей и ответы отельеров, компилирует описание новой
характеристики в спецификацию. Значения характеристик LLM не «угадывает».
Если ключ не задан или API недоступен, агенты переходят на правила.
"""

from __future__ import annotations

import json
import logging
import re
import time

import httpx

from .config import settings

log = logging.getLogger(__name__)

_last_error: dict = {"at": None, "error": None}


def enabled() -> bool:
    return settings.llm_enabled


def status() -> dict:
    return {
        "enabled": enabled(),
        "model": settings.llm_model if enabled() else None,
        "base_url": settings.llm_base_url if enabled() else None,
        "last_error": _last_error["error"],
    }


def _extract_json(text: str):
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1)
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=-1)
    if start > 0:
        text = text[start:]
    return json.loads(text)


def chat_json(system: str, user: str, max_tokens: int = 1200, temperature: float = 0.0) -> dict | None:
    """Возвращает распарсенный JSON-ответ модели или None (нет ключа, ошибка, невалидный JSON)."""
    if not enabled():
        return None
    payload = {
        "model": settings.llm_model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {settings.llm_api_key}", "Content-Type": "application/json"}
    url = f"{settings.llm_base_url}/chat/completions"
    for attempt in range(2):
        try:
            t0 = time.perf_counter()
            with httpx.Client(timeout=settings.llm_timeout) as client:
                r = client.post(url, json=payload, headers=headers)
                if r.status_code == 400 and "response_format" in r.text and attempt == 0:
                    payload.pop("response_format", None)
                    continue
                r.raise_for_status()
                content = r.json()["choices"][0]["message"]["content"]
            log.info("LLM ответ за %.1f с", time.perf_counter() - t0)
            return _extract_json(content)
        except Exception as exc:  # noqa: BLE001
            _last_error.update({"at": time.time(), "error": f"{exc.__class__.__name__}: {str(exc)[:200]}"})
            log.warning("LLM недоступна: %s", exc)
            if attempt == 0 and "response_format" in payload:
                payload.pop("response_format", None)
                continue
            return None
    return None
