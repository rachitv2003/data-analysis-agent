# Capability: Query Execution Timer and Progress

**Capability ID:** C22
**Status:** draft

## What It Does

While a query is executing, the UI displays a running elapsed timer and a live step counter ("Step X / 6") so users know the agent is working and how far along it is, rather than staring at a frozen spinner with no feedback.

## Summary

Today the Ask button shows a spinner but gives no indication of how long execution has been running or how many reasoning steps the agent has taken. With C22, a progress bar appears below the question textarea as soon as Ask is clicked. It shows elapsed seconds and the current iteration count, updating roughly every second until the answer arrives. The step counter requires the backend to write `iteration_count` to the DB on every completed iteration (not just at finalize), and a new lightweight status endpoint for the frontend to poll.

## User Stories

- As a user, I want to see how long a query has been running so I know it hasn't frozen.
- As a user running complex queries, I want to see "Step 3 / 6" so I can gauge how much longer to wait.
- As a user, I want the progress display to disappear cleanly when the answer arrives.

## Functional Requirements

1. When the Ask button is clicked, a progress row appears below the textarea. It shows:
   - A running elapsed timer in whole seconds: `⏱ 14s`
   - A step counter: `Step 2 / 6` (updates to reflect live iteration_count from the backend)
   - A simple animated progress bar filling left-to-right as steps complete
2. The step counter updates via polling (`GET /sessions/{session_id}/current-run`) approximately once per second while the POST /ask request is in flight.
3. The progress row disappears immediately when the answer card is rendered.
4. `max_iterations` for the denominator is exposed by the server in the status response so the frontend never hardcodes it.
5. If the session has no `session_id` (single-turn flow is deprecated in C19 but kept for compat), the step counter shows `Step — / —` and only the elapsed timer is shown. The polling is skipped.
6. The backend writes `iteration_count` to the `query_runs` row at the end of every `execute_action` call (in addition to the existing write in `finalize`). The `execute_action` node issues a DB UPDATE touching only `iteration_count`; all other fields remain unchanged until finalize.
7. A new route `GET /sessions/{session_id}/current-run` returns:
   ```json
   { "run_id": "...", "status": "running", "iteration_count": 2, "max_iterations": 6 }
   ```
   It returns the most-recently-created QueryRun for the session regardless of status. If no run exists, returns `{ "run_id": null, "status": "idle", "iteration_count": 0, "max_iterations": 6 }`.
8. The elapsed timer is entirely client-side (`Date.now()` diff) — no server timestamp is used for it.
9. The polling interval is 1000 ms. On each successful poll the counter and progress bar update immediately; on error (network, 404) the counter shows the last known value and polling continues silently.
10. When `status` in the poll response is `completed` or `failed`, the frontend stops polling even if the POST /ask response has not yet arrived (edge case).

## Out of Scope

- Predicted clock-time ETA ("~40s remaining") — too unreliable given variable LLM latency.
- Streaming the agent's intermediate results (tool outputs) to the browser — that is a larger architectural change.
- Progress display for the dataset-selector pre-call (C19) — it is fast enough to not need progress feedback.
- Per-step timing (how long each individual step took).

## Data Model Changes

No new columns. `query_runs.iteration_count` gains a mid-run write contract: it is now updated inside `execute_action` in addition to `finalize`. The value written mid-run is the running total iterations; `finalize` writes the same field one final time. Both writes are idempotent under SQLite WAL mode.

## API Changes

### New: `GET /sessions/{session_id}/current-run`

Returns the latest QueryRun for the given session.

**Response (200):**
```json
{
  "run_id": "uuid",
  "status": "running",
  "iteration_count": 2,
  "max_iterations": 6
}
```

If no runs exist for the session: `{ "run_id": null, "status": "idle", "iteration_count": 0, "max_iterations": 6 }`.

`max_iterations` is sourced from `get_settings().max_iterations`.

### Existing: `POST /ask` — no change

The response contract is unchanged. The frontend uses this endpoint exactly as before; progress polling runs in parallel while waiting.

## UI Changes

1. **Progress row:** Inserted between the textarea and the Ask button area. Hidden by default. Shown on Ask click, hidden when answer renders.
   ```
   ⏱ 14s   [████████░░░░░░░░] Step 3 / 6
   ```
2. **Progress bar:** A thin bar (height ~4px) spanning the width of the progress row. Width = `(iteration_count / max_iterations) × 100%`. Animated CSS transition on width change.
3. **Step counter:** `Step X / Y` — X from last poll, Y from `max_iterations` in poll response.
4. **Elapsed timer:** Increments every second via `setInterval`. Starts at `0s` when Ask is clicked.
5. **Layout:** Progress row uses `display:flex; gap:12px; align-items:center` with the bar taking `flex:1` and the text label having `flex-shrink:0`.
6. **Colours:** Progress fill uses `#1e40af` (matches the primary button). Background track is `#e2e8f0`.
7. **No impact on answer cards** — this is purely a pre-answer UI element.

## Agent / Graph Changes

### `execute_action` node — mid-run DB write

After appending to `action_history` and before returning the updated state, `execute_action` opens a short-lived DB session and issues:

```python
with get_session() as db:
    run = db.get(QueryRunRow, run_id)
    if run:
        run.iteration_count = state["iteration_count"] + 1
        db.commit()
```

`run_id` is available in `AgentState` (already present from the setup node).

This is a fire-and-forget DB touch; exceptions are caught and logged at DEBUG level so a DB error never kills the agent loop.

### No changes to `plan_action`, `setup`, `finalize`, or the graph edges.

## Acceptance Criteria

- [ ] Clicking Ask shows progress row within 200 ms
- [ ] Elapsed timer increments every second while query runs
- [ ] Step counter updates to reflect live `iteration_count` from the server
- [ ] Progress bar width tracks `iteration_count / max_iterations`
- [ ] Progress row disappears when the answer card is rendered
- [ ] `GET /sessions/{session_id}/current-run` returns correct `iteration_count` mid-run
- [ ] `query_runs.iteration_count` reflects partial progress if the DB is inspected mid-run
- [ ] Polling stops cleanly when POST /ask returns (no dangling intervals)
- [ ] If session has no runs yet, status endpoint returns `{ "run_id": null, "status": "idle" }`
- [ ] Polling errors do not throw uncaught exceptions or affect query execution
