.PHONY: up down logs migrate test

up:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f --tail=150

migrate:
	docker compose run --rm api alembic upgrade head

test:
	docker compose run --rm api pytest -q
