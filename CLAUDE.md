# CLAUDE.md

Guía para Claude Code en este repositorio.

## Proyecto

StockPilot es un backend en Python con un agente de IA que controla el stock de unidades 0 km en la red de concesionarios de una automotriz. Detecta concesionarios con faltantes y unidades con mucho tiempo en playa (aging), propone transferencias o asignaciones, las valida con reglas deterministas y las deja pendientes hasta que una persona las aprueba por API.

Principio central: **la IA propone, el código valida y una persona decide.**

Es un proyecto de portfolio para postular a una pasantía de Desarrollo Backend + IA. La autora viene de QA Automation (más de 5 años) y está dando el salto a desarrollo backend.

## Propuesta completa

Todas las decisiones de alcance, arquitectura, stack, testing y roadmap están en el documento de la propuesta:
https://claude.ai/artifact/XBpKidW3WkfdBCRa7BKkWt

Leelo con las herramientas de Claude Docs antes de planificar una fase nueva. Si algo de este archivo contradice el documento, preguntá cuál vale.

## Cómo trabajar con la autora

- Conversación en español. Código, nombres, docstrings y README en inglés.
- Explicá el porqué de cada decisión técnica: el objetivo también es aprender y poder defender el código en una entrevista.
- Trabajá en pasos chicos y revisables, una funcionalidad por vez.
- No hagas commit, push ni crees ramas o PRs sin que la autora lo pida.
- Al cerrar una funcionalidad hecha con ayuda de IA, proponé la entrada correspondiente para `AI_USAGE.md`: qué se delegó, cómo se validó y qué errores se corrigieron.

## Stack (decidido)

- Python 3.12, uv con `pyproject.toml`
- FastAPI + Uvicorn, Pydantic v2, pydantic-settings
- PostgreSQL 16, SQLAlchemy 2.0 (modelos tipados con `Mapped`), Alembic
- LangGraph sobre LangChain, con checkpointer de PostgreSQL
- Modelo de IA: Gemini (capa gratuita de Google AI Studio) como principal y Ollama como alternativa local. Se elige con `LLM_PROVIDER` y `LLM_MODEL`; una sola función crea el modelo con `init_chat_model`.
- httpx + tenacity para clientes externos
- APScheduler para jobs locales; n8n es un extra
- pytest, pytest-cov, respx, Testcontainers, pytest-bdd; colección Postman ejecutada con Newman en CI
- Ruff, mypy, pre-commit, structlog
- Docker + Docker Compose, GitHub Actions; Cloud Run + Cloud SQL como extra

## Arquitectura y reglas

Layout `src/stockpilot/` con capas:

- `api/`: rutas FastAPI. Sin lógica de negocio.
- `services/`: reglas de negocio (cobertura, aging, validador de órdenes).
- `domain/`: VIN, estados de la unidad, errores de dominio. Sin dependencias de frameworks.
- `repositories/`: único lugar que habla con la base.
- `models/` (ORM) y `schemas/` (Pydantic) separados.
- `integrations/`: un cliente por API externa (NHTSA vPIC, Georef, DolarApi, ArgentinaDatos), con timeout, reintentos y error de dominio propio.
- `agents/`: grafo LangGraph, herramientas y prompts.
- `workflows/`: jobs programados.
- `core/`: configuración y logs.

Reglas:

- Las herramientas del agente llaman a servicios o repositorios ya testeados; el agente nunca escribe SQL libre.
- Toda propuesta del agente pasa por el validador antes de convertirse en una orden pendiente.
- Las dependencias se inyectan (FastAPI `Depends`), para poder reemplazarlas en los tests.
- Tipado estricto: el código nuevo tiene que pasar mypy.

## Testing

- Los tests no usan red ni el modelo real: APIs externas con respx y el LLM con `FakeChatModel` de LangChain.
- Integración con PostgreSQL real vía Testcontainers.
- Las evals del agente con el modelo real llevan la marca `eval` y no corren en CI.
- Meta de cobertura: 80% o más en servicios y validadores.
- Cada funcionalidad nueva viene con sus tests en el mismo cambio.

## Seguridad y confidencialidad

- Ninguna clave en el código ni en Git: todo por variables de entorno, documentado en `.env.example`.
- Solo datos ficticios generados por `scripts/seed.py`. La capa gratuita de Gemini puede usar lo que recibe.
- No usar marcas ni logos de automotrices reales, ni datos o detalles del empleo actual o anterior de la autora. El dominio está tomado como referencia de su última experiencia laboral, sin nombrar al cliente.

## Git

- Rama principal: `main`. Ramas por funcionalidad y PR hacia `main`.
- Conventional Commits: `feat:`, `fix:`, `test:`, `docs:`, `refactor:`, `chore:`, `ci:`.
- CI (Ruff, mypy, pytest) tiene que pasar antes de mergear.

## Comandos

```bash
uv sync                                   # instalar dependencias
docker compose up -d db                   # levantar PostgreSQL 16
uv run alembic upgrade head               # aplicar migraciones
uv run python scripts/seed.py             # red ficticia: 30 concesionarios, 2.000 unidades, 180 días de ventas (--reset para regenerar)
uv run python -m stockpilot.workflows.sync_dealer_locations  # coordenadas y nombres oficiales de concesionarios (Georef)
uv run alembic revision --autogenerate -m "describe change"  # nueva migración (revisarla siempre)
uv run fastapi dev src/stockpilot/main.py # API en :8000 (/health, /docs)
uv run pytest                             # tests (sin evals); los de integración necesitan Docker
uv run pytest -m "not integration"        # solo unitarios, sin Docker
uv run pytest --cov                       # con cobertura (mínimo 80%, como en CI)
uv run ruff check . && uv run ruff format --check .
uv run mypy src tests scripts
```

Actualizá esta sección cuando cambien los comandos.

## Estado del roadmap

1. Base: estructura, uv, Docker, CI, modelos y Alembic, datos de prueba. **Hecha.**
2. API y negocio: CRUD, estados de la unidad y VIN, cobertura y aging, tests. **Hecha.**
3. Integraciones: vPIC, Georef, DolarApi, feriados (ArgentinaDatos), con mocks. **Hecha.**
4. Agente IA: grafo, herramientas, validador, aprobación humana, evals y job diario de detección. Hito: MVP. **En curso.**
5. Automatización y presentación: demás jobs, runbooks, README final, Postman + Newman, Cloud Run (extra).

Actualizá este estado al cerrar cada fase.
