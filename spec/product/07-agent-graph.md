# Agent Graph

## Graph Type

LangGraph `StateGraph` — ReAct (Reason + Act) loop.

## State

```python
class AgentState(TypedDict, total=False):
    run_id: str
    dataset_id: str
    question: str
    action_history: list[dict]   # [{"action": str, "result": str, "is_error": bool}]
    iteration_count: int
    llm_response: str            # raw last LLM output — router inspects for FINAL ANSWER
    answer: str | None
    error: str | None
    status: str                  # completed | failed
```

## Nodes

### `setup`
**Reads from state:** `run_id`, `dataset_id`
**Writes to state:** nothing (side effect: loads DataFrame into module-level cache keyed by `run_id`)
**External calls:**
| System | Operation | On Failure |
|--------|-----------|------------|
| filesystem | `pandas.read_csv(file_path)` | fatal — set error, route to handle_error |
| SQLite | fetch Dataset by dataset_id | fatal — set error, route to handle_error |

### `plan_action`
**Reads from state:** `question`, `action_history`, `iteration_count`
**Writes to state:** `llm_response`, `iteration_count` (+1)
**External calls:**
| System | Operation | On Failure |
|--------|-----------|------------|
| Gemini API | chat completion with `<node:plan>` tag injected | fatal on 5xx; recoverable on 4xx (append error, retry) |

**Behaviour:** Builds a prompt from the question + action_history, injects `<node:plan>` tag, calls Gemini. If `iteration_count >= max_iterations`, sets error and routes to handle_error.

### `execute_action`
**Reads from state:** `llm_response` (pandas expression)
**Writes to state:** appends `{action, result, is_error}` to `action_history`
**External calls:** none (pure pandas eval against cached DataFrame)

**Behaviour:** `eval(llm_response, {"df": df})`, converts result to string. On exception, marks `is_error=True` and routes back to plan_action for self-correction.

### `finalize`
**Reads from state:** `llm_response` (after stripping `FINAL ANSWER:` prefix)
**Writes to state:** `answer`, `status="completed"`
**Side effects:** saves answer + action_history to QueryRun in SQLite; deletes DataFrame from cache.

### `handle_error`
**Reads from state:** `error`
**Writes to state:** `status="failed"`
**Side effects:** saves error_message to QueryRun in SQLite; deletes DataFrame from cache.

## Edge Topology

```
START → setup
setup → [error?] → handle_error → END
setup → [ok]    → plan_action

plan_action → [FINAL ANSWER] → finalize → END
plan_action → [max_iter]     → handle_error → END
plan_action → [action]       → execute_action

execute_action → [error] → plan_action   (self-correct, error appended to history)
execute_action → [ok]    → plan_action   (result appended to history, loop)
```

## Termination Signal

`FINAL ANSWER:` prefix (case-insensitive). `plan_action` router checks `llm_response.strip().upper().startswith("FINAL ANSWER:")`. If yes → strip prefix → set `answer` → route to `finalize`.

## Max Iterations

`max_agent_iterations = 10` (configurable via `DATA_ANALYST_MAX_ITERATIONS` env var).

## Error Boundary

- **Recoverable:** pandas expression raises Exception, Gemini returns malformed output → append error to history, increment iteration, retry via `plan_action`
- **Fatal:** LLM API 5xx / network failure, `iteration_count >= max_agent_iterations` → route to `handle_error`, set status=failed

## Setup / Cleanup

- `setup` loads CSV via `pandas.read_csv(dataset.file_path)`, stores DataFrame in `_dataframes: dict[str, pd.DataFrame]` module-level dict keyed by `run_id`
- `finalize` and `handle_error` both pop `run_id` from `_dataframes` (release memory)

## Stub Provider

When `GEMINI_API_KEY` is not set, the stub LLM branches on `<node:plan>` in the prompt:
- First call: returns `df.describe().to_string()` (a real pandas expression)
- Second call: returns `FINAL ANSWER: [stub] The dataset has {N} rows and {M} columns based on df.describe().`
- Never returns identical output on two consecutive calls (iteration distinguishes them)
