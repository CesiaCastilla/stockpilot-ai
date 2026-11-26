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

Start a run with `POST /api/v1/agent/runs` and follow it with `GET /api/v1/agent/runs/{id}`. Every transfer the agent proposes goes through the validator and waits in `/api/v1/transfer-orders?status=pending` until a person approves or rejects it. Meanwhile the run is `waiting`, saved by LangGraph's PostgreSQL checkpointer, so it survives a restart; when its last order is decided it resumes, and for a rejected one it may propose a replacement, once.

To run without a key, use a local model with [Ollama](https://ollama.com) that supports tool calling: set `LLM_PROVIDER=ollama` and `LLM_MODEL` (for example `qwen3:8b`). Tests never call a real model.

## Daily detection

Every morning at 06:00 (Buenos Aires time), a job measures coverage and aging across the network and, if a transfer can fix some shortage, starts the agent. Its proposals wait for people like any other. On a morning with nothing to fix, it does not call the model at all.

```bash
uv run python -m stockpilot.workflows.daily_detection   # run it once, now
uv run python -m stockpilot.workflows.scheduler         # run it every day at DAILY_DETECTION_AT
```

The scheduler is a process of its own, not part of the API: every API worker would run the job again. The job skips a day when another agent run is in progress, and first marks as failed any run left `running` by a process that stopped (`AGENT_RUN_ABANDONED_AFTER_MINUTES`). Its exit code is 1 when there is no AI model or the run failed, so any scheduler can tell.

## Agent evals

`tests/evals/scenarios.yaml` holds 19 business scenarios: a dealer sold out with aged units elsewhere, a full yard, the only unit reserved, a shortage with no unit anywhere, a rejected transfer that needs a replacement, and more. Each one declares what the run must achieve, not the exact text, and every run is also checked for invented VINs, wrong trims or destinations, more units than needed, older units left behind and false numbers in the reasons.

```bash
uv run pytest -m eval              # all scenarios with the real model (about 4 minutes)
uv run pytest -m eval -k oldest    # some of them
EVAL_PAUSE_SECONDS=5 uv run pytest -m eval   # slower, for a low per-minute quota
```

They use the real model, so they never run in CI or with a plain `uv run pytest`. Each run writes a report to `tests/evals/reports/`, with the prompt version, to compare prompts and models. The scenarios and checks are tested themselves in CI, against a hand-written reference plan and deliberately wrong ones.

## Development

```bash
uv sync                                        # install dependencies
uv run pytest                                  # all tests (integration ones need Docker)
uv run pytest -m "not integration"             # unit tests only, no Docker
uv run ruff check . && uv run ruff format --check .
uv run mypy src tests scripts
```

CI (GitHub Actions) runs Ruff, mypy and pytest with coverage on every push to `main` and every pull request.
