# stockpilot-ai

Python backend with an AI agent (LangGraph) that reallocates new vehicle units across a dealer network. FastAPI, PostgreSQL, Gemini and automated tests.

> Personal portfolio project. Not affiliated with any car manufacturer; it uses only public information and fictitious data.

*Resumen en español: backend en Python con un agente de IA que propone transferencias de unidades 0 km entre concesionarios. La IA propone, el código valida y una persona decide.*

## Quickstart

Requirements: [uv](https://docs.astral.sh/uv/) and Docker.

```bash
cp .env.example .env
docker compose up -d db
uv run fastapi dev src/stockpilot/main.py
```

Then open http://localhost:8000/health (API and database status) or http://localhost:8000/docs (OpenAPI).

## Development

```bash
uv sync                                        # install dependencies
uv run pytest                                  # all tests (integration ones need Docker)
uv run pytest -m "not integration"             # unit tests only, no Docker
uv run ruff check . && uv run ruff format --check .
uv run mypy src tests
```

CI (GitHub Actions) runs Ruff, mypy and pytest with coverage on every push to `main` and every pull request.
