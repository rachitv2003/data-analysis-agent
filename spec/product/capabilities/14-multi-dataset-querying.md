# Capability: Multi-Dataset Querying

**Status:** draft

## Purpose

Users can select more than one dataset and ask a question that spans all of them — comparing tables, joining on shared keys, computing cross-dataset aggregations. The agent receives all DataFrames and their schemas simultaneously and can reference any of them in its pandas expressions.

## Inputs

`POST /ask` gains `dataset_ids: list[str]` (replaces the existing `dataset_id: str`). For backward compatibility `dataset_id` (singular) is still accepted and treated as `dataset_ids: [dataset_id]`.

```json
{
  "dataset_ids": ["uuid-1", "uuid-2"],
  "question": "Which products appear in both the sales and inventory tables?",
  "session_id": "uuid-session"
}
```

## Outputs

Same response shape as today. No structural change — the answer describes the cross-dataset result.

## Behavior

### Backend

1. **DataFrame loading.** `setup` node loads all `dataset_ids` from the DB and reads each into a pandas DataFrame. Each is stored in `_dataframes` keyed by `run_id`, as a dict of `{variable_name: df}`:
   - Variable names are derived from the filename: `sales_csv`, `inventory_csv` (lowercased, extension kept, non-alphanumeric → `_`)
   - Additionally, `df1`, `df2`, … aliases are always set so the LLM can use short names

2. **Schema summary.** The `_build_prompt` function generates a schema block for each DataFrame:
   ```
   Available DataFrames:
   - df1 (sales.csv): 9999 rows × 4 cols — columns: id, product, revenue, region
   - df2 (inventory.csv): 500 rows × 3 cols — columns: id, product, stock_level
   ```

3. **Expression evaluation.** `execute_action` passes all DataFrames into the `eval()` namespace:
   ```python
   eval(expr, {"df1": df1, "df2": df2, "sales_csv": df1, "inventory_csv": df2, "pd": pd})
   ```

4. **Session constraint.** All `dataset_ids` in a session must be the same set. If a follow-up question uses a different set, return 400 `session_dataset_mismatch`.

5. **Context injection.** If any dataset has a context string (C12), all contexts are injected under their respective DataFrame variable names.

### AskRequest schema change

```python
class AskRequest(BaseModel):
    dataset_id: str | None = None      # backward compat — single dataset
    dataset_ids: list[str] | None = None  # new — multiple datasets
    question: str
    session_id: str | None = None
```
Exactly one of `dataset_id` or `dataset_ids` must be provided (or `dataset_id` is used to populate a single-element `dataset_ids` list internally).

### UI

1. The dataset selector becomes a multi-select control — a list of checkboxes, one per uploaded dataset, replacing the `<select>` dropdown.
2. At least one dataset must be selected to enable the Ask button.
3. The session panel still appears on the right — sessions are labelled by the first question as before.
4. When multiple datasets are selected, the conversation header shows: *"Querying: sales.csv + inventory.csv"*

## Data model changes

- **`query_runs`**: `dataset_id TEXT` → store first dataset id (for backward compat), add `dataset_ids_json TEXT` (JSON array of all ids)

## API changes

- `POST /ask`: accept `dataset_ids: list[str]` in addition to `dataset_id: str`
- Response: add `dataset_ids: list[str]` field
- `GET /sessions/{id}` turns: include `dataset_ids` per turn

## AgentState changes

- `dataset_id: str` → `dataset_ids: list[str]`
- `setup` loads all DataFrames; `_build_prompt` generates schema summary for all

## Acceptance criteria

- [ ] `POST /ask` with `dataset_ids: ["id1", "id2"]` runs the agent with both DataFrames available
- [ ] A join query (e.g. `pd.merge(df1, df2, on="id")`) executes successfully
- [ ] `dataset_id` (singular) still works — treated as `dataset_ids: [id]`
- [ ] Schema summary in prompt lists all DataFrames with their shapes and column names
- [ ] Session created with two datasets rejects a follow-up with different datasets (400)
- [ ] `query_runs.dataset_ids_json` stores the full list
- [ ] UI multi-select shows all uploaded datasets as checkboxes
- [ ] "Querying: X + Y" label appears in conversation header when multiple datasets selected
- [ ] Integration test: upload two datasets, ask a join question using stub, verify both DataFrames are in eval namespace
