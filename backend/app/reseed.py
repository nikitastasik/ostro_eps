"""Пересоздать базу и загрузить демо-знание заново: python -m app.reseed

Используется командой `make seed`. Полезно, если в ходе демо данные изменили
(ответили на вопросы, построили дома) и хочется вернуться к исходному состоянию.
"""

from __future__ import annotations

import logging

from .db import Base, SessionLocal, engine, init_db
from .seed import bootstrap

logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(message)s")


def main() -> None:
    from . import models  # noqa: F401

    Base.metadata.drop_all(engine)
    init_db()
    with SessionLocal() as session:
        mode = bootstrap(session)
        session.commit()
    print(f"База пересоздана, режим загрузки: {mode}. Перезапустите сервис app, чтобы сбросить кеши.")


if __name__ == "__main__":
    main()
