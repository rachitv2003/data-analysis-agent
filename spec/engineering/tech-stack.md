# Tech Stack

## Language

**Python 3.12+**

**Why:** User requested Python. Best ecosystem for data analysis (pandas), FastAPI, and LangGraph.

## Agent Framework

**LangGraph** (StateGraph — ReAct loop)

**Why:** Native ReAct loop support, clean state management, required by spec Rule 9 for tool-using agents.

## LLM Provider

**Google Gemini**

**Model:** `gemini-2.5-flash` (configurable via `DATA_ANALYST_LLM_MODEL`)

**Why:** User has a Gemini API key. `gemini-2.5-flash` is the current safe default as of 2026 (see tech-stack rule).

## Backend Framework

**FastAPI** — async HTTP server, file upload, Jinja2 templating, auto-generated OpenAPI docs.

## Database

**SQLite** via SQLAlchemy 2.0 declarative ORM.

**Why:** User requested SQLite. Zero configuration, file-based, single-user workload.

**ORM:** SQLAlchemy 2.0 (Mapped types, `DeclarativeBase`)

## Frontend

**Jinja2** server-rendered HTML templates. No JS framework or build step for v0.1.

## Key Libraries

| Library | Version | Purpose |
|---------|---------|---------|
| fastapi | >=0.115 | HTTP server + routing |
| uvicorn | >=0.30 | ASGI server |
| jinja2 | >=3.1 | Server-rendered HTML templates |
| python-multipart | >=0.0.9 | File upload parsing |
| sqlalchemy | >=2.0 | ORM + SQLite driver |
| alembic | >=1.13 | Database migrations |
| pydantic-settings | >=2.0 | Settings from env vars |
| langgraph | >=0.2 | ReAct agent orchestration |
| google-generativeai | >=0.8 | Gemini API client |
| pandas | >=2.0 | CSV loading + data operations |
| structlog | >=24.0 | Structured logging |

## What to Avoid

- No SQLite → PostgreSQL migration in v0.1 (out of scope)
- No async pandas (synchronous is fine for single-user)
- No React/Next.js frontend (Jinja2 is sufficient for v0.1)
- No LangChain (use LangGraph + google-generativeai directly)

## Dependency Management

`uv` + `pyproject.toml`

## Permanent Rules

- Default dev port: **8001** (not 8000)
- All commands prefixed with `uv run`
- Model name configurable via env var `DATA_ANALYST_LLM_MODEL`
- `provider=auto`: real Gemini when `GEMINI_API_KEY` set, stub otherwise
