.PHONY: up down logs seed reset test lint build data precompute eval dev

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

test:          ## тесты бэкенда (локально, SQLite)
	cd backend && python -m pytest -q

lint:
	cd backend && ruff check app tests
	cd frontend && npx tsc --noEmit

build:         ## проверить сборку образа
	docker build -t ostrovok-hotel-knowledge .

dev:           ## локальная разработка: API на :8080 (SQLite) + Vite на :5173
	cd backend && WORKER_ENABLED=true uvicorn app.main:app --reload --port 8080 &
	cd frontend && npm run dev

eval:          ## метрики качества на размеченных выборках
	python scripts/evaluate.py

data:          ## пересобрать данные региона из сырых источников (нужна сеть, ~40 мин)
	sh scripts/fetch_data.sh

precompute:    ## заново прогнать агентов по всем отелям → data/seed/seed.json.gz (~10 мин)
	python scripts/precompute.py
