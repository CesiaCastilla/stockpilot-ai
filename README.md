# stockpilot-ai

Python backend with an AI agent (LangGraph) that reallocates new vehicle units across a dealer network. FastAPI, PostgreSQL, Gemini and automated tests.

> Personal portfolio project. Not affiliated with any car manufacturer; it uses only public information and fictitious data.

*Resumen en español: backend en Python con un agente de IA que propone transferencias de unidades 0 km entre concesionarios. La IA propone, el código valida y una persona decide.*

## Quickstart

Requirements: [uv](https://docs.astral.sh/uv/) and Docker.

```bash
cp .env.example .env
docker compose up -d db
uv run alembic upgrade head
uv run python scripts/seed.py   # fictitious dealer network
uv run fastapi dev src/stockpilot/main.py
```

Then open http://localhost:8000/health (API and database status) or http://localhost:8000/docs (OpenAPI).

## AI model

The agent uses Gemini by default. Create a free API key in [Google AI Studio](https://aistudio.google.com/apikey), put it in `.env` as `GOOGLE_API_KEY` (never in Git), and check it:

```bash
uv run python scripts/check_llm.py   # one request: prints the tool the model asked for
```

To run without a key, use a local model with [Ollama](https://ollama.com) that supports tool calling: set `LLM_PROVIDER=ollama` and `LLM_MODEL` (for example `qwen3:8b`). Tests never call a real model.

## Development

```bash
uv sync                                        # install dependencies
uv run pytest                                  # all tests (integration ones need Docker)
uv run pytest -m "not integration"             # unit tests only, no Docker
uv run ruff check . && uv run ruff format --check .
uv run mypy src tests scripts
```

CI (GitHub Actions) runs Ruff, mypy and pytest with coverage on every push to `main` and every pull request.
