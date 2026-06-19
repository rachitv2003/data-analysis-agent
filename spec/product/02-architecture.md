# Architecture

## System Overview

```
Browser
  │
  ▼
FastAPI (port 8001)
  ├── GET  /                              → serves main UI (Jinja2)
  ├── GET  /health                        → liveness check
  ├── POST /upload                        → parse file, duplicate check, save CSV, create DatasetRow
  ├── GET  /datasets                      → list all datasets
  ├── DELETE /datasets/{id}               → delete dataset + cascade sessions/runs/CSV file
  ├── DELETE /datasets                    → delete all datasets + cascade
  ├── PATCH /datasets/{id}/context        → update dataset context notes
  ├── GET  /datasets/{id}/sessions        → sessions for a specific dataset
  ├── POST /ask                           → auto-select datasets, create run, invoke agent, return answer
  ├── GET  /sessions                      → list all sessions (global, most-recently-updated first)
  ├── GET  /sessions/{id}                 → turns for a session
  ├── GET  /runs/current                  → most-recent run's status + iteration_count (progress polling)
  └── GET  /stats/daily                   → aggregated token usage for today (UTC)
  │
  ▼
Pre-flight: dataset selector
  └── select_datasets() — one-shot LLM call to pick relevant datasets; skipped when explicit IDs supplied
  │
  ▼
LangGraph ReAct Agent (StateGraph)
  ├── setup          → loads selected DataFrames from disk into module-level cache keyed by run_id
  ├── plan_action    → builds prompt (schema + context + history), calls LLM, stores llm_response
  ├── execute_action → evals Python expression; captures Plotly figures as JSON; writes iteration_count to DB
  ├── finalize       → strips FINAL ANSWER prefix, appends chart divs, persists to DB, clears cache
  ├── force_finalize → fires on max-iter or 3 consecutive errors; one synthesis LLM call; status=completed
  └── handle_error   → fires on fatal errors; status=failed
  │
  ▼
SQLite (data_analyst.db)
  ├── datasets             → id, filename, file_path, row_count, col_count, columns_json,
  │                          content_hash, format, context, created_at
  ├── query_runs           → id, dataset_id, session_id, question, answer, status, error_message,
  │                          action_history, iteration_count, tokens_input, tokens_output,
  │                          dataset_ids_json, selector_reasoning, created_at, updated_at
  └── conversation_sessions → id, dataset_id, dataset_ids_json, created_at, updated_at
```

## Layers

| Layer | Responsibility |
|-------|----------------|
| FastAPI routes | HTTP handling, multipart file upload, JSON request/response, error envelopes |
| Pre-flight selector | One-shot LLM call that picks which datasets to load; runs before the graph |
| LangGraph StateGraph | ReAct loop orchestration (setup → plan → execute → loop → finalize) |
| LLM providers | `GeminiProvider`, `OpenRouterProvider`, `StubLLMProvider` — uniform `complete(prompt) → LLMResponse` |
| pandas sandbox | `eval`/`exec` namespace with DataFrames, pd, px, go, plt |
| SQLAlchemy / SQLite | Persist dataset metadata, session state, run results |
| Jinja2 templates | Server-rendered HTML — no JS build step |

## Data Flow

1. **Upload:** Browser submits `multipart/form-data` → FastAPI computes SHA-256, checks duplicates, calls `parse_file`, saves CSV to `uploads/{id}.csv`, creates `DatasetRow`.
2. **Ask:** Browser posts `{question, session_id?}` → `ask.py` resolves datasets (explicit IDs or C19 auto-select) → creates `QueryRunRow(status="running")` → calls `run_agent`.
3. **Dataset selection (C19):** If no `dataset_ids` supplied, `select_datasets()` sends one LLM call with all dataset schemas; returns subset of IDs to load. Falls back to all datasets on failure.
4. **Agent setup:** `setup` node reads each CSV from disk via `pd.read_csv`, stores DataFrames in `_dataframes[run_id]`, combines dataset context strings.
5. **ReAct loop:** `plan_action` builds prompt → LLM → `execute_action` evals code → appends `{action, result, is_error}` to `action_history` → back to `plan_action`. Iteration count is written to DB on each execute so `GET /runs/current` reflects live progress.
6. **Termination:** LLM emits `FINAL ANSWER: ...` → `finalize` strips prefix, appends chart divs, persists answer. Or: max iterations / 3 consecutive errors → `force_finalize` makes one synthesis LLM call and persists.
7. **Response:** `ask.py` reads the completed `QueryRunRow` and returns `{run_id, session_id, dataset_ids, datasets_used, selector_reasoning, answer_markdown, answer_html, iteration_count, tokens_input, tokens_output, status, is_best_effort, steps}`.
8. **Progress polling (C22):** While step 5 runs, the browser polls `GET /runs/current` ~1×/s to update the elapsed timer, step counter, and progress bar.

## Agent Sandbox

`execute_action` evaluates expressions in a Python `eval`/`exec` namespace:

| Name | Value |
|------|-------|
| `df` | First loaded DataFrame (alias) |
| `df1`, `df2`, … | Per-dataset positional aliases |
| `<filename_stem>` | Filename-derived variable name (e.g. `sales_data`) |
| `pd` | `pandas` module |
| `px` | `plotly.express` (if installed) |
| `go` | `plotly.graph_objects` (if installed) |
| `plt` | `matplotlib.pyplot` (if installed) |

## External Dependencies

| Dependency | Purpose | Failure Mode |
|------------|---------|--------------|
| Google Gemini API | LLM reasoning | Rate-limited: retries up to 3× with backoff; fatal 5xx → `handle_error` |
| OpenRouter API | Alternative LLM provider | Same retry logic; disabled when key absent |
| Local filesystem | CSV file storage (`uploads/`) | Upload fails with 500; existing datasets unaffected |
| Plotly CDN (`cdn.plot.ly/plotly-2.35.2.min.js`) | Interactive chart JS bundle | Charts render as empty divs; no server-side failure |

## Deployment

Single-process FastAPI app. Start with `uv run python -m data_analyst`. SQLite file at `data_analyst.db`. CSV uploads at `uploads/`. No Docker, no migrations tool — `init_db()` runs `Base.metadata.create_all` on startup.
