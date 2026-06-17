# Data Analysis Agent

Upload a CSV, ask questions in plain English, get answers — powered by Google Gemini and a LangGraph ReAct loop.

> **All commands run from the repo root.**

---

## Quick Start

### 1. Install dependencies

```
# repo root
python -m uv sync
```

### 2. Configure environment

```
# repo root
cp .env.example .env
```

Edit `.env` and set `DATA_ANALYST_GEMINI_API_KEY` to your Gemini API key.  
Leave it blank to run in **stub mode** (a yellow banner appears in the UI, answers are pre-canned).

### 3. Apply database migrations

```
# repo root
python -m uv run alembic upgrade head
python -m uv run alembic current
```

`alembic current` must show a revision hash — blank output means the migration was not applied.

### 4. Run the app

```
# repo root
python -m uv run python -m data_analyst
```

Open your browser at **http://localhost:8001**

---

## How it Works

1. **Upload a CSV** — the file is saved to `uploads/` and metadata (rows, columns) is stored in SQLite
2. **Ask a question** — a LangGraph ReAct loop runs:
   - Gemini reasons and generates a pandas expression
   - The expression is executed against your DataFrame
   - The result is fed back to Gemini iteratively
   - When Gemini emits `FINAL ANSWER: <text>`, the answer is returned
3. **Read the answer** — plain-text answer displayed in the browser

---

## Stub Mode

When `DATA_ANALYST_GEMINI_API_KEY` is not set, the app runs in stub mode:
- A yellow banner is shown on every page
- The agent runs a `df.describe()` call and returns a canned answer
- All other functionality (upload, dataset listing) works normally

To switch to real answers: add your Gemini API key to `.env` and restart the app.

---

## Running Tests

```
# repo root
python -m uv run pytest
```

All 14 tests must pass. Tests use SQLite in-memory and the stub LLM — no API key required.

```
# Unit tests only
python -m uv run pytest tests/unit/

# Integration tests only
python -m uv run pytest tests/integration/
```

---

## Project Layout

```
src/data_analyst/
  api/          ← FastAPI routes (upload, ask, datasets, health, UI)
  config/       ← Settings via pydantic-settings (env prefix: DATA_ANALYST_)
  db/           ← SQLAlchemy models + session management
  domain/       ← Pydantic domain models (Dataset, QueryRun)
  graph/        ← LangGraph ReAct agent (state, nodes, edges, runner)
  llm/          ← LLM provider abstraction (Gemini + stub)
  templates/    ← Jinja2 HTML templates
  observability/← structlog configuration
tests/
  unit/         ← Config, DB model, domain model tests
  integration/  ← Golden-path smoke tests (upload → ask → answer)
alembic/        ← Database migrations
spec/           ← Product and engineering spec
reports/        ← Session logs
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATA_ANALYST_DATABASE_URL` | `sqlite:///data_analyst.db` | SQLite database path |
| `DATA_ANALYST_GEMINI_API_KEY` | *(empty)* | Gemini API key — leave blank for stub mode |
| `DATA_ANALYST_LLM_MODEL` | `gemini-2.5-flash` | Gemini model name |
| `DATA_ANALYST_MAX_ITERATIONS` | `10` | Max ReAct loop iterations |
| `DATA_ANALYST_LOG_LEVEL` | `INFO` | Logging level |
| `PORT` | `8001` | HTTP server port |

---

## What's Deferred (Future Phases)

- Visual charts and dashboards
- Multi-turn conversation history
- Proactive data insights / auto-profiling
- Multi-user support / authentication
