# Capability: Token Usage Tracking

**Status:** draft

## Purpose

Every query run records the number of input and output tokens consumed across all Gemini API calls in that run, so users can see the cost of each question and monitor cumulative usage.

## Inputs

No new inputs — token counts are captured automatically during every `/ask` call.

## Outputs

| Field | Type | Description |
|---|---|---|
| tokens_input | integer | Total prompt tokens sent to Gemini across all ReAct iterations |
| tokens_output | integer | Total completion tokens received from Gemini across all iterations |

These fields are added to the `POST /ask` response and to each turn in `GET /sessions/{id}`.

## Behavior

1. **LLM client layer.** `LLMClient.complete()` currently returns only `str`. It is updated to return a named tuple / dataclass:
   ```python
   @dataclass
   class LLMResponse:
       text: str
       tokens_input: int   # prompt_token_count from Gemini usage_metadata
       tokens_output: int  # candidates_token_count from Gemini usage_metadata
   ```
   The `GeminiProvider` reads `response.usage_metadata` from the `google-genai` SDK response. The `StubLLMProvider` returns `tokens_input=10, tokens_output=20` (fixed values for deterministic tests).

2. **State accumulation.** `AgentState` gains two new fields:
   ```python
   tokens_input: int   # running total across iterations, default 0
   tokens_output: int  # running total across iterations, default 0
   ```
   The `plan_action` node adds each call's token counts to the running totals after every LLM call.

3. **Persistence.** The `finalize` and `handle_error` nodes write `tokens_input` and `tokens_output` from state into the `query_runs` row.

4. **API exposure.** `POST /ask` response adds:
   ```json
   { "tokens_input": 312, "tokens_output": 87 }
   ```
   `GET /sessions/{id}` turns also include per-turn token counts.

5. **UI display.** Each conversation turn shows a token summary below the answer:
   ```
   2 iterations · 312 in / 87 out tokens
   ```

## Failure modes

| Condition | Behavior |
|---|---|
| `usage_metadata` missing from Gemini response | Log a warning; store 0 for that call; do not fail the run |
| Stub provider | Always returns fixed counts (10 in / 20 out per call) |

## Data model changes required

- **`query_runs`**: add `tokens_input INTEGER NOT NULL DEFAULT 0`, `tokens_output INTEGER NOT NULL DEFAULT 0`

## API changes required

- `POST /ask` response: add `tokens_input`, `tokens_output`
- `GET /sessions/{id}` turns: add `tokens_input`, `tokens_output` per turn

## UI changes required

- Conversation thread turn footer: show `{iterations} iterations · {tokens_input} in / {tokens_output} out tokens`
- When `tokens_input == 0` (stub mode or missing metadata): show only `{iterations} iterations`

## Acceptance criteria

- [ ] `POST /ask` response includes `tokens_input` and `tokens_output` as integers ≥ 0
- [ ] A 2-iteration run accumulates token counts from both calls (not just the last)
- [ ] `query_runs` DB row has correct token totals after a completed run
- [ ] Stub provider returns fixed counts (10 in / 20 out per iteration) — integration test asserts `tokens_output == 40` for a 2-iteration stub run
- [ ] Missing `usage_metadata` does not cause a 500 error
- [ ] Token counts appear in the UI conversation thread footer
- [ ] `GET /sessions/{id}` includes token counts per turn
