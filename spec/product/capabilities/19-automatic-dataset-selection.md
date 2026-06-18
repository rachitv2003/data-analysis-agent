# Capability: Automatic Dataset Selection

**Capability ID:** C19
**Status:** draft

## What It Does

Before loading any DataFrames into the sandbox, the LLM inspects the schema of every uploaded dataset and autonomously selects which datasets are relevant to the user's question — so the user never has to manually choose datasets to query.

## Summary

Today (post-C14) the user must check boxes to select which datasets to include in a query. This friction disappears with C19: all uploaded datasets are always available, and a lightweight pre-flight LLM call examines every dataset's column names alongside the user's question to produce a JSON list of dataset IDs to load. Only those datasets are loaded into the sandbox for the ReAct loop. If the LLM returns an empty selection (or the call fails), all datasets are loaded as a safety net. The "Select all / Deselect all" checkbox in the UI is scoped exclusively to the dataset deletion flow and has no role in query routing.

## User Stories

- As a user, I want to type a question and have the agent figure out which of my uploaded datasets are relevant, so I don't have to manually check boxes before every query.
- As a user, I want to know which datasets the agent decided to use for my question, so I can trust or challenge the selection.
- As a user uploading many datasets, I want irrelevant datasets excluded from the sandbox so the prompt stays focused and token usage stays low.
- As a user whose question spans multiple datasets, I want the agent to automatically include all datasets it needs for a join or comparison, without me specifying them.

## Functional Requirements

1. The `POST /ask` endpoint no longer requires `dataset_ids` to be supplied by the caller. When `dataset_ids` is omitted, the backend fetches all datasets from the DB and passes their schemas to the LLM selection call.
2. The backend assembles a schema summary for every uploaded dataset using the existing `columns_json` and `col_count` / `row_count` fields — no new DB columns are needed for the schema itself. `columns_json` stores column names only (a JSON array of strings, e.g. `["id","name","revenue"]`); no dtype information is available or included in the schema summary.
3. A new lightweight LLM call ("dataset-selector call") runs before the `setup` node. It receives:
   - The user's question (verbatim)
   - A schema block listing all datasets: their filename, row count, column count, and column names (no dtypes)
   - A strict instruction to return a JSON array of dataset ID strings — no prose
4. The dataset-selector call is a single, non-iterative prompt. It does not use the ReAct loop. It uses the same LLM provider (Gemini / stub) as the ReAct loop but with a separate, minimal prompt.
5. If the dataset-selector call returns a valid non-empty JSON array of dataset IDs, those IDs are used as the `dataset_ids` for the run.
6. If the dataset-selector call returns an empty array, a malformed response, or raises an exception, the system falls back to loading all datasets (safety net). The fallback event is logged at WARN level.
7. The selection reasoning — the raw LLM response from the selector call — is stored on the `QueryRun` as `selector_reasoning` (new field, TEXT, nullable).
8. `query_runs.dataset_ids_json` (introduced in C14) is populated with the final selected dataset IDs so the full run record is self-contained. `dataset_ids_json` remains NULL for single-dataset runs, preserving the existing C14 behavior; the session constraint check and selector reasoning both use the in-memory dataset list, not this column.
9. The C14 session constraint check (`runner.py`) always receives the **full set of dataset IDs present in the session**, not just the subset chosen by the selector. `select_datasets` narrows which DataFrames are loaded into the Python sandbox, but `dataset_ids` stored on the `QueryRun` and passed to the session constraint check always equals the full set of datasets belonging to the session. This prevents false `session_dataset_mismatch` errors when the selector picks a different subset on each turn.
10. If the caller supplies an explicit `dataset_ids` list in the request body, C19 selection is skipped and those IDs are used directly (opt-out path for programmatic callers).
11. The UI dataset checkbox list serves only the deletion flow. It has no effect on which datasets are loaded for a query. The Ask button is always enabled as long as at least one dataset has been uploaded (regardless of checkbox state).
12. After the answer is returned, the UI displays a collapsible "Datasets used" line in the response footer showing the filenames of the datasets that were actually loaded.

## Out of Scope

- Reranking or scoring datasets by relevance — the LLM returns a binary include/exclude list, not a ranked list.
- Per-column relevance filtering — entire datasets are selected or excluded, not individual columns.
- Caching the schema summary across queries — it is rebuilt on every call from the DB.
- Any change to how the ReAct loop itself reasons about DataFrames once they are loaded.
- Changing the deletion UI beyond clarifying the checkbox scope label.
- Streaming or asynchronous execution of the selector call.

## Data Model Changes

One new field on `query_runs`:

| Field               | Type | Required | Description |
|---------------------|------|----------|-------------|
| selector_reasoning  | TEXT | no       | Raw LLM output from the dataset-selector call; null if selection was skipped (explicit `dataset_ids` supplied) or if fallback triggered |

No new tables. No changes to the `datasets` table — `columns_json` already holds the schema information needed.

> **Confirmed:** `columns_json` stores column names only as a JSON array of strings (e.g. `["id","name","revenue"]`) — verified from `upload.py` (`json.dumps(df.columns.tolist())`). No dtype information is stored. The selector prompt therefore lists column names only.

## API Changes

### `POST /ask` — modified

`dataset_ids` becomes optional (was implicitly required post-C14 for multi-dataset runs):

```python
class AskRequest(BaseModel):
    dataset_id: str | None = None       # backward compat — single dataset (C14)
    dataset_ids: list[str] | None = None  # explicit multi-dataset (C14); if omitted, C19 auto-selects
    question: str
    session_id: str | None = None
```

Behaviour when `dataset_ids` is `None` and `dataset_id` is `None`: trigger C19 automatic selection over all uploaded datasets.

### Response — modified

```json
{
  "run_id": "...",
  "answer": "...",
  "iteration_count": 4,
  "status": "completed",
  "dataset_ids": ["uuid-1"],
  "selector_reasoning": "The question asks about sales revenue; only sales.csv contains a revenue column."
}
```

`selector_reasoning` is `null` when selection was not performed (explicit IDs supplied).

> **Implementation note:** The `ask.py` route handler must add `selector_reasoning` to the response dict it returns, sourced from `state.get("selector_reasoning")` after the agent run. The field does not exist on `QueryRunRow` today and must be added as part of C19 implementation; the route handler should read it from the final agent state (not from the DB row) since the DB persist happens inside the graph.

### `GET /datasets` — no change

No new endpoint needed. The selector call happens entirely within the `/ask` request lifecycle.

## UI Changes

1. **Ask button enablement:** The Ask button is enabled whenever at least one dataset has been uploaded, regardless of which checkboxes are checked. Previously (C14) at least one checkbox had to be checked to enable the button.
2. **Checkbox scope label:** The dataset list section header changes from "Select datasets to query" to "Datasets" (or similar neutral label). A small note below the list reads: "Check datasets to delete them. All datasets are available for queries automatically."
3. **"Datasets used" footer:** Each answer card gains a collapsible footer line: *"Datasets used: sales.csv, inventory.csv"* derived from `dataset_ids` in the response. Collapsed by default; clicking expands to show full dataset names.
4. **No other UI changes.** The checkbox multi-select remains for the deletion flow (C15).

## Agent / Graph Changes

### New pre-node: `select_datasets`

Inserted between the `/ask` route handler and the `setup` node. Not a LangGraph node in the ReAct graph — it runs synchronously in the route handler before the graph is invoked, because it is a one-shot call, not part of the iterative loop.

**Inputs:** user question (str), list of all Dataset records from DB
**Outputs:** `dataset_ids: list[str]` (resolved list to pass into `AgentState`)

**Behaviour:**

1. Build schema block:
   ```
   Dataset 1 — "sales.csv" (id: uuid-1): 9999 rows, 4 columns — id, product, revenue, region
   Dataset 2 — "inventory.csv" (id: uuid-2): 500 rows, 3 columns — id, product, stock_level
   ```
2. Call LLM with prompt:
   ```
   <node:select>
   You are a dataset selector. Given the user's question and the list of available datasets,
   return a JSON array of dataset IDs (and only those IDs) that are needed to answer the question.
   Return [] if none are relevant (the caller will handle the fallback).
   Do not include any explanation outside the JSON array.
   </node:select>

   Question: <question>

   Available datasets:
   <schema block>

   Response (JSON array of IDs only):
   ```
   The `<node:select>...</node:select>` wrapper mirrors the `<node:plan>` tag used by the ReAct loop and allows the stub provider to detect the selector call unambiguously.
3. Parse response. If valid non-empty list → use it. Otherwise → fall back to all IDs, log WARN.
4. Store raw response in `selector_reasoning`.

### `setup` node — unchanged in interface

`setup` continues to receive `dataset_ids: list[str]` in `AgentState` and loads those DataFrames. No change to its internal logic.

### `AgentState` — one new field

```python
class AgentState(TypedDict, total=False):
    ...                                  # existing fields unchanged
    selector_reasoning: str | None       # raw selector LLM output (C19)
```

### C14 session constraint contract

`select_datasets` resolves the sandbox-load list (which DataFrames are injected into the Python eval namespace). The `dataset_ids` value passed to `run_agent` — and therefore stored on `QueryRun` and `ConversationSession`, and used for the C14 session constraint check in `runner.py` — always equals the **full set of datasets belonging to the session**, regardless of what the selector chose. Concretely:

- New session: `session.dataset_ids_json` is set from the full set of uploaded dataset IDs.
- Follow-up turn: the constraint check compares `sorted(session_ids) == sorted(full_dataset_ids)`, not the selector's subset.
- The selector output only determines which DataFrames are loaded by the `setup` node, not which IDs are recorded on the run.

### Stub provider extension

When `GEMINI_API_KEY` is not set and the selector prompt is detected (presence of `<node:select>` tag in the prompt text), the stub returns the first dataset ID in the list:
```python
import json
# stub returns first dataset id as a single-element list
return json.dumps([datasets[0].id])
```

## Acceptance Criteria

- [ ] `POST /ask` with no `dataset_ids` field and two datasets uploaded runs successfully and returns an answer
- [ ] `query_runs.dataset_ids_json` is populated with the **full set of session dataset IDs** (not just the selector-chosen subset). The selector output only determines which DataFrames are loaded into the sandbox (`sandbox_dataset_ids` at runtime); `dataset_ids_json` always reflects the full session set so the C14 session constraint check never produces false mismatches on follow-up turns.
- [ ] `query_runs.selector_reasoning` stores the raw LLM output from the selector call
- [ ] When the selector returns an empty array, all datasets are loaded and a WARN log entry is emitted
- [ ] When the selector returns malformed JSON, all datasets are loaded and a WARN log entry is emitted
- [ ] `POST /ask` with explicit `dataset_ids: ["uuid-1"]` bypasses selector and uses the supplied IDs (selector_reasoning is null)
- [ ] The Ask button is enabled when at least one dataset is uploaded, regardless of checkbox state
- [ ] The answer card shows a "Datasets used" footer listing the filenames of the selected datasets
- [ ] The dataset list header or sub-label clarifies that checkboxes are for deletion, not for query selection
- [ ] Stub mode: selector call returns a valid JSON array containing at least one dataset ID
- [ ] Integration test: upload two datasets with non-overlapping column names; ask a question specific to one; verify only that dataset's ID appears in `dataset_ids_json`
- [ ] Integration test: upload two datasets needed for a join; ask a join question; verify both IDs appear in `dataset_ids_json`
