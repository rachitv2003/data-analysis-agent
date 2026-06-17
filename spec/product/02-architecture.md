# Architecture

## System Overview

```
Browser
  │
  ▼
FastAPI (port 8001)
  ├── POST /upload          → saves CSV to disk, creates DatasetRow in SQLite
  ├── POST /ask             → creates QueryRun, invokes agent, returns answer
  ├── GET  /datasets        → lists uploaded datasets
  └── GET  /                → serves the main UI (Jinja2)
  │
  ▼
LangGraph ReAct Agent
  ├── setup          → loads CSV into pandas DataFrame (keyed by run_id)
  ├── plan_action    → Gemini reasons: which pandas op to run next, or FINAL ANSWER
  ├── execute_action → runs the pandas expression, captures result as string
  ├── handle_error   → appends error to history, routes back to plan_action (retry)
  └── finalize       → saves answer + history to QueryRun; releases DataFrame
  │
  ▼
SQLite (data_analyst.db)
  ├── datasets   → id, filename, file_path, row_count, col_count, columns_json, created_at
  ├── query_runs → id, dataset_id, session_id, question, answer, status, error_message,
  │               action_history, iteration_count, created_at, updated_at
  └── conversation_sessions → id, dataset_id, created_at, updated_at
```

## Layers

| Layer | Responsibility |
|-------|----------------|
| FastAPI routes | HTTP request handling, file upload, response envelope |
| LangGraph graph | ReAct loop orchestration (setup → plan → execute → loop → finalize) |
| LLM provider | Wraps Gemini API (or stub); called only from plan_action node |
| Tools (pandas) | Pure functions that execute pandas expressions against the loaded DataFrame |
| SQLAlchemy / SQLite | Persist dataset metadata and query run results |
| Jinja2 templates | Server-rendered HTML — no JS build step |

## Data Flow

1. **Upload:** User submits CSV → FastAPI saves file to `uploads/` → creates DatasetRow → returns dataset_id
2. **Ask:** User submits {dataset_id, question, session_id?} → FastAPI resolves/creates ConversationSession → creates QueryRun → loads prior Q&A pairs → invokes agent with conversation context
3. **Agent setup:** loads CSV as pandas DataFrame, caches by run_id
4. **ReAct loop:** plan_action → Gemini → pandas expression → execute_action → result appended to history → loop
5. **Termination:** Gemini emits `FINAL ANSWER: <text>` → finalize saves answer → status=completed
6. **Response:** FastAPI returns {run_id, answer, iteration_count, status}

## Agent Sandbox Capabilities

The `execute_action` node evaluates Python expressions in a restricted `eval()` namespace. The following libraries are available in the sandbox:

| Library | Purpose |
|---------|---------|
| `pandas` (as `df`, `df1`, `df2`, …) | Data manipulation — filtering, aggregation, joins |
| `plotly.express` / `plotly.graph_objects` | Interactive chart generation (C4); `fig.to_html()` output embedded in `answer_html` |
| `matplotlib` | Fallback static chart generation (C4); PNG saved to temp path, base64-embedded in `answer_html` |

## External Dependencies

| Dependency | Purpose | Failure Mode |
|------------|---------|--------------|
| Google Gemini API | LLM reasoning for ReAct loop | Falls back to stub provider; request fails with 503 |
| Local filesystem | CSV file storage (`uploads/`) | Upload fails with 500; existing datasets unaffected |
| Plotly CDN (`cdn.plot.ly`) | Loads Plotly JS bundle for interactive charts (C4) | Charts render as empty divs; no server-side failure |

## Deployment Model

Single-process FastAPI app running locally via `uv run python -m data_analyst`. SQLite database file at `data_analyst.db`. Uploads directory at `uploads/`. No external services required beyond Gemini API.
