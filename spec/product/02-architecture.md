# Architecture

## System Overview

```text
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
  ├── DELETE /sessions/{id}               → delete a single session and its runs
  ├── DELETE /sessions                    → delete all sessions and their runs
  ├── PATCH /sessions/{id}/name           → rename a session
  ├── GET  /runs/current                  → most-recent run's status + iteration_count (progress polling)
  ├── GET  /stats/daily                   → aggregated token usage for today (server local time)
  ├── GET  /memory                        → read global persistent memory
  ├── PATCH /memory                       → replace global persistent memory; triggers C31 compression
  ├── GET  /datasets/{id}                 → single dataset metadata + column schema (used by Database tab)
  ├── POST /datasets/{id}/describe        → C30: trigger on-demand notes generation for a dataset
  ├── POST /datasets/{id}/re-derive       → C25: re-execute derivation code against current parents
  ├── POST /datasets/{id}/clean           → C24: preview NL cleaning operation (LLM generates code)
  └── POST /datasets/{id}/clean/apply     → C24: apply cleaning code in-place; updates CSV + Parquet
  │
  ▼
Pre-flight: clarification check (C26)
  └── check_clarification() — one-shot LLM call; returns clarification question or proceeds; skipped when explicit IDs supplied
  │
  ▼
Pre-flight: dataset selector (C19)
  └── select_datasets() — one-shot LLM call to pick relevant datasets; skipped when explicit IDs supplied
  │
  ▼
LangGraph ReAct Agent (StateGraph)
  ├── setup          → checks session DataFrame cache (C27); loads from Parquet/CSV on miss; caches by session_id
  ├── plan_action    → builds prompt (schema + context + history), calls LLM, stores llm_response
  ├── execute_action → evals Python expression; captures Plotly figures as JSON; writes iteration_count to DB
  ├── finalize       → strips FINAL ANSWER prefix, appends chart divs, persists to DB, clears cache
  ├── force_finalize → fires on max-iter or 3 consecutive errors; one synthesis LLM call; status=completed
  └── handle_error   → fires on fatal errors; status=failed
  │
  ▼
SQLite (data_analyst.db)
  ├── datasets             → id, filename, file_path, row_count, col_count, columns_json,
  │                          content_hash, format, context, origin, derived_from_run_id,
  │                          derived_from_dataset_ids, derivation_code, parquet_path,
  │                          auto_notes_status, context_facts, created_at, updated_at
  ├── query_runs           → id, dataset_id, session_id, question, answer, status, error_message,
  │                          action_history, iteration_count, tokens_input, tokens_output,
  │                          prompt_breakdown, dataset_ids_json, selector_reasoning,
  │                          created_at, updated_at
  ├── conversation_sessions → id, dataset_id, dataset_ids_json, name, created_at, updated_at
  └── settings             → key, value, updated_at  (global_memory, global_memory_facts, llm_model)
```

## Layers

| Layer | Responsibility |
| ----- | -------------- |
| FastAPI routes | HTTP handling, multipart file upload, JSON request/response, error envelopes |
| Pre-flight selector | One-shot LLM call that picks which datasets to load; runs before the graph |
| LangGraph StateGraph | ReAct loop orchestration (setup → plan → execute → loop → finalize) |
| Graph-adjacent LLM helpers | `graph/describe.py` (C30 auto-notes), `graph/compress.py` (C31 fact extraction), and `generate_suggestions()` in `graph/nodes.py` (follow-up questions) — single LLM calls that run outside the StateGraph |
| LLM providers | `GeminiProvider`, `OpenRouterProvider`, `StubLLMProvider` — uniform `complete(prompt) → LLMResponse` |
| pandas sandbox | `eval`/`exec` namespace with DataFrames, pd, px, go, plt |
| SQLAlchemy / SQLite | Persist dataset metadata, session state, run results |
| Jinja2 templates | Server-rendered HTML — no JS build step |

## Data Flow

1. **Upload:** Browser submits `multipart/form-data` → FastAPI computes SHA-256, checks duplicates, calls `parse_file`, saves CSV to `uploads/{id}.csv`, writes Parquet to `uploads/{id}.parquet` (C27), creates `DatasetRow`.
2. **Ask:** Browser posts `{question, session_id?}` → `ask.py` runs C26 clarification check (returns early if ambiguous) → resolves datasets (explicit IDs or C19 auto-select) → creates `QueryRunRow(status="running")` → calls `run_agent`.
3. **Dataset selection (C19):** If no `dataset_ids` supplied, `select_datasets()` sends one LLM call with all dataset schemas; returns subset of IDs to load. Falls back to all datasets on failure.
4. **Agent setup (C27):** `setup` node checks `_session_cache[session_id]` for each dataset; on hit, uses cached DataFrame; on miss, reads from Parquet (`pd.read_parquet`) with CSV fallback, stores in session cache. Single-turn queries (no session_id) use run-scoped `_dataframes[run_id]` as before.
5. **ReAct loop:** `plan_action` builds prompt → LLM → `execute_action` evals code → appends `{action, result, is_error}` to `action_history` → back to `plan_action`. Iteration count is written to DB on each execute so `GET /runs/current` reflects live progress.
6. **Termination:** LLM emits `FINAL ANSWER: ...` → `finalize` strips prefix, appends chart divs, persists answer. Or: max iterations / 3 consecutive errors → `force_finalize` makes one synthesis LLM call and persists.
7. **Response:** `ask.py` reads the completed `QueryRunRow` and returns `{type, run_id, session_id, dataset_ids, derived_dataset_ids, datasets_used, selector_reasoning, answer_markdown, answer_html, iteration_count, tokens_input, tokens_output, status, is_best_effort, steps, suggested_questions, prompt_breakdown}`. `type` is `"answer"` on the normal path (`"clarification"` when the C26 pre-flight returns early). `derived_dataset_ids` are datasets materialised by `save_dataset()` during this run (C25); `suggested_questions` are three LLM-generated follow-ups (see step 7a); `prompt_breakdown` is the per-section token accounting persisted on the run.
7a. **Follow-up suggestions:** After the answer is read, `ask.py` calls `generate_suggestions(question, answer)` (in `graph/nodes.py`) — one LLM call that returns up to three short follow-up questions as a JSON array. Failures are swallowed (returns `[]`). Its token cost is added to the run totals before responding.
8. **Progress polling (C22):** While step 5 runs, the browser polls `GET /runs/current` ~1×/s to update the elapsed timer, step counter, and progress bar.

## Graph-Adjacent LLM Helpers

These modules live next to the StateGraph but run as standalone LLM calls, not graph nodes:

| Module | Capability | Trigger | Behaviour |
| ------ | ---------- | ------- | --------- |
| `graph/describe.py` | C30 auto-notes | Background task after upload, or `POST /datasets/{id}/describe` | `generate_dataset_notes()` loads a 50-row sample, asks the LLM for plain-text notes (max ~300 words), writes them to `DatasetRow.context`, tracks `auto_notes_status`, then triggers C31 compression. Skips generation if notes already exist and `overwrite=False`. |
| `graph/compress.py` | C31 semantic compression | After notes are written (C30) and on `PATCH /memory` | `compress_dataset_context(dataset_id)` extracts key facts from `DatasetRow.context` → `context_facts`; `compress_memory()` extracts facts from `global_memory` → `global_memory_facts`. `extract_facts()` is one LLM call returning a JSON array (≤20 facts); failures return `[]`. The `*_async` variants fire-and-forget on a daemon thread for the lazy on-read self-heal in the agent setup path; an in-flight lock dedupes concurrent compression of the same target. |
| `graph/nodes.py` → `generate_suggestions()` | Follow-up question suggestions | Called by `ask.py` after each answer (step 7a) | One LLM call returning up to three short follow-up questions as a JSON array; failures return `[]`. |

## Agent Sandbox

`execute_action` evaluates expressions in a Python `eval`/`exec` namespace:

| Name | Value |
| ---- | ----- |
| `df` | First loaded DataFrame (alias) |
| `df1`, `df2`, … | Per-dataset positional aliases |
| `<filename_stem>` | Filename-derived variable name (e.g. `sales_data`) |
| `pd` | `pandas` module |
| `np` | `numpy` (if installed) |
| `px` | `plotly.express` (if installed) |
| `go` | `plotly.graph_objects` (if installed) |
| `plt` | `matplotlib.pyplot` (if installed) |
| `sns` | `seaborn` (if installed) |
| `scipy` | `scipy` module (if installed) |
| `stats` | `scipy.stats` (if installed) |
| `sklearn` | `sklearn` top-level (if installed; import submodules as needed) |
| `sm` | `statsmodels.api` (if installed) |
| `save_dataset(df, name, desc)` | C25: materialise a DataFrame as a registered derived dataset; returns confirmation string |

## External Dependencies

| Dependency | Purpose | Failure Mode |
| ---------- | ------- | ------------ |
| Google Gemini API | LLM reasoning | Rate-limited: retries up to 3× with backoff; fatal 5xx → `handle_error` |
| OpenRouter API | Alternative LLM provider | Same retry logic; disabled when key absent |
| Local filesystem | CSV file storage (`uploads/`) | Upload fails with 500; existing datasets unaffected |
| Plotly CDN (`cdn.plot.ly/plotly-2.35.2.min.js`) | Interactive chart JS bundle | Charts render as empty divs; no server-side failure |

## Deployment

Single-process FastAPI app. Start with `uv run python -m data_analyst`. SQLite file at `data_analyst.db`. CSV uploads at `uploads/`. No Docker, no migrations tool — `init_db()` runs `Base.metadata.create_all` on startup.
