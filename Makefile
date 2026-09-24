.PHONY: install test test-live test-running run up down

install:  ## Install runtime + test dependencies
	pip install -r requirements-dev.txt

test:  ## Full suite in-process, deterministic mock LLM (what CI runs)
	pytest

test-live:  ## Replay the conversation fixtures against real Claude (needs ANTHROPIC_API_KEY)
	LLM_PROVIDER=anthropic pytest -m conversation -v

test-running:  ## Replay the fixtures against an already running service (e.g. docker compose)
	AGENT_BASE_URL=http://localhost:8000 pytest tests/test_conversations.py tests/test_api.py -v

run:  ## Run the API locally on SQLite
	uvicorn --factory app.main:create_app --reload

up:  ## Start API + Postgres + Prometheus
	docker compose up --build -d

down:
	docker compose down
