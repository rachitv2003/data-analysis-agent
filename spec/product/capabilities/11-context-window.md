# C29 — Live Context Window Display

**Status:** implemented
**Covers:** C29 (live context window display)

---

## Overview

Surfaces real-time token budget awareness in the UI:
- **Sidebar estimate:** static component breakdown computed at rest from current state (datasets, memory, history length).
- **Steps inspector actuals:** per-component token breakdown from the last planning call, stored after each run.

---

## Sidebar — Estimated Token Usage

### Components measured

| Component | Source | Token estimate |
|-----------|--------|----------------|
| System / prompt overhead | Fixed template length | hard-coded constant (~800 tokens) |
| Dataset schemas | Column names + dtypes for all checked datasets | `len(schema_text) / 4` |
| Conversation history | Last N turns in the active session | `len(history_text) / 4` |
| Memory | Current global memory content | `len(memory_text) / 4` |
| Dataset context notes | `context` field for all checked datasets | `len(notes_text) / 4` |

Estimation uses the universal approximation of **4 characters per token** — accurate enough for budget awareness; not a true tokeniser.

### Model context limits

A hard-coded mapping of known models to their context windows, stored in both the Python config and the JavaScript UI:

| Model | Tokens |
|-------|--------|
| `gemini-3.1-flash-lite` | 1,000,000 |
| `gemini-3.1-flash` | 1,000,000 |
| `gemini-3.1-pro` | 1,000,000 |
| `gemini-2.5-flash-lite` | 1,000,000 |
| `gemini-2.5-flash` | 1,000,000 |
| `gemini-2.5-pro` | 1,000,000 |
| `gemini-2.0-flash` | 1,000,000 |
| `gemini-1.5-pro` | 2,097,152 |
| `gemini-1.5-flash` | 1,048,576 |
| `claude-opus-4` | 200,000 |
| `claude-sonnet-4` | 200,000 |
| `claude-haiku-4` | 200,000 |
| `claude-3-5-sonnet` | 200,000 |
| `claude-3-5-haiku` | 200,000 |
| `claude-3-opus` | 200,000 |
| `gpt-4o` | 128,000 |
| `gpt-4o-mini` | 128,000 |
| `gpt-4-turbo` | 128,000 |
| _(unknown Gemini)_ | 1,000,000 (catch-all) |
| _(unknown)_ | 128,000 (fallback) |

The active model name is read from `GET /stats/daily` (already returned). The JS widget looks up the limit against this table and shows `used / limit` tokens in the sidebar.

### Update triggers

The estimate re-computes whenever:
- A dataset is checked or unchecked for querying.
- A new answer turn arrives.
- The user edits and saves global memory.
- The tab switches to Analyse.

### UI placement

A compact row below the session name / token counter widget in the sidebar:

```
Context window
▓▓▓▓▓▓▓░░░░░░░░░░░░░░░  14 200 / 1 000 000
```

Clicking or hovering the bar opens a breakdown tooltip:

```
System overhead    ~  800
Dataset schemas    ~  3 400
History (4 turns)  ~  2 100
Memory             ~    450
Dataset notes      ~  7 450
────────────────────────────
Total              ~ 14 200 / 1 000 000
```

---

## Steps Inspector — Actual Token Breakdown

### Storage

A new column `prompt_breakdown: TEXT (nullable JSON)` on `query_runs`. It stores the per-component token counts recorded during the **last** `plan_action` call in the run (the most representative planning call, or the final one if the run completes in one iteration).

Shape:
```json
{
  "system_overhead": 812,
  "dataset_schemas": 3201,
  "history": 2089,
  "memory": 441,
  "dataset_notes": 7392,
  "action_history": 1740,
  "total_prompt": 15675
}
```

`total_prompt` is the actual `tokens_input` value reported by the LLM API for that call (authoritative).

### How it is captured

In `plan_action`, after building the prompt string but before calling the LLM:
1. Measure the byte/char length of each sub-section of the prompt.
2. Compute `section_tokens = len(section) // 4` for each section.
3. After the LLM call returns `tokens_input`, record it as `total_prompt`.
4. Serialize the breakdown dict to JSON and write to `QueryRunRow.prompt_breakdown` (updated alongside the existing `tokens_input` / `tokens_output` columns).

### UI — steps inspector panel

An expandable row at the top of the steps inspector (C23) labelled **"Prompt breakdown"**, visible after the run completes:

```
▼ Prompt breakdown                          15 675 tokens
   System overhead          812
   Dataset schemas         3 201
   Conversation history    2 089
   Memory                    441
   Dataset notes           7 392
   Action history          1 740
```

If `prompt_breakdown` is NULL for a run (old runs before this capability), the row is hidden.

---

## Data Model Changes

### `query_runs`

| Column | Type | Nullable | Default | Description |
|--------|------|----------|---------|-------------|
| `prompt_breakdown` | TEXT | yes | NULL | JSON breakdown of prompt token counts per section for the last plan_action call in this run |

---

## API Changes

### `GET /sessions/{session_id}` and `POST /ask` response

Each turn object gains:
```json
"prompt_breakdown": {
  "system_overhead": 812,
  "dataset_schemas": 3201,
  "history": 2089,
  "memory": 441,
  "dataset_notes": 7392,
  "action_history": 1740,
  "total_prompt": 15675
}
```

`null` for runs before C29.

### `GET /stats/daily`

Response gains:
```json
"context_limit": 1000000
```

Allows the sidebar widget to look up the limit for the active model without a separate request.

---

## Out of Scope

- True per-model tokenisation (tiktoken, sentencepiece): too heavy; approximation is sufficient for budget display.
- Per-call token breakdown (only the last planning call is stored, not every intermediate call).
- Token counting for the force-finalize synthesis call (not reflected in the breakdown).
