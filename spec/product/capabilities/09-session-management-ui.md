# Capability: Session Management UI

**Status:** draft

## Purpose

Users can maintain multiple named conversation sessions per dataset — starting new ones, resuming old ones, and seeing the full history of any session. Currently the UI only holds one active session in memory; switching datasets or refreshing the page loses context entirely.

## Inputs

| Source | Input | Description |
|--------|-------|-------------|
| GET /datasets/{dataset_id}/sessions | dataset_id | Fetch all sessions for a dataset |
| GET /sessions/{session_id} | session_id | Fetch full turn history for a session |
| POST /ask | session_id (optional) | Continue an existing session |

## Outputs

### GET /datasets/{dataset_id}/sessions
```json
{
  "data": [
    {
      "session_id": "uuid",
      "created_at": "2026-06-17T10:00:00Z",
      "updated_at": "2026-06-17T10:05:00Z",
      "turn_count": 3,
      "first_question": "Describe the dataset"
    }
  ]
}
```
Sessions are ordered by `updated_at` descending (most recently active first).

## Behavior

### Backend

1. **New API endpoint.** `GET /datasets/{dataset_id}/sessions` returns all `ConversationSessionRow` entries for that dataset, annotated with:
   - `turn_count` — number of completed `QueryRunRow` entries linked to the session
   - `first_question` — the `question` from the earliest `QueryRunRow` in the session (used as a label)

2. **Existing endpoints unchanged.** `POST /ask` (with `session_id`) and `GET /sessions/{id}` already handle continuation and history retrieval.

### Frontend

1. **Session sidebar panel.** When a dataset is selected, fetch `GET /datasets/{dataset_id}/sessions` and display a scrollable list of past sessions below the dataset selector, above the conversation thread:
   - Each row shows: session label (first question, truncated to 60 chars) + relative time (e.g. "2 hours ago")
   - The active session is highlighted
   - A **"+ New session"** button at the top of the list

2. **Resuming a session.** Clicking a past session:
   - Fetches `GET /sessions/{session_id}` to retrieve all turns
   - Renders the full Q&A history in the conversation thread
   - Sets `currentSessionId` so subsequent questions continue that session

3. **Starting a new session.** Clicking **"+ New session"** (or asking the first question with no session selected):
   - Clears the conversation thread
   - Sets `currentSessionId = null`
   - A new session is created on the next `POST /ask`
   - The session list refreshes after the first answer

4. **Session list refresh.** After each successful `POST /ask`, re-fetch the session list so the updated timestamp and turn count are reflected.

5. **Empty state.** If a dataset has no prior sessions, show: *"No previous conversations. Ask a question to start one."*

## Data model changes

None — `conversation_sessions` and `query_runs` already store everything needed.

## API changes

- **New:** `GET /datasets/{dataset_id}/sessions` — returns session list with `turn_count` and `first_question`

## UI changes

- Session list panel between dataset selector and conversation thread
- Each session entry: truncated first question + relative timestamp
- Active session highlighted (blue left border)
- "+ New session" button
- Session list auto-refreshes after each answer

## Acceptance criteria

- [ ] `GET /datasets/{id}/sessions` returns all sessions for that dataset, ordered by `updated_at` desc
- [ ] `turn_count` and `first_question` are correct for each session
- [ ] Clicking a past session loads its full history into the conversation thread
- [ ] Subsequent questions after resuming are appended to the correct session
- [ ] "+ New session" clears the thread and unsets `currentSessionId`
- [ ] Session list refreshes after each new answer (updated_at and turn_count update)
- [ ] Dataset with no sessions shows the empty state message
- [ ] Integration test: create 2 sessions for the same dataset, `GET /datasets/{id}/sessions` returns both
