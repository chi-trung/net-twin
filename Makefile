.PHONY: up down logs ps backend-dev frontend-dev test lint build clean

## ── full stack (Docker) ─────────────────────────────────
up:            ## build & start everything
	docker compose up --build -d

down:          ## stop everything
	docker compose down

logs:          ## tail all service logs
	docker compose logs -f --tail=100

ps:            ## show service status
	docker compose ps

## ── local development ───────────────────────────────────
backend-dev:   ## run backend with hot reload (needs .venv + local infra)
	cd backend && uvicorn app.main:app --reload --port 8000

frontend-dev:  ## run vite dev server (proxies /api and /ws to :8000)
	cd frontend && npm run dev

## ── quality gates ───────────────────────────────────────
test:          ## run backend test suite
	cd backend && python -m pytest -q

lint:          ## lint backend (ruff) and frontend (eslint)
	cd backend && python -m ruff check app tests
	cd frontend && npm run lint

build:         ## production build of the frontend
	cd frontend && npm run build

clean:         ## remove containers, volumes and local caches
	docker compose down -v
	rm -rf backend/.pytest_cache backend/.ruff_cache frontend/dist
