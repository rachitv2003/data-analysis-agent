# Conversation

**Status:** implemented
**Covers:** C3 (multi-turn history), C9 (session management UI), C14 (multi-dataset querying), C15 (dataset deletion)

A session groups multiple question/answer turns against a fixed set of datasets into a persistent conversation thread. Prior turns are injected into each new turn's prompt as context. The UI lists all sessions globally, allows resuming any session, and provides dataset deletion with cascade.

---

## Sessions

A `ConversationSession` is created on the first `/ask` call and reused on subsequent calls via `session_id`. Each session is bound to a fixed set of datasets — adding or removing datasets requires starting a new session.

**Session constraint:** `runner.py` validates that the dataset IDs supplied on a follow-up turn (sorted) match the IDs stored on the session. A mismatch raises `ValueError("Session dataset mismatch")` → HTTP 400.

**Conversation history:** Up to the last 10 completed turns are injected into the LLM prompt as `Q: ... / A: ...` pairs. The session cap is 20 turns; exceeding it raises `ValueError("Session limit reached")` → HTTP 400.

**Session storage:** `ConversationSessionRow` stores:
- `dataset_id` — primary dataset (backward compat; always the first dataset ID)
- `dataset_ids_json` — JSON array of all dataset IDs for multi-dataset sessions
- `created_at`, `updated_at`

---

## Session Endpoints

`GET /sessions` — lists all sessions across all datasets, most-recently-updated first. Each entry includes `session_id`, `created_at`, `updated_at`, `turn_count`, `first_question`.

`GET /sessions/{session_id}` — returns all turns in chronological order. Each turn includes `run_id`, `question`, `answer_markdown`, `answer_html`, `iteration_count`, `tokens_input`, `tokens_output`, `status`, `is_best_effort`, `steps`, `created_at`.

`GET /datasets/{dataset_id}/sessions` — lists sessions for a specific dataset (same shape as global listing).

---

## Multi-Dataset Querying (C14)

A session can be bound to more than one dataset. The `POST /ask` request accepts `dataset_ids: list[str]` (or the legacy `dataset_id: str`). All specified datasets are loaded into the ReAct sandbox simultaneously as separate DataFrames, allowing joins and cross-dataset questions.

Variable naming: each dataset gets a filename-derived variable (`sales_data`, `inventory`, etc.) plus `df` / `df1` / `df2` aliases. The prompt lists all loaded DataFrames with their schemas.

---

## Dataset Deletion (C15)

`DELETE /datasets/{dataset_id}` — deletes a single dataset and cascades to all sessions and runs referencing it. Blocked with 409 if any run against this dataset has `status="running"`.

`DELETE /datasets` — deletes all datasets and all sessions/runs. Blocked with 409 if any run is running.

Both endpoints delete the CSV file from disk (`file_path`) and return:
```json
{
  "data": {
    "deleted_dataset_ids": ["uuid", ...],
    "deleted_session_count": 3,
    "deleted_run_count": 12
  }
}
```

Cascade logic (in `_cascade_delete`): finds all sessions referencing any of the deleted datasets (checking both `dataset_id` and `dataset_ids_json`), deletes their runs, deletes the sessions, then deletes orphan runs with no session, then deletes the dataset rows and CSV files.

---

## Session Management UI (C9)

The sessions panel (left column of the top row) shows all sessions globally, most-recently-updated first. Each row shows the first question (truncated to 55 chars) and `N turns · Xm ago`. Clicking a session row calls `resumeSession()`, which fetches `GET /sessions/{id}` and replays all turns into the conversation thread.

"+ New" button calls `startNewSession()`, which clears `currentSessionId`, empties the thread, and deactivates all session items. A new session is created automatically on the next `POST /ask`.

The session list is refreshed via `loadSessionList()` after every successful ask.

---

## Implementation

| File | Role |
|------|------|
| `src/data_analyst/api/sessions.py` | `GET /sessions`, `GET /sessions/{session_id}` |
| `src/data_analyst/api/dataset_sessions.py` | `GET /datasets/{dataset_id}/sessions` |
| `src/data_analyst/api/datasets.py` | `DELETE /datasets/{id}`, `DELETE /datasets`, `_cascade_delete` |
| `src/data_analyst/graph/runner.py` | Session creation, conversation history loading, 20-turn cap |
| `src/data_analyst/db/models.py` | `ConversationSessionRow`, `QueryRunRow` |
| `src/data_analyst/templates/index.html` | Session sidebar, resume/new, dataset checkboxes, deletion modals |
