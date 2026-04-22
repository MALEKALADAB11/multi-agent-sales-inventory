.PHONY: up down migrate seed simulate test

up:
	docker-compose up -d
	@echo "✅ All services started"

down:
	docker-compose down

migrate:
	alembic upgrade head
	@echo "✅ Migrations applied"

seed:
	python data/seeds/seed_stores.py
	@echo "✅ Seed complete"

simulate:
	python data/seeds/seed_pos_simulator.py
	@echo "✅ POS transactions simulated"

dev:
	uvicorn main:app --reload --port 8000

test:
	pytest tests/ -v --cov=. --cov-report=term-missing

step0:
	make up
	sleep 10
	make migrate
	make seed
	@echo "✅ STEP 0.1 complete — infrastructure ready"

step1:
	make simulate
	@echo "✅ STEP 1.1 — POS data flowing via Kafka"

step2:
	@echo "✅ STEP 2.1 — APP02 Analyste ready — test: curl localhost:8000/api/v1/forecast/eod/store-lac2"