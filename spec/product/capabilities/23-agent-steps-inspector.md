# Capability: Agent Steps Inspector

**Capability ID:** C23
**Status:** draft

## What It Does

After a query completes, the answer card gains a collapsible "Steps" section that shows every Python expression the agent executed — including failed attempts — so the user can understand exactly how the answer was derived.

## Summary

The agent already records every action it takes in `action_history` (a JSON column on `query_runs`). This data is captured but never surfaced to the user. C23 adds a collapsible "X steps" toggle under each answer that expands to show a step-by-step log: the Python expression run, its output (or error), and a visual indicator for failed steps. No new backend storage is needed; the primary work is wiring `action_history` into the API response and building the UI renderer.

## User Stories

- As a user, I want to see what Python code the agent ran so I can understand how it derived the answer.
- As a user, I want to see which steps failed and what the error was so I can judge whether the answer is trustworthy.
- As a user, I want this to be hidden by default so it doesn't clutter the conversation when I don't need it.
- As a data analyst, I want to copy a specific step's expression to verify it or run it myself.

## Functional Requirements

1. The `POST /ask` response includes a new `steps` field: the full `action_history` array, each element being `{ "action": "<Python expression>", "result": "<output string>", "is_error": bool }`. The field is always present (empty array `[]` for stub runs or runs that produced no actions).
2. `steps` is sourced from `state.get("action_history", [])` — the same data already written to `query_runs.action_history` by the `finalize` node. No additional DB columns are needed.
3. Each answer card in the conversation thread gains a collapsible footer row showing the step count: `▶ 4 steps` (or `▶ 4 steps · 1 error` if any step has `is_error: true`).
4. Clicking the toggle expands an inline panel showing all steps in order.
5. Each step in the expanded panel shows:
   - Step number (1-indexed)
   - The Python expression, rendered in a monospace code block with a copy button
   - The result string (truncated to 300 chars with "... (truncated)" if longer)
   - A visual error badge (`✗ Error`) on the step number when `is_error: true`
   - Result text for error steps uses a muted red colour; result text for successful steps uses the default body colour
6. The toggle is collapsed by default.
7. Steps from before the current session (loaded via `GET /sessions/{id}` for resumed conversations) are also shown, sourced from the stored `action_history` JSON in the DB.
8. If `steps` is empty (e.g. stub mode, or a run that errored out immediately), the footer row is not rendered.
9. The result string displayed in the panel is the raw output from the Python sandbox — no Markdown rendering is applied to it. It is displayed in a `<pre>` element.

## Out of Scope

- SQL queries — the agent executes Python expressions only (pandas-based). There is no SQL execution layer; the inspector shows Python only.
- Per-step timing — execution duration per step is not currently tracked.
- Re-running individual steps from the UI.
- Filtering or searching within the steps panel.
- Exporting the step log.

## Data Model Changes

No new columns. `query_runs.action_history` already exists and is already populated by the `finalize` node. The GET /sessions/{id} history endpoint already returns stored QueryRun data; it must include `action_history` in its response for resumed sessions.

## API Changes

### `POST /ask` — modified response

One new field added to the existing response envelope:

```json
{
  "run_id": "...",
  "answer": "...",
  "iteration_count": 4,
  "status": "completed",
  "dataset_ids": ["..."],
  "selector_reasoning": "...",
  "steps": [
    { "action": "df[df['region']=='West']['revenue'].sum()", "result": "142300.0", "is_error": false },
    { "action": "df.groupby('product')['revenue'].mean()", "result": "...", "is_error": false }
  ]
}
```

`steps` is `[]` when no actions were executed.

### `GET /sessions/{session_id}` — modified response

Each turn object in the `turns` array gains a `steps` field:

```json
{
  "run_id": "...",
  "question": "...",
  "answer_html": "...",
  "steps": [ ... ]
}
```

Sourced by JSON-parsing `QueryRunRow.action_history`. If `action_history` is NULL or invalid JSON, `steps` defaults to `[]`.

## UI Changes

1. **Answer card footer:** Below the answer text, a toggle row is added:
   ```
   ▶ 4 steps · 1 error
   ```
   The `· 1 error` suffix is omitted when no steps have `is_error: true`. The `▶` rotates to `▼` when expanded.

2. **Steps panel (expanded state):**
   ```
   ┌─────────────────────────────────────────────────┐
   │  Step 1                                          │
   │  ┌────────────────────────────────────────────┐ │
   │  │ df[df['region']=='West']['revenue'].sum()  │ │
   │  └────────────────────────────────────────────┘ │
   │  142300.0                                        │
   │                                                  │
   │  Step 2  ✗ Error                                 │
   │  ┌────────────────────────────────────────────┐ │
   │  │ df.groupby('Region')['revenue'].mean()     │ │
   │  └────────────────────────────────────────────┘ │
   │  KeyError: 'Region'                              │
   └─────────────────────────────────────────────────┘
   ```

3. **Copy button:** Each code block has a small "Copy" button (top-right corner). Clicking it writes the expression to the clipboard and briefly shows "Copied".

4. **Panel styling:**
   - Background: `#f8fafc` (light grey), `border: 1px solid #e2e8f0`, `border-radius: 6px`, `padding: 12px`
   - Code block background: `#1e293b` (dark), white monospace text, `border-radius: 4px`, `padding: 8px 12px`
   - Error step number badge: `background: #fef2f2; color: #dc2626; border-radius: 4px; padding: 1px 5px`
   - Error result text: `color: #b91c1c`
   - Toggle button: unstyled `<button>`, same colour as answer metadata text (`#6b7280`)

5. **Session resume:** When a session is resumed, prior turns re-render their steps panels from the `steps` array returned by `GET /sessions/{session_id}`. The toggle state is collapsed by default for all prior turns.

## Agent / Graph Changes

None. `action_history` is already populated correctly by the `execute_action` and `finalize` nodes. No graph changes are required.

## Acceptance Criteria

- [ ] POST /ask response includes `steps` array with one entry per executed action
- [ ] Each entry has `action`, `result`, and `is_error` fields
- [ ] Answer card shows `▶ X steps` footer toggle after a query completes
- [ ] Clicking the toggle expands the steps panel showing all steps in order
- [ ] Error steps are visually distinguished (badge + red result text)
- [ ] Clicking "Copy" on a code block writes the expression to clipboard
- [ ] Panel is collapsed by default
- [ ] Footer toggle is absent when `steps` is empty
- [ ] Resumed session turns also render their steps from the API response
- [ ] Result strings longer than 300 chars are truncated with "... (truncated)"
- [ ] GET /sessions/{id} response includes `steps` per turn
