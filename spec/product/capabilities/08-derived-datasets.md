# Derived Datasets

**Status:** implemented
**Covers:** C25 (autonomous derived dataset persistence)

When the agent produces a significant intermediate DataFrame — merged tables, feature-engineered data, cluster assignments, cohort segments — it saves it as a first-class registered dataset using `save_dataset()`. The saved dataset is immediately available to the C19 auto-selector and is loaded into future queries exactly like an uploaded dataset. This eliminates the need to re-derive state across turns and makes cross-query analytical continuity reliable.

---

## Core Flow

1. During `execute_action`, the agent calls `save_dataset(df, name, description)`.
2. The function writes `df` to `uploads/{new_id}.csv`, creates a `DatasetRow` with `origin="derived"` and full provenance metadata, and returns a confirmation string that is recorded in `action_history`.
3. The agent references the saved dataset by name in its `FINAL ANSWER` text.
4. On the next query, `plan_action` injects a **derived datasets manifest** into the prompt listing all derived datasets from the current session. C19 auto-selection includes derived datasets in its universe.
5. `setup` loads derived datasets exactly like uploaded datasets — no special handling.

---

## `save_dataset()` Sandbox Function

```python
save_dataset(df: pd.DataFrame, name: str, description: str = "") -> str
```

Injected into the `execute_action` eval namespace alongside `pd`, `px`, etc.

**Behaviour:**
- Generates a new UUID as the dataset ID.
- Writes `df.to_csv(f"uploads/{new_id}.csv", index=False)`.
- Creates a `DatasetRow` with:
  - `origin = "derived"`
  - `filename = f"{name}.csv"`
  - `file_path = "uploads/{new_id}.csv"`
  - `row_count`, `col_count`, `columns_json` from the DataFrame
  - `context = description` (auto-injected into future prompts)
  - `derived_from_run_id = current_run_id`
  - `derived_from_dataset_ids = JSON(current_sandbox_dataset_ids)`
  - `derivation_code = last_executed_code_block`
- Returns a string: `"Dataset '{name}' saved — {rows} rows × {cols} cols (id: {new_id}). Variable '{var}' now available."` where `{var}` is the snake_case variable name derived from the filename.
- The return string is captured as the action result and appended to `action_history`, so the agent sees it on the next iteration.

**Naming convention (enforced via prompt):** snake_case, descriptive, e.g. `customers_clustered_10seg`, `orders_features`, `high_value_cohort`. Duplicate names are allowed — they create new dataset IDs each time.

**When the agent should call it (enforced via prompt instruction):**
- After merging two or more datasets into a combined DataFrame.
- After feature engineering producing new derived columns used in further analysis.
- After clustering, classification, or segmentation (the labelled DataFrame).
- After filtering to a meaningful cohort that will be reused.
- **Not** for trivial single-step transformations (e.g. `df.head(10)`, `df.describe()`).

---

## Prompt Changes

### Derived datasets manifest (injected into `plan_action` prompt)

When derived datasets exist for the current session, `_build_prompt` includes an additional section **before** the action history:

```
## Derived Datasets Available
The following datasets were saved during this session and are pre-loaded:

- `customers_clustered_10seg` (df_customers_clustered_10seg): 95 406 rows × 9 cols
  Description: KMeans k=10 segmentation of olist_customers. Columns: customer_id, total_spend, frequency, cluster
  Derived from: olist_customers

Do NOT re-derive if a relevant derived dataset is already listed above. Use it directly by its variable name.
```

Session-derived datasets are those whose `derived_from_run_id` belongs to a `QueryRunRow` in the current `session_id`.

### `save_dataset` instruction (injected into `plan_action` system prompt)

```
## Saving Derived Datasets
When you produce a significant intermediate DataFrame, save it for future use:

  save_dataset(df_result, "descriptive_name", "What this dataset contains and how it was created")

Use snake_case names. Only save when the result represents meaningful work that future queries could reuse. After saving, reference the dataset name in your FINAL ANSWER so the user knows it is available.
```

---

## Cross-Query Persistence (solving the shifting-clusters problem)

The canonical fix for the stochastic cluster-label bug from the conversation log:

1. First clustering query: agent fits KMeans, calls `save_dataset(clustered_df, "customers_clustered_10seg", "...")`.
2. Subsequent queries: C19 selector receives `customers_clustered_10seg` in its dataset universe, selects it. `setup` loads it from disk. Agent receives pre-labelled data — no KMeans re-run.
3. Labels are permanently fixed in the CSV. No random seed needed.

---

## API Changes

### `GET /datasets` response

Each dataset entry includes three new fields:

```json
{
  "id": "uuid",
  "filename": "customers_clustered_10seg.csv",
  "origin": "derived",
  "derived_from_dataset_ids": ["uuid-a", "uuid-b"],
  "derivation_description": "KMeans k=10 segmentation of olist_customers",
  "stale": false,
  ...
}
```

`stale` is computed at query time: `any(parent.updated_at > derived.created_at for parent in parents)`. Returns `false` for uploaded datasets.

### `POST /datasets/{dataset_id}/re-derive`

Re-executes `derivation_code` against the current versions of the parent datasets.

- Loads each parent CSV from `derived_from_dataset_ids`.
- Executes `derivation_code` in a sandboxed namespace identical to `execute_action`.
- Overwrites `uploads/{dataset_id}.csv` with the result.
- Updates `row_count`, `col_count`, `columns_json`, `updated_at`.
- Returns the same shape as `GET /datasets/{dataset_id}` with `stale: false`.

**Error responses:**
- `404 dataset_not_found` — dataset does not exist.
- `400 not_derived` — dataset has `origin="uploaded"`.
- `404 parent_not_found` — one or more parent datasets were deleted.
- `400 re_derive_error` — `derivation_code` raised an exception; body includes `error_message`.

### `DELETE /datasets/{dataset_id}` (cascade extension)

Deleting a dataset now also deletes all derived datasets whose `derived_from_dataset_ids` contains the deleted ID, recursively. Returns a `derived_deleted` count in the response alongside the existing `sessions_deleted` and `runs_deleted` counts.

---

## Data Model

See `spec/product/04-data-model.md` — four new nullable columns added to `datasets`.

## UI

See `spec/product/06-ui.md` — derived dataset badge, session filter, staleness indicator, re-derive button, lineage modal.

---

## Implementation

| File | Role |
|------|------|
| `src/data_analyst/graph/nodes.py` | `_make_eval_ns` injects `save_dataset` closure; `_build_prompt` adds derived manifest section |
| `src/data_analyst/api/datasets.py` | `GET /datasets` returns `origin`, `derived_from_dataset_ids`, `derivation_description`, `stale`; `POST /{id}/re-derive`; `DELETE /{id}` cascade extension |
| `src/data_analyst/db/models.py` | Four new columns on `DatasetRow` |
| `src/data_analyst/templates/index.html` | Derived badge, session filter, staleness indicator, re-derive button, lineage modal |
