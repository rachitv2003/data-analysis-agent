# Query and Analysis

**Status:** implemented
**Covers:** C2 (NL Q&A / ReAct loop), C8 (expanded result display), C19 (auto dataset selection), C20 (early exit / force-finalize)

The core capability: accept a natural-language question, run a LangGraph ReAct loop using pandas to answer it, and return the result. The loop handles multi-step reasoning, iterative self-correction, and graceful termination on error or iteration limits.

---

## Ask Endpoint

`POST /ask` — JSON body

```json
{
  "dataset_id": "uuid",        // backward compat; single-dataset shorthand
  "dataset_ids": ["uuid", ...], // explicit multi-dataset; if omitted → C19 auto-select
  "question": "string",
  "session_id": "uuid"         // optional; omit to start a new session
}
```

When neither `dataset_id` nor `dataset_ids` is supplied, C19 auto-selection runs against all uploaded datasets.

Response:
```json
{
  "data": {
    "run_id": "uuid",
    "session_id": "uuid",
    "dataset_ids": ["uuid"],
    "datasets_used": [{"id": "uuid", "filename": "sales.csv"}],
    "selector_reasoning": "string | null",
    "answer_markdown": "...",
    "answer_html": "<p>...</p>",
    "iteration_count": 3,
    "tokens_input": 312,
    "tokens_output": 87,
    "status": "completed | failed",
    "is_best_effort": false,
    "steps": [{"action": "...", "result": "...", "is_error": false}]
  }
}
```

`is_best_effort` is `true` when `error_message` is `"max_iterations"` or `"consecutive_errors"` — the agent ran out of iterations or hit 3 consecutive errors and synthesised a partial answer.

---

## Automatic Dataset Selection (C19)

When no `dataset_ids` are provided, a lightweight pre-flight LLM call inspects all uploaded dataset schemas (filenames, row counts, column names) and returns a JSON array of IDs to load. This runs in `src/data_analyst/graph/selector.py` before the ReAct graph is invoked.

**Behaviour:**
- Single dataset uploaded → selector is skipped; that dataset is always used.
- Multiple datasets → selector sends one non-iterative LLM call with a `<node:select>` tagged prompt.
- If the selector returns an empty array, malformed JSON, or raises an exception → all datasets are loaded (fallback). Fallback is logged at WARN level.
- The raw LLM selector output is stored as `QueryRun.selector_reasoning`.
- When explicit `dataset_ids` are supplied in the request, the selector is skipped entirely.

The full set of session dataset IDs (not the selector's subset) is always passed to `run_agent` for the C14 session constraint check, to prevent false mismatches on follow-up turns.

---

## ReAct Agent Loop

Implemented as a LangGraph `StateGraph` in `src/data_analyst/graph/`. Each turn is a new invocation with `conversation_history` prepopulated from prior completed runs in the session.

### Nodes

**`setup`** — loads each selected dataset's CSV from disk into a `pd.DataFrame`, stores in a module-level dict keyed by `run_id`. Variable names are derived from filenames (`sales_data.csv` → `sales_data`), with `df` / `df1` / `df2` aliases always available. Combines per-dataset context strings into a single `dataset_context` block injected into the prompt.

**`plan_action`** — builds the full prompt (`_build_prompt`) with schema, context, conversation history, and action history; calls the LLM; stores the response as `llm_response`; increments `iteration_count`; accumulates token counts. When `iteration_count >= max_iterations - 2`, appends a wrap-up instruction urging the LLM to produce `FINAL ANSWER` soon.

**`execute_action`** — strips optional markdown fences from `llm_response`, evaluates it via `_exec_code` (preamble via `exec` + final expression via `eval`). Captures result: if a Plotly `BaseFigure` is returned, serialises it to JSON and appends to `state["charts"]`; otherwise converts to string via `_result_to_str`. Appends `{action, result, is_error}` to `action_history`. Writes `iteration_count` to DB mid-run so the progress endpoint reflects live state.

**`finalize`** — strips `FINAL ANSWER:` prefix, appends captured Plotly chart divs to the answer markdown, persists answer + action_history + token counts to `QueryRunRow`, pops DataFrame from cache.

**`force_finalize`** (C20) — fires when `iteration_count >= max_iterations` or last 3 `action_history` entries are all errors. Makes one additional LLM synthesis call (`<node:finalize>` tag) asking for a best-effort summary of work done. Sets `status="completed"` and `error_message="max_iterations"` or `"consecutive_errors"`. Chart divs are appended to this answer as well.

**`handle_error`** — fires on fatal errors (dataset not found, LLM 5xx). Persists error message, sets `status="failed"`.

### Edge Logic

```
setup      → [error]         → handle_error → END
setup      → [ok]            → plan_action
plan_action → [FINAL ANSWER] → finalize      → END
plan_action → [action]       → execute_action
plan_action → [error]        → handle_error  → END
execute_action → [≥3 consec errors] → force_finalize → END
execute_action → [iter >= max]      → force_finalize → END
execute_action → [ok / error]       → plan_action
```

### Termination

`FINAL ANSWER:` prefix (case-insensitive) in `llm_response` → route to `finalize`. Max iterations default: 6 (configurable via `DATA_ANALYST_MAX_ITERATIONS`).

---

## Sandbox Capabilities

`execute_action` evaluates expressions in a Python `eval`/`exec` namespace containing:

| Name | Value |
|------|-------|
| `df` | First loaded DataFrame (alias) |
| `df1`, `df2`, … | Per-dataset aliases |
| `<filename_stem>` | Filename-derived variable (e.g. `sales_data`) |
| `pd` | `pandas` module |
| `np` | `numpy` (if available) |
| `px` | `plotly.express` (if available) |
| `go` | `plotly.graph_objects` (if available) |
| `plt` | `matplotlib.pyplot` (if available) |
| `sns` | `seaborn` (if available) |
| `scipy` | `scipy` module (if available) |
| `stats` | `scipy.stats` (if available) |
| `sklearn` | `sklearn` top-level (if available; import submodules as needed) |
| `sm` | `statsmodels.api` (if available) |

---

## Expanded Result Display (C8)

DataFrames returned from `execute_action` are truncated before being serialised to the action history string:
- Rows: first 100 (`_MAX_ROWS`)
- Columns: first 20 (`_MAX_COLS`)

When truncation occurs, a note is appended: `_(showing 100 of N rows)_` or `_(showing 20 of N columns)_`. This prevents token bloat on large query results fed back to the LLM in subsequent iterations.

---

## Implementation

| File | Role |
|------|------|
| `src/data_analyst/api/ask.py` | Route handler: auto-select, validate, call `run_agent`, return response |
| `src/data_analyst/graph/runner.py` | `run_agent`: create DB records, invoke graph, return run_id + session_id |
| `src/data_analyst/graph/agent.py` | LangGraph `StateGraph` wiring |
| `src/data_analyst/graph/nodes.py` | All node implementations: `setup`, `plan_action`, `execute_action`, `finalize`, `force_finalize`, `handle_error` |
| `src/data_analyst/graph/edges.py` | Routing functions: `after_setup`, `after_plan`, `after_execute` |
| `src/data_analyst/graph/selector.py` | `select_datasets`: pre-flight dataset selection LLM call |
| `src/data_analyst/graph/state.py` | `AgentState` TypedDict |
