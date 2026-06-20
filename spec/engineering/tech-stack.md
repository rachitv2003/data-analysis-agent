# Tech Stack

## Language

**Python 3.12+**

**Why:** User requested Python. Best ecosystem for data analysis (pandas), FastAPI, and LangGraph.

## Agent Framework

**LangGraph** (StateGraph — ReAct loop)

**Why:** Native ReAct loop support, clean state management, required by spec Rule 9 for tool-using agents.

## LLM Provider

**Google Gemini**

**Model:** `gemini-2.0-flash` (configurable via `DATA_ANALYST_LLM_MODEL`)

**Why:** User has a Gemini API key. Tested and working; default can be changed via env var.

## Backend Framework

**FastAPI** — async HTTP server, file upload, Jinja2 templating, auto-generated OpenAPI docs.

## Database

**SQLite** via SQLAlchemy 2.0 declarative ORM.

**Why:** User requested SQLite. Zero configuration, file-based, single-user workload.

**ORM:** SQLAlchemy 2.0 (Mapped types, `DeclarativeBase`)

## Frontend

**Jinja2** server-rendered HTML templates. No JS framework or build step for v0.1.

## Key Libraries

### Core Framework & Backend

| Library | Version | Purpose |
|---------|---------|---------|
| fastapi | >=0.115 | HTTP server + routing |
| uvicorn | >=0.30 | ASGI server; [standard] includes extra workers |
| jinja2 | >=3.1 | Server-rendered HTML templates |
| python-multipart | >=0.0.9 | File upload parsing (multipart/form-data) |
| aiofiles | >=23.0 | Async file I/O (background tasks) |

### Database & ORM

| Library | Version | Purpose |
|---------|---------|---------|
| sqlalchemy | >=2.0 | ORM + SQLite driver |
| alembic | >=1.13 | Database schema migrations |
| pydantic-settings | >=2.0 | Settings from env vars (.env) |

### Agent & LLM

| Library | Version | Purpose |
|---------|---------|---------|
| langgraph | >=0.2 | ReAct agent orchestration; StateGraph |
| google-genai | >=1.0 | Google Gemini API client; fallback to stub |

### Data Processing & Analysis

| Library | Version | Purpose |
|---------|---------|---------|
| pandas | >=2.0 | CSV/Parquet loading, DataFrame operations, agent sandbox |
| numpy | >=2.4.6 | Numerical arrays (agent sandbox, pandas backend) |
| pyarrow | >=17.0 | Parquet file I/O (C27 pre-conversion on upload) |
| openpyxl | >=3.1.5 | Excel file reading (supported in C11 multi-format) |
| xlrd | >=2.0.2 | Legacy Excel file reading fallback |

### Visualization & Charting

| Library | Version | Purpose |
|---------|---------|---------|
| plotly | >=5.0 | Interactive charts (C4; captured as JSON in agent sandbox) |
| matplotlib | >=3.8 | Plotting library (agent sandbox, used by some operations) |
| seaborn | >=0.13.2 | Statistical data visualization (agent sandbox) |

### Statistical & ML Models

| Library | Version | Purpose |
|---------|---------|---------|
| scikit-learn | >=1.9.0 | ML algorithms (agent sandbox; used in agent code) |
| statsmodels | >=0.14.6 | Statistical modeling (agent sandbox; regression, ANOVA, etc.) |

### Utilities

| Library | Version | Purpose |
|---------|---------|---------|
| markdown-it-py | >=3.0 | Markdown → HTML rendering (C6 response rendering) |
| structlog | >=24.0 | Structured logging (JSON + console output) |
| tabulate | >=0.9 | Table formatting (agent sandbox output) |

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
