# Pre-flight Clarification Check

**Status:** implemented
**Covers:** C26 (pre-flight clarification check)

Before invoking the ReAct agent, a lightweight pre-flight LLM call determines whether the question is sufficiently clear to proceed. If genuine ambiguity is detected, the agent returns a clarification question to the user instead of running the full analysis. The user's response is incorporated into the session history, and the next `/ask` call proceeds normally.

---

## When It Triggers

The check runs before every `/ask` call **except** when:

- No datasets are uploaded (will error for other reasons anyway).
- `dataset_ids` or `dataset_id` are explicitly provided in the request (dataset ambiguity already resolved by caller).

When the check runs, the LLM is instructed to return "proceed" unless there is **genuine** ambiguity — undefined time ranges with multiple plausible values, metrics with multiple matching columns, or unclear subject across similar datasets. It does **not** ask about analytical approach or method (those are the agent's decisions).

---

## Flow

```text
POST /ask (no explicit dataset_ids)
  ↓
C26 clarification check  ← new (one LLM call, <node:clarify> tag)
  │
  ├─ proceed → C19 selector → ReAct loop → normal response
  │
  └─ needs clarification
       ↓
       Create QueryRunRow(status="clarification", answer=clarification_question)
       Create or reuse ConversationSession
       Return clarification response immediately
       (C19 and ReAct loop do NOT run)

User reads clarification question in thread
User types answer → POST /ask (same session_id, same original question)
  ↓
C26 check sees conversation history includes clarification Q&A → proceed
  ↓
C19 → ReAct loop → normal response
```

---

## LLM Prompt (`<node:clarify>`)

Input to the check:

- The user's question.
- All available dataset schemas (filename, row count, column names + dtypes).
- Conversation history from the current session (so follow-up questions with clear context return "proceed" immediately).

Expected JSON response from the LLM:

```json
{"proceed": true}
```

or

```json
{"proceed": false, "question": "Which time period are you referring to — 2016, 2017, or 2018?"}
```

On parse failure or LLM error → fall through to proceed (fail-open). Log at WARN level.

**Timeout:** `check_clarification()` enforces a 60-second wall-clock timeout on the LLM call. On timeout → fail-open (same handling as parse failure): log at WARN level, return proceed. The 60-second limit ensures C26 never holds up a query longer than the ReAct loop itself.

**Stub provider behaviour:** always returns `{"proceed": true}`. The clarification path is not exercised in stub/integration tests; unit tests mock the LLM call directly.

---

## API Changes

### `POST /ask` — clarification response

When the check returns `proceed: false`, `/ask` returns HTTP 200 with a distinct shape:

```json
{
  "data": {
    "clarification_needed": true,
    "clarification_question": "Which time period are you referring to — 2016, 2017, or 2018?",
    "run_id": "uuid",
    "session_id": "uuid",
    "tokens_input": 45,
    "tokens_output": 18
  },
  "error": null
}
```

`run_id` references the thin `QueryRunRow(status="clarification")` created to record the exchange. `session_id` is created (or reused from the request) so the clarification turn appears in the thread on `GET /sessions/{id}`.

### `GET /sessions/{session_id}` — clarification turns

Sessions may include turns where `status="clarification"`. The `answer` field holds the clarification question text. The UI renders these with distinct styling (see `spec/product/06-ui.md`).

---

## Token Tracking

Clarification check tokens are recorded on the `QueryRunRow(status="clarification")`:

- `tokens_input` — prompt tokens sent to LLM for the check.
- `tokens_output` — completion tokens returned.

These are included in daily token stats (`GET /stats/daily`).

---

## `QueryRunRow` status extension

`status` gains a new value: `"clarification"`. Existing values (`pending`, `running`, `completed`, `failed`) are unchanged. A clarification row has:

- `question` — the user's original question.
- `answer` — the clarification question the agent asked.
- `status = "clarification"`.
- `iteration_count = 0`.
- `action_history = null`.

---

## UI

The conversation thread renders a clarification turn as a distinct turn type: lighter background, labelled "Needs clarification", showing the agent's question. The user responds by typing in the same textarea and submitting — the frontend reuses the same `session_id` and the original question text, appending a note to the UI (not to the API payload) indicating this is a follow-up. No special API field is needed; the session history carries the context.

---

## Implementation

| File | Role |
| ---- | ---- |
| `src/data_analyst/graph/clarify.py` | `check_clarification(question, datasets, history) -> ClarifyResult` — LLM call + JSON parse |
| `src/data_analyst/api/ask.py` | Call `check_clarification` before C19; return clarification response shape if needed; create thin QueryRunRow |
| `src/data_analyst/db/models.py` | `QueryRunRow.status` accepts `"clarification"` |
| `src/data_analyst/templates/index.html` | Render clarification turns in thread with distinct styling |
