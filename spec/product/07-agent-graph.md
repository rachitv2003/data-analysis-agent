# Agent Graph

## Graph Type

LangGraph `StateGraph` — ReAct (Reason + Act) loop.

## State

```python
class AgentState(TypedDict, total=False):
    run_id: str
    dataset_ids: list[str]              # C14: one or more dataset UUIDs
    dataset_context: str | None         # C12: combined context injected from all datasets
    session_id: str | None
    question: str
    conversation_history: list[dict]    # C3: prior turns [{question, answer}]
    action_history: list[dict]          # [{"action": str, "result": str, "is_error": bool}]
    iteration_count: int
    llm_response: str                   # raw last LLM output — router inspects for FINAL ANSWER
    tokens_input: int
    tokens_output: int
    charts: list[str]                   # C4: Plotly JSON specs captured during this run
    answer: str | None
    error: str | None
    status: str                         # completed | failed
    selector_reasoning: str | None      # C19: raw selector LLM output; None if selection skipped
```

## Nodes

### `setup`
**Reads from state:** `run_id`, `session_id`, `dataset_ids`
**Writes to state:** nothing (side effect: populates DataFrame cache)
**External calls:**

| System | Operation | On Failure |
| ------ | --------- | ---------- |
| memory | `_session_cache[session_id][dataset_id]` lookup (C27) | cache miss — fall through to disk |
| filesystem | `pd.read_parquet(parquet_path)` (C27, preferred) | fall back to CSV |
| filesystem | `pd.read_csv(file_path)` (fallback when no Parquet) | fatal — set error, route to handle_error |
| SQLite | fetch Dataset by dataset_id | fatal — set error, route to handle_error |

**Behaviour:** For each `dataset_id`, check `_session_cache[session_id]` first. On hit, use the cached DataFrame and update the LRU order. On miss, load from `parquet_path` if set, else from `file_path` (CSV); store result in session cache. Single-turn queries (no `session_id`) use the existing run-scoped `_dataframes[run_id]` dict instead of the session cache.

### `plan_action`
**Reads from state:** `question`, `action_history`, `iteration_count`, `conversation_history`, `dataset_context`
**Writes to state:** `llm_response`, `iteration_count` (+1), `tokens_input`, `tokens_output`
**External calls:**
| System | Operation | On Failure |
|--------|-----------|------------|
| LLM (Gemini / OpenRouter / stub) | chat completion with `<node:plan>` tag injected | fatal on 5xx; recoverable on 4xx (append error, retry) |

**Behaviour:** Builds a prompt from the question + action_history + conversation_history + dataset_context + persistent memory, injects `<node:plan>` tag, calls the configured LLM. When `iteration_count >= max_iterations - 2`, appends a wrap-up instruction to the prompt (no additional LLM call): "IMPORTANT: You are running out of iterations. You MUST produce a FINAL ANSWER in this response or the next one. Summarise your best findings from the action history above, even if incomplete. Do not start a new line of investigation."

### `execute_action`
**Reads from state:** `llm_response` (pandas expression)
**Writes to state:** appends `{action, result, is_error}` to `action_history`
**External calls:**

| System | Operation | On Failure |
|--------|-----------|------------|
| SQLite | `_update_iteration_count(run_id, n)` — writes `iteration_count` to `QueryRunRow` mid-run for live progress polling | non-fatal — ignored silently |

**Behaviour:** `eval(llm_response, {"df": df})`, converts result to string. Calls `_update_iteration_count` after each successful eval so the progress bar stays current. On exception, marks `is_error=True` and routes back to plan_action for self-correction.

### `finalize`
**Reads from state:** `llm_response` (after stripping `FINAL ANSWER:` prefix)
**Writes to state:** `answer`, `status="completed"`
**Side effects:** saves answer + action_history to QueryRun in SQLite; deletes DataFrame from cache.

### `force_finalize`
**Reads from state:** `question`, `action_history`
**Writes to state:** `answer`, `status="completed"`, `error_message` (`"max_iterations"` or `"consecutive_errors"`)
**External calls:**
| System | Operation | On Failure |
|--------|-----------|------------|
| LLM (Gemini / stub) | one synthesis call with `<node:finalize>` tag injected | fall back to static message; still sets status=completed |

**Behaviour:** Makes one LLM call asking for a best-effort summary of work done. The response (no `FINAL ANSWER:` prefix required) is saved as the answer. `error_message` is set to indicate which trigger fired. Status is always `completed` — never `failed`. Called when max iterations are reached OR when 3 consecutive execute errors are detected. The stub provider detects `<node:finalize>` and returns a canned best-effort summary.

### `handle_error`
**Reads from state:** `error`
**Writes to state:** `status="failed"`
**Side effects:** saves error_message to QueryRun in SQLite; deletes DataFrame from cache.

## Edge Topology

```
START → setup
setup → [error?] → handle_error → END
setup → [ok]    → plan_action

plan_action → [FINAL ANSWER]  → finalize       → END
plan_action → [fatal error]   → handle_error   → END
plan_action → [action]        → execute_action

execute_action → [3 consec. errors OR max_iter] → force_finalize → END
execute_action → [fatal error]                  → handle_error   → END
execute_action → [error / ok]                   → plan_action     (error appended to history, self-correct)

force_finalize → END
```

## Termination Signal

`FINAL ANSWER:` substring (case-insensitive). `plan_action` router checks `"FINAL ANSWER:" in llm_response.upper()`. If found → extract text after the marker → set `answer` → route to `finalize`. This tolerates models that embed `FINAL ANSWER:` after preamble rather than strictly at the start.

## Max Iterations

`MAX_ITERATIONS = 6` (configurable via `DATA_ANALYST_MAX_ITERATIONS` env var; default lowered from 10 to 6 as of C20).

## Error Boundary

- **Recoverable:** pandas expression raises Exception, Gemini returns malformed output → append error to history, increment iteration, retry via `plan_action`
- **Best-effort:** `iteration_count >= MAX_ITERATIONS` OR last 3 `action_history` entries all have `is_error=True` → route to `force_finalize`, set `status="completed"`, `error_message="max_iterations"` or `"consecutive_errors"`
- **Fatal:** LLM API 5xx / network failure, dataset not found → route to `handle_error`, set `status="failed"`

## Setup / Cleanup

- `setup` checks `_session_cache[session_id][dataset_id]` first (C27). On hit, reuses the cached DataFrame. On miss, loads from `parquet_path` (preferred) or `file_path` (CSV fallback), then stores in `_session_cache` and the per-run `_dataframes[run_id]` dict.
- Single-turn queries (no `session_id`) bypass the session cache and use `_dataframes[run_id]` only.
- `finalize` and `handle_error` both pop `run_id` from `_dataframes` (release run-scoped memory). The session cache (`_session_cache`) is not cleared on finalize — it persists across turns in the same session and is only evicted by LRU pressure or `DELETE /sessions/{id}`.

## Stub Provider

When `DATA_ANALYST_GEMINI_API_KEY` is not set (and no other provider is configured), the stub LLM branches on the prompt tag:
- `<node:plan>` — First call: returns `df.describe().to_string()` (a real pandas expression). Second call: returns `FINAL ANSWER: [stub] The dataset has {N} rows and {M} columns based on df.describe().`. Never returns identical output on two consecutive calls (iteration distinguishes them).
- `<node:finalize>` — Returns a canned best-effort summary: `Based on the work done, here is a partial summary: [stub] The analysis reached the iteration limit. The dataset was loaded and partial results were computed.`
