# C29 — Live Context Window Display

**Status:** implemented
**Covers:** C29 (live context window display)

---

## Overview

Surfaces real-time token budget awareness in the UI:
- **Sidebar context bar:** shows the *most recent single prompt size* (`last_prompt` from the latest turn's stored breakdown; falls back to `total_prompt` on runs recorded before `last_prompt` was tracked) against the active model's context limit. Before any turn has run for the current selection it falls back to a crude estimate. It does **not** compute a per-component breakdown.
- **Steps inspector actuals:** per-component token breakdown for a given turn, rendered behind a "Token breakdown" toggle, using the stored actuals in `prompt_breakdown`. The breakdown is **accumulated across every LLM call in the run**, so its `total_prompt` equals `run.tokens_input` — the same "tokens in" figure shown below the answer and in the Last query pane.

---

## Sidebar — Context Window Bar

The sidebar bar (`#ctx-bar-wrap`, JS `_updateCtxBar`) displays a single used/total figure, not a component breakdown:

- **Used:** `_lastActualPromptTokens` — the `last_prompt` recorded by the most recent turn that arrived (the size of a single plan_action prompt, i.e. how full one call's context is — falls back to `total_prompt` for runs recorded before `last_prompt` was tracked). When no actual is available yet (no turn has run for the current dataset selection), it falls back to a crude estimate of `500 + (number of checked datasets) × 200` tokens, prefixed with `~` to mark it as approximate.
- **Limit:** the active model's context window, read from the single `context_limit` value returned by `GET /stats/daily` (stored in `window._ctxLimit`).

The bar is hidden when no datasets are checked. Fill colour turns to a warning state at ≥70% and a danger state at ≥90% of the limit.

The **per-component** token estimates (system overhead, dataset schemas, history, memory, dataset notes, action history) are computed server-side in `_build_prompt`, stored in `prompt_breakdown`, and surfaced only in the per-turn steps inspector (see below) — never as an at-rest sidebar tooltip.

### Per-component estimation (server-side, for the breakdown)

| Component | Source | Token estimate |
|-----------|--------|----------------|
| System / prompt overhead | **Residual:** total prompt tokens minus all other measured sections | `_tok(prompt) − (schemas + notes + memory + history + action_history)`, floored at 0 |
| Dataset schemas | Column names + dtypes for all checked datasets (plus derived-dataset manifest) | `len(schema_text) / 4` |
| Conversation history | Last N turns in the active session | `len(history_text) / 4` |
| Memory | Current global memory / facts content | `len(memory_text) / 4` |
| Dataset context notes | `context` field for all checked datasets | `len(notes_text) / 4` |
| Action history (this turn) | Prior actions + results accumulated this turn | `len(history_text) / 4` |

Estimation uses the universal approximation of **4 characters per token** — accurate enough for budget awareness; not a true tokeniser. `system_overhead` is **not** a hard-coded constant: it is derived as a residual so the components always sum to the estimated prompt total.

### Model context limits

A hard-coded mapping of known models to their context windows, defined in the Python config (`_CONTEXT_LIMITS` in `stats.py`) and exposed to the UI as a single resolved value via `GET /stats/daily`:

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

The limit is resolved server-side by `get_context_limit(model)` against `_CONTEXT_LIMITS` and returned as the single `context_limit` value on `GET /stats/daily`. There is **no** separate model→limit table in the JavaScript; the UI simply reads `context_limit` into `window._ctxLimit` and renders `used / limit` in the sidebar bar.

### Update triggers

The bar re-renders whenever `_updateCtxBar` is called, which includes:
- A dataset is checked or unchecked for querying.
- A new answer turn arrives (updating `_lastActualPromptTokens` from the turn's `total_prompt`).
- `GET /stats/daily` refreshes (which sets `window._ctxLimit`).

### UI placement

A compact row below the session name / token counter widget in the sidebar. When an actual prompt total is known it is shown exactly; before the first turn it shows a `~`-prefixed fallback estimate:

```
Context window
▓▓▓▓▓▓▓░░░░░░░░░░░░░░░  14 200 / 1 000 000
```

The bar shows only this single used/total figure — there is no sidebar breakdown tooltip. The per-component breakdown lives in the steps inspector (below).

---

## Steps Inspector — Actual Token Breakdown

### Storage

A column `prompt_breakdown: TEXT (nullable JSON)` on `query_runs`. It **accumulates** the per-component token counts across every LLM call in the run — each `plan_action` call (merge-summed), plus an `auxiliary` bucket for the non-plan_action calls (dataset selector, follow-up suggestions, force-finalize synthesis). Because each `plan_action` call re-sends the whole prompt and each auxiliary call adds its own input tokens, the cumulative `total_prompt` equals `run.tokens_input`.

Shape:
```json
{
  "system_overhead": 1624,
  "dataset_schemas": 6402,
  "history": 4178,
  "memory": 882,
  "dataset_notes": 14784,
  "action_history": 3480,
  "auxiliary": 20,
  "total_prompt": 31370,
  "last_prompt": 15675
}
```

- `total_prompt` is the **cumulative** actual `tokens_input` across all calls in the run — it reconciles with `run.tokens_input` (the headline "tokens in").
- `last_prompt` is the actual `tokens_input` of the **most recent single `plan_action` call** — the size of one prompt, used by the sidebar context-window bar (a different quantity from the cumulative total).
- `auxiliary` is the summed input tokens of the selector + suggestion + force-finalize calls.

### How it is captured

In `_build_prompt` (called by `plan_action` each iteration), while assembling the prompt string:
1. Compute `section_tokens = len(section) // 4` for the dataset schemas, dataset notes, memory, history, and action-history sub-sections.
2. Compute `system_overhead` as a **residual** — `max(0, _tok(prompt) − schemas − notes − memory − history − action_history)` — so it captures the static template text plus anything not attributed to a named section. It is not a fixed constant.
3. `plan_action` writes this breakdown via `_update_prompt_breakdown` on each iteration. The helper **merge-sums** the delta into the stored breakdown (so components and `total_prompt` accumulate across iterations) and overwrites `last_prompt` with this call's `tokens_input`. `total_prompt` for each call is the actual `tokens_input` reported by the LLM API (authoritative).
4. Calls outside `plan_action` fold their input tokens into the breakdown too: `force_finalize` adds its synthesis call (in `nodes.py`), and the `/ask` handler adds the dataset-selector and follow-up-suggestion calls — each as an `auxiliary` component plus the same delta on `total_prompt`. After these, `total_prompt == run.tokens_input`.
5. The breakdown dict is serialized to JSON and written to `QueryRunRow.prompt_breakdown` alongside the existing `tokens_input` / `tokens_output` columns.

### UI — steps inspector panel

An expandable row at the top of the steps inspector (C23) labelled **"Prompt breakdown"**, visible after the run completes:

```
▼ Prompt breakdown                Total (= tokens in)  31 370 tok
   System overhead          1 624
   Dataset schemas          6 402
   Dataset notes           14 784
   Project notes              882
   Conversation history     4 178
   Steps (this run)         3 480
   Selector & suggestions      20
   ────────────────────────────
   Total (= tokens in)     31 370
   Tokens out                 640
```

The displayed components decompose the **prompt (input)** and are accumulated across the run; the `Total (= tokens in)` row equals the "tokens in" figure shown below the answer. Output has no per-component structure, so it is shown as a plain `Tokens out` total beside the input total (the run's `tokens_output`, passed to `_renderBreakdown` from `appendTurn`), not as a component. If `prompt_breakdown` is NULL for a run (old runs before this capability), the row is hidden.

---

## Data Model Changes

### `query_runs`

| Column | Type | Nullable | Default | Description |
|--------|------|----------|---------|-------------|
| `prompt_breakdown` | TEXT | yes | NULL | JSON breakdown of prompt token counts per section, accumulated across all LLM calls in the run; `total_prompt` reconciles with `tokens_input`, `last_prompt` holds the most recent single-call size |

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

- True per-model tokenisation (tiktoken, sentencepiece): too heavy; approximation is sufficient for budget display. The per-component figures remain 4-chars-per-token **estimates**; only `total_prompt` / `last_prompt` (and the `auxiliary` bucket) are authoritative LLM-reported counts, so the components sum to approximately — not exactly — `total_prompt`.
- A separate per-call (per-iteration) breakdown: calls are summed into one cumulative breakdown, not stored individually.
