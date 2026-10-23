.PHONY: up down logs seed reset install test lint fmt build data precompute eval dev

# Python из backend/.venv (make install), иначе системный python3
PY := $(if $(wildcard backend/.venv/bin/python),$(CURDIR)/backend/.venv/bin/python,python3)

up:            ## запустить всё (http://localhost:8080)
	docker compose up --build -d
	@echo "Откройте http://localhost:8080"

down:          ## остановить
	docker compose down

logs:          ## логи приложения
	docker compose logs -f app

seed:          ## пересоздать БД и загрузить демо-знание заново
	docker compose exec app python -m app.reseed
	docker compose restart app

reset:         ## снести контейнеры вместе с данными Postgres
	docker compose down -v

install:       ## локальные зависимости для разработки: backend/.venv и frontend/node_modules
	python3 -m venv backend/.venv
	backend/.venv/bin/pip install -r backend/requirements-dev.txt
	cd frontend && npm ci

test:          ## тесты бэкенда (локально, SQLite)
	cd backend && $(PY) -m pytest -q

lint:          ## ruff (линтер + формат) по backend/ и scripts/, проверка типов TypeScript
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .
	cd frontend && npx tsc --noEmit

fmt:           ## автоформатирование Python
	$(PY) -m ruff check --fix .
	$(PY) -m ruff format .

build:         ## проверить сборку образа
	docker build -t ostrovok-hotel-knowledge .

dev:           ## локальная разработка: API на :8080 (SQLite) + Vite на :5173
	cd backend && WORKER_ENABLED=true $(PY) -m uvicorn app.main:app --reload --port 8080 &
	cd frontend && npm run dev

eval:          ## метрики качества на размеченных выборках
	$(PY) scripts/evaluate.py

data:          ## пересобрать данные региона из сырых источников (нужна сеть, ~40 мин)
	sh scripts/fetch_data.sh

precompute:    ## заново прогнать агентов по всем отелям → data/seed/seed.json.gz (~10 мин)
	$(PY) scripts/precompute.py
