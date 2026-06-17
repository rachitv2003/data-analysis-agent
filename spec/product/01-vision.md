# Vision

## What This Agent Does

The Data Analysis Agent lets users upload a CSV file through a browser interface and ask questions about their data in plain English. A LangGraph ReAct loop powered by Google Gemini reasons over the data using pandas operations, iterating until it can produce a confident natural-language answer. Users get accurate, explainable answers without needing to write code or SQL.

## Who Uses It

Data analysts, product managers, and non-technical stakeholders who have structured data in CSV files and need quick answers without writing code.

## Core Problem Being Solved

Answering ad-hoc questions about a CSV dataset today requires either writing Python/pandas or uploading to a BI tool and building a chart. This agent eliminates that friction: upload once, ask questions in plain English, get answers immediately.

## Success Criteria

- [ ] A user can upload a CSV file via the browser in under 5 seconds
- [ ] A user can type a natural language question and receive a correct text answer in under 30 seconds
- [ ] The ReAct loop correctly answers aggregation, filter, and comparison questions on datasets up to 100 MB
- [ ] The app runs fully offline (except for Gemini API calls) with a single `uv run` command
- [ ] Stub mode shows a visible banner and returns plausible stub answers without any API key

## What This Agent Does NOT Do (Out of Scope)

- No visual charts or dashboards (deferred to Phase 3)
- No proactive insights or automatic data profiling (deferred to Phase 4)
- No multi-turn conversation history (deferred to Phase 3)
- No authentication or multi-user support
- No database querying (CSV only)

## Key Constraints

- Must use Google Gemini (`gemini-2.5-flash`) as the LLM provider
- Stack: Python 3.12+, FastAPI, SQLite, LangGraph, SQLAlchemy 2.0
- All commands use `uv run` prefix
- Dev port: 8001
- Stub mode must be automatic when `GEMINI_API_KEY` is not set

## Phases of Development

| Phase | Description | Success Gate |
|-------|-------------|--------------|
| 1 | Domain models + SQLite schema + alembic migration | `uv run pytest tests/unit/` — 100% pass |
| 2 | ReAct agent loop (stubbed) + FastAPI routes + Jinja2 UI | `uv run pytest` — all tests pass; golden-path smoke test green |
| 3 | Live Gemini integration + multi-turn conversation | End-to-end test with real Gemini key |
| 4 | Charts/dashboards + proactive insights | Visual smoke test |
