# Capability: Multi-Turn Conversation

**Status:** draft

## Purpose

Users can ask follow-up questions about the same dataset that refer to previous questions and answers in the same session, without re-stating context.

## Inputs

| Field | Type | Required | Description |
|---|---|---|---|
| dataset_id | string (UUID) | yes | The dataset being queried |
| question | string | yes | The user's current natural language question |
| session_id | string (UUID) | no | Existing session to continue; if omitted, a new session is created |

## Outputs

| Field | Type | Description |
|---|---|---|
| run_id | string (UUID) | ID of the QueryRun created for this question |
| session_id | string (UUID) | Session ID to pass on the next follow-up question |
| answer | string | Agent's plain-text answer |
| iteration_count | integer | Number of ReAct iterations |
| status | string | completed \| failed |

## Behavior

1. **Session lookup.** If `session_id` is provided, load the existing `ConversationSession` from the DB. Verify it belongs to the same `dataset_id` — if not, return a 400 error. If `session_id` is omitted, create a new `ConversationSession` and persist it.

2. **History injection.** Load all `QueryRun` records for the session in chronological order. Build a conversation history string of the form:
   ```
   Q: <previous question>
   A: <previous answer>
   ```
   Pass this history into the `plan_action` prompt so Gemini has context for the follow-up.

3. **ReAct loop.** Run the existing ReAct loop (unchanged) with the enriched prompt. The loop reasons over the data using pandas as usual.

4. **Persist.** On completion, link the new `QueryRun` to the `ConversationSession` via `session_id`. Update `ConversationSession.updated_at`.

5. **Return.** Return `session_id` alongside the answer so the client can pass it on the next request.

## Failure modes

| Condition | Behavior |
|---|---|
| `session_id` not found | 404 — "Session not found" |
| `session_id` belongs to a different `dataset_id` | 400 — "Session dataset mismatch" |
| Session has > 20 turns | 400 — "Session limit reached; start a new session" |
| Conversation history too long for LLM context | Truncate oldest turns (keep most recent 10) and continue |

## Data model changes required

- **New table:** `conversation_sessions` — `id` (UUID PK), `dataset_id` (FK), `created_at`, `updated_at`
- **New column:** `query_runs.session_id` (TEXT, nullable FK → `conversation_sessions.id`)

## API changes required

- `POST /ask` — add optional `session_id` field to request body; add `session_id` to response
- `GET /sessions/{session_id}` — list all turns in a session (question + answer pairs)

## UI changes required

- After receiving an answer, show a "Continue conversation" button that pre-fills `session_id` for the next question
- Display the conversation thread (all previous Q&A pairs) above the question box when a session is active

## Acceptance criteria

- [ ] Sending `POST /ask` without `session_id` creates a new `ConversationSession` and returns it in the response
- [ ] Sending `POST /ask` with a valid `session_id` appends the new turn to that session
- [ ] The agent's answer to a follow-up correctly references context from a prior turn (e.g. "How many are in the north?" after "Show me revenue by region")
- [ ] `session_id` belonging to a different `dataset_id` returns HTTP 400
- [ ] Unknown `session_id` returns HTTP 404
- [ ] A session with > 20 turns returns HTTP 400 with a clear message
- [ ] `GET /sessions/{session_id}` returns all turns in order
- [ ] Integration test: 2-turn conversation where the second question uses context from the first
