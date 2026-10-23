"""Настройки приложения из переменных окружения (см. .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "да"}


def _default_data_dir() -> Path:
    env = os.environ.get("DATA_DIR")
    if env:
        return Path(env)
    # backend/app/config.py -> repo root / data
    return Path(__file__).resolve().parents[2] / "data"


@dataclass(frozen=True)
class Settings:
    app_port: int = int(os.environ.get("APP_PORT", "8080"))
    database_url: str = os.environ.get("DATABASE_URL", "sqlite:///./ostrovok.db")
    data_dir: Path = field(default_factory=_default_data_dir)
    region: str = os.environ.get("REGION", "sochi")
    static_dir: Path | None = Path(os.environ["STATIC_DIR"]) if os.environ.get("STATIC_DIR") else None

    # LLM (любой OpenAI-совместимый API). Без ключа система работает на правилах.
    llm_api_key: str = os.environ.get("LLM_API_KEY", "")
    llm_base_url: str = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    llm_model: str = os.environ.get("LLM_MODEL", "gpt-4o-mini")
    llm_timeout: float = float(os.environ.get("LLM_TIMEOUT", "40"))

    # Поведение агентов
    seed_on_start: bool = _bool("SEED_ON_START", True)
    worker_enabled: bool = _bool("WORKER_ENABLED", True)
    hotelier_autoreply: bool = _bool("HOTELIER_AUTOREPLY", False)
    actualize_interval_min: float = float(os.environ.get("ACTUALIZE_INTERVAL_MIN", "30"))

    # Демо-доступ в кабинет отельера
    hotelier_login: str = os.environ.get("HOTELIER_LOGIN", "hotelier")
    hotelier_password: str = os.environ.get("HOTELIER_PASSWORD", "hotelier123")
    secret_key: str = os.environ.get("SECRET_KEY", "change-me-in-env")

    log_level: str = os.environ.get("LOG_LEVEL", "INFO")

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_api_key)

    @property
    def region_dir(self) -> Path:
        return self.data_dir / self.region


settings = Settings()
