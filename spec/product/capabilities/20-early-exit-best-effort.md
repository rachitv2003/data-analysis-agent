# Capability: C20 — Early Exit and Best-Effort Answers

## What It Does

When the ReAct loop runs out of iterations or gets stuck in a consecutive-error cycle, the agent produces the best answer it can from the work done so far instead of returning an unhelpful failure message.

## Inputs

| Input | Type | Source | Required |
|-------|------|--------|---------|
| `question` | string | `AgentState` | yes |
| `action_history` | list of `{action, result, is_error}` dicts | `AgentState` | yes |
| `iteration_count` | int | `AgentState` | yes |
| `MAX_ITERATIONS` | int constant | settings / env var | yes |

## Outputs

| Output | Type | Destination |
|--------|------|-------------|
| `answer` | string | `AgentState.answer`, persisted to `query_runs.answer` |
| `status` | `"completed"` | `AgentState.status`, persisted to `query_runs.status` |
| `error_message` | `"max_iterations"` or `"consecutive_errors"` | `query_runs.error_message` |
| `is_best_effort` | bool | API response field on `/ask` and `/sessions` |
| amber badge | UI element | answer card in the conversation thread |

## External Calls

| System | Operation | On Failure |
|--------|-----------|------------|
| LLM (Gemini / stub) | one synthesis call via `<node:finalize>` prompt | if the call fails, fall back to a static message: "The analysis could not be completed. Actions tried: {N}." — still status=completed |

## Business Rules

**1. Hard cap: MAX_ITERATIONS = 6**
The `DATA_ANALYST_MAX_ITERATIONS` default is lowered from 10 to 6. The agent has at most 6 plan→execute cycles before forced finalization.

**2. Wrap-up injection at iteration N-2**
When `iteration_count >= MAX_ITERATIONS - 2` (i.e., iteration 4 or 5 of 6), the `plan_action` node appends the following instruction to the existing prompt — no additional LLM call:

```
IMPORTANT: You have {remaining} iterations remaining. You MUST produce a FINAL ANSWER in this response or the next one. Summarize your best findings from the action history above, even if incomplete. Do not start a new line of investigation.
```

**3. Early exit on 3 consecutive errors**
If the last 3 entries in `action_history` all have `is_error: True`, the graph routes from `after_execute` directly to `force_finalize` instead of back to `plan_action`. The consecutive-error count is derived from `action_history` at routing time; no new state field is needed.

**4. `force_finalize` node — best-effort synthesis**
`force_finalize` is called when:
- Max iterations are reached (replacing the previous `handle_error` route), OR
- 3 consecutive errors are detected in `after_execute`

The node makes one LLM call with this prompt:

```
<node:finalize>
The analysis loop has ended. Based on the work done so far, write the best answer you can.
If you have partial results, summarise them. If no useful results were obtained, explain what you tried and what information would be needed to answer properly.
Do NOT say "I was unable to answer" without explanation.
</node:finalize>

Question: {question}

Work done so far:
{action_history formatted as before}
```

The response (no `FINAL ANSWER:` prefix required) becomes the answer. `status` is set to `completed`. `error_message` is set to `"max_iterations"` or `"consecutive_errors"` so callers can distinguish which trigger fired.

**5. Never return empty**
The existing `"Sorry, I was unable to answer this question"` path (triggered when max iterations are reached) is replaced entirely by `force_finalize`. Status is always `completed` for best-effort runs; the `failed` status is reserved for fatal infrastructure errors (dataset not found, LLM 5xx, etc.).

**6. API field: `is_best_effort`**
Both `/ask` and `/sessions` responses include:
- `is_best_effort: true` when `error_message` is `"max_iterations"` or `"consecutive_errors"`
- `is_best_effort: false` otherwise (including normal completions)

**7. UI indicator**
Answer cards where `is_best_effort` is `true` display a subtle amber "⚠ Best effort" badge next to the answer body. Normal completions show no badge.

**8. Stub provider**
The stub LLM detects `<node:finalize>` in the prompt and returns a canned best-effort summary, e.g.:
```
Based on the work done, here is a partial summary: [stub] The analysis reached the iteration limit. The dataset was loaded and partial results were computed.
```

## Data Model Notes

No new columns are needed. The `query_runs.error_message` column already exists and is documented here with its new valid values:

| Value | Meaning |
|-------|---------|
| `null` | Normal completion — no error |
| `"max_iterations"` | Best-effort answer: loop hit MAX_ITERATIONS cap |
| `"consecutive_errors"` | Best-effort answer: 3 consecutive execute errors |
| any other string | Fatal error — status is `failed` |

## Agent / Graph Changes Summary

| Component | Change |
|-----------|--------|
| `MAX_ITERATIONS` constant | 10 → 6 |
| `plan_action` node | Appends wrap-up instruction when `iteration_count >= MAX_ITERATIONS - 2` |
| `force_finalize` node | New node — makes one LLM synthesis call, sets `status="completed"` |
| `after_execute` edge | Checks for 3+ consecutive errors → routes to `force_finalize` |
| `after_plan` edge | Max-iterations path routes to `force_finalize` instead of `handle_error` |
| `handle_error` node | Unchanged — still handles fatal errors only |
| Stub provider | Detects `<node:finalize>` tag, returns canned best-effort summary |

## Success Criteria

- [ ] A query that previously hit 10 iterations now caps at 6
- [ ] At iteration 4 (when `MAX_ITERATIONS = 6`), the `plan_action` prompt contains the wrap-up instruction
- [ ] When 3 consecutive errors occur, `force_finalize` is called without exhausting remaining iterations
- [ ] `force_finalize` always produces a non-empty answer
- [ ] `query_runs.status` is `completed` (not `failed`) for best-effort runs
- [ ] `query_runs.error_message` is `"max_iterations"` or `"consecutive_errors"` for best-effort runs
- [ ] `/ask` response includes `is_best_effort: true` for those runs
- [ ] The UI shows the amber "⚠ Best effort" badge on best-effort answer cards
- [ ] A query that resolves in 2 iterations is unaffected: no badge, `status="completed"`, `error_message=null`, `is_best_effort=false`
