# API

## API Style

REST. All routes return `{"data": ..., "error": null}` on success or raise HTTP 4xx/5xx with `{"detail": {"code": "...", "message": "..."}}`.

## Endpoints

### `GET /health`

**Purpose:** Liveness check.

**Response:**
```json
{"data": {"status": "ok"}, "error": null}
```

### `GET /`

**Purpose:** Serves the main HTML page (Jinja2). Shows upload form + query interface.

### `POST /upload`

**Purpose:** Upload a CSV file and register it as a dataset.

**Request:** `multipart/form-data` with field `file` (CSV file).

**Query parameters:**
- `force` (boolean, default `false`) — when `true`, bypasses duplicate detection and registers the upload even if a dataset with the same content hash or filename already exists.

**Response:**
```json
{
  "data": {
    "dataset_id": "uuid",
    "filename": "sales.csv",
    "format": "csv",
    "row_count": 1000,
    "col_count": 8,
    "columns": ["date", "region", "revenue", "units"],
    "auto_notes_status": null
  },
  "error": null
}
```

**Error cases:**
| Status | Condition |
|--------|-----------|
| 400 | File extension not in `.csv`, `.tsv`, `.txt`, `.json`, `.xlsx`, `.xls` |
| 400 | File cannot be parsed by pandas |
| 400 | File is empty (0 rows) |
| 409 | `duplicate_dataset` — a dataset with the same content hash or filename already exists (unless `force=true`) |
| 500 | Filesystem write failure |

The 409 `duplicate_dataset` detail object additionally carries `match_type` (`"content"`, `"filename"`, or `"both"`), `existing_dataset_id`, `existing_filename`, and `existing_created_at`.

### `GET /datasets`

**Purpose:** List all uploaded datasets.

**Response:**
```json
{
  "data": [
    {
      "dataset_id": "uuid",
      "filename": "sales.csv",
      "row_count": 1000,
      "col_count": 8,
      "columns": ["date", "region", "revenue", "units"],
      "created_at": "2026-06-17T12:00:00Z",
      "origin": "uploaded",
      "stale": false,
      "derived_from_run_id": null,
      "derived_from_dataset_ids": null,
      "derivation_description": null
    }
  ],
  "error": null
}
```

`stale` is `true` when any parent dataset's `updated_at` is newer than the derived dataset's `created_at`; always `false` for uploaded datasets.

### `GET /datasets/{dataset_id}`

**Purpose:** Return full metadata for a single dataset, including per-column dtypes. Used by the Database tab schema panel.

**Response:**

```json
{
  "data": {
    "dataset_id": "uuid",
    "filename": "customers_clustered_10seg.csv",
    "row_count": 95406,
    "col_count": 9,
    "columns": ["customer_id", "cluster", "total_spend"],
    "columns_schema": [
      {"name": "customer_id", "dtype": "object"},
      {"name": "cluster",     "dtype": "int64"},
      {"name": "total_spend", "dtype": "float64"}
    ],
    "created_at": "2026-06-20T04:00:00Z",
    "format": "csv",
    "context": "KMeans k=10 segmentation of olist_customers",
    "origin": "derived",
    "stale": false,
    "derived_from_run_id": "uuid",
    "derived_from_dataset_ids": ["uuid-a", "uuid-b"],
    "derivation_description": "KMeans k=10 segmentation of olist_customers",
    "derivation_code": "from sklearn.cluster import KMeans\ndf_result = ..."
  },
  "error": null
}
```

`columns_schema` dtypes are read from the Parquet file if `parquet_path` is set, otherwise from `pd.read_csv(..., nrows=200).dtypes` (200 rows gives pandas enough data to infer numeric and date types). Dtypes are mapped to human-readable aliases: `object`/`string` → `"text"`, `int*`/`uint*` → `"integer"`, `float*` → `"float"`, `datetime*` → `"datetime"`, `bool` → `"boolean"`, `timedelta*` → `"duration"`, `category` → `"category"`. Uploaded datasets return `null` for all `derived_*` fields.

**Error cases:**

| Status | Condition |
| ------ | --------- |
| 404 | dataset_id not found |

---

### `GET /datasets/{dataset_id}/preview`

**Purpose:** Return the first N rows of a dataset as formatted value objects. Used by the dataset preview panel.

**Query parameters:**
- `rows` (integer, default `10`) — number of rows to return, clamped to the range 1–50.

**Response:**
```json
{
  "data": {
    "columns": ["date", "region", "revenue", "units"],
    "rows": [
      {"date": "2026-01-01", "region": "North", "revenue": 1200.5, "units": 30}
    ]
  },
  "error": null
}
```

Rows are read from the Parquet file if present, otherwise from the CSV (`nrows=N`). Values are formatted per cell: floats are rounded to 4 decimals, whole-number floats are coerced to `int`, non-finite/`NaN` values become `null`, booleans stay boolean, integers stay integer, and any other type is stringified.

**Error cases:**

| Status | Condition |
| ------ | --------- |
| 404 | `dataset_not_found` — dataset_id not found |
| 404 | `file_not_found` — dataset file missing on disk |
| 500 | `preview_error` — preview generation raised an exception |

---

### `GET /datasets/{dataset_id}/sessions`

**Purpose:** Return all sessions scoped to a single dataset (sessions whose `dataset_id` matches, or whose `dataset_ids_json` contains the id), ordered by most recently updated.

**Response:** Same item shape as `GET /sessions`:
```json
{
  "data": [
    {
      "session_id": "uuid",
      "name": "Q2 Revenue Analysis",
      "created_at": "...",
      "updated_at": "...",
      "turn_count": 4,
      "first_question": "What is the total revenue?"
    }
  ],
  "error": null
}
```

**Error cases:**

| Status | Condition |
| ------ | --------- |
| 404 | `dataset_not_found` — dataset_id not found |

---

### `POST /ask`

**Purpose:** Ask a natural language question about a dataset. Runs a pre-flight clarification check (C26), then the ReAct agent.

**Request:**
```json
{
  "dataset_id": "uuid (optional — backward compat; treated as dataset_ids: [uuid])",
  "dataset_ids": ["uuid", "uuid2"],
  "question": "What is the total revenue by region?",
  "session_id": "uuid (optional — omit to start a new session)",
  "skip_clarification": false
}
```

`dataset_id` and `dataset_ids` are both optional. If neither is supplied, C19 auto-selects from all uploaded datasets.

`skip_clarification` (default `false`) bypasses the C26 pre-flight check entirely. The frontend sets this to `true` when re-submitting after a clarification turn, so the second request runs the agent directly without triggering another clarification.

**Response — answer (normal path):**
```json
{
  "data": {
    "type": "answer",
    "run_id": "uuid",
    "session_id": "uuid",
    "dataset_ids": ["uuid"],
    "derived_dataset_ids": ["uuid3"],
    "datasets_used": [{"id": "uuid", "filename": "sales.csv"}],
    "selector_reasoning": "null or raw LLM text from C19 selector",
    "answer_markdown": "The total revenue by region is:\n\n| Region | Revenue |\n|--------|--------|\n| North | **$1.2M** |\n| South | **$0.8M** |",
    "answer_html": "<p>The total revenue by region is:</p><table>...",
    "iteration_count": 3,
    "tokens_input": 312,
    "tokens_output": 87,
    "status": "completed",
    "is_best_effort": false,
    "steps": [{"action": "df.groupby('region')['revenue'].sum()", "result": "...", "is_error": false}],
    "suggested_questions": ["What is the revenue trend over time?", "Which product has the highest margin?", "How does North compare to South YoY?"],
    "prompt_breakdown": {
      "system_overhead": 812,
      "dataset_schemas": 3201,
      "history": 2089,
      "memory": 441,
      "dataset_notes": 7392,
      "action_history": 1740,
      "total_prompt": 15675
    }
  },
  "error": null
}
```

`prompt_breakdown` (C29) records per-component token counts from the last `plan_action` call. `null` for runs before C29.

`suggested_questions` is an array of 0–3 follow-up question strings. The example above shows 3, which is the typical case. Returns an empty array when the LLM call fails or returns unparseable output.

`derived_dataset_ids` is the list of dataset IDs created by `save_dataset()` calls during this run (C25). Empty list when none were created.

**Response — clarification (C26):** When the pre-flight check detects genuine ambiguity, `/ask` returns HTTP 200 with a distinct shape instead of running the agent:

```json
{
  "data": {
    "type": "clarification",
    "clarification_question": "Which time period are you referring to — 2016, 2017, or 2018?",
    "run_id": "uuid",
    "session_id": "uuid"
  },
  "error": null
}
```

The `run_id` references a thin `QueryRunRow(status="clarification")`. The user answers in the thread; the frontend re-submits with the same `session_id`, combining the original question and the user's clarification, and sets `skip_clarification: true` so the pre-flight check is not re-run.

**Error cases:**
| Status | Condition |
|--------|-----------|
| 404 | dataset_id / any of dataset_ids not found |
| 404 | session_id not found |
| 400 | question is empty |
| 400 | session_id belongs to a different dataset_id |
| 400 | session has more than 20 turns |
| 400 | no datasets uploaded (C19 auto-select path) |
| 500 | Agent failed after max iterations |

### `GET /sessions`

**Purpose:** Return all sessions across all datasets, ordered by most recently updated.

**Response:**
```json
{
  "data": [
    {
      "session_id": "uuid",
      "name": "Q2 Revenue Analysis",
      "created_at": "...",
      "updated_at": "...",
      "turn_count": 4,
      "first_question": "What is the total revenue?"
    }
  ],
  "error": null
}
```

---

### `GET /sessions/{session_id}`

**Purpose:** Return all turns in a conversation session in chronological order.

**Response:**
```json
{
  "data": {
    "session_id": "uuid",
    "dataset_id": "uuid",
    "dataset_ids": ["uuid", "uuid2"],
    "name": "Q2 Revenue Analysis",
    "turns": [
      {
        "run_id": "uuid",
        "question": "...",
        "answer_markdown": "...",
        "answer_html": "...",
        "iteration_count": 3,
        "tokens_input": 312,
        "tokens_output": 87,
        "status": "completed",
        "is_best_effort": false,
        "steps": [],
        "prompt_breakdown": {
          "system_overhead": 812,
          "dataset_schemas": 3201,
          "history": 2089,
          "memory": 441,
          "dataset_notes": 7392,
          "action_history": 1740,
          "total_prompt": 15675
        },
        "created_at": "..."
      }
    ]
  },
  "error": null
}
```

**Error cases:**
| Status | Condition |
|--------|-----------|
| 404 | session_id not found |

---

### `PATCH /sessions/{session_id}/name`

**Purpose:** Rename a session.

**Request:** `{"name": "My analysis"}`

**Response:** `{"data": {"session_id": "uuid", "name": "My analysis"}, "error": null}`

**Error cases:** 404 session not found.

---

### `DELETE /sessions/{session_id}`

**Purpose:** Delete a single session and all its query runs.

**Response:** `{"data": {"deleted": "uuid"}, "error": null}`

**Error cases:** 404 session not found.

---

### `DELETE /sessions`

**Purpose:** Delete all sessions and all query runs that belong to sessions.

**Response:** `{"data": {"deleted": "all"}, "error": null}`

### `DELETE /datasets/{dataset_id}` *(C15)*

**Purpose:** Delete a single dataset and cascade — removes all sessions and query runs for that dataset, then deletes the CSV file from disk.

**Response:**
```json
{
  "data": {
    "deleted_dataset_ids": ["uuid"],
    "deleted_session_count": 3,
    "deleted_run_count": 12,
    "derived_deleted": 2
  },
  "error": null
}
```

`derived_deleted` is the count of derived datasets recursively deleted because they depended on the deleted dataset.

**Error cases:**
| Status | Condition |
|--------|-----------|
| 404 | dataset_id not found |
| 409 | a run for this dataset is currently `running` (`dataset_in_use`) |

---

### `DELETE /datasets` *(C15)*

**Purpose:** Delete all datasets and cascade to all sessions and runs.

**Response:** Same shape as single delete, but `deleted_dataset_ids` lists all deleted IDs.

---

### `PATCH /datasets/{dataset_id}/context` *(C12)*

**Purpose:** Update the context/notes for an existing dataset.

**Request:** `{"context": "string (max 4000 chars)"}`

**Response:** `{"data": {"dataset_id": "uuid", "context": "updated notes"}, "error": null}`

**Error cases:** 400 `context_too_long`, 404 dataset not found.

---

### `GET /stats/daily` *(C18, C29)*

**Purpose:** Return aggregated token usage statistics and the active model name for the current local calendar day (server timezone). Used by the token usage counter widget.

**Query parameters:** none.

**Response:**
```json
{
  "data": {
    "date": "2026-06-17",
    "model": "gemini-2.5-flash",
    "tokens_input": 3100,
    "tokens_output": 2410,
    "query_count": 11,
    "context_limit": 1000000
  },
  "error": null
}
```

`context_limit` is the context window size (tokens) for the active model, looked up from the hard-coded model table in C29. Used by the sidebar token budget widget to render the used/total bar. Returns `128000` for unknown models.

**Implementation notes:**

- Aggregates `query_runs` rows where `status = 'completed'` and `DATE(created_at, 'localtime') = <today local>`.
- `model` is read from `Settings.llm_model` at request time.
- Returns zero values (all counts = 0) when no completed runs exist for today — never a 404.

**Error cases:** none; always returns 200.

---

### `GET /runs/current` *(C22)*

**Purpose:** Return the most-recently-created query run for progress polling. Used by the frontend progress poller to track the active run's status and iteration count.

**Query parameters:** none.

**Response — active run:**
```json
{
  "data": {
    "run_id": "uuid",
    "status": "running",
    "iteration_count": 3,
    "max_iterations": 12
  },
  "error": null
}
```

**Response — no runs exist:**
```json
{
  "data": {
    "run_id": null,
    "status": "idle",
    "iteration_count": 0,
    "max_iterations": 12
  },
  "error": null
}
```

`max_iterations` is read from `Settings.max_iterations`.

**Error cases:** none; always returns 200.

---

### `POST /upload` — extended fields *(C12, C16, C30)*

In addition to `file`, accepts:
- `context` (form field, string, optional) — typed dataset notes, max 4 000 chars
- `notes_file` (file, optional) — `.txt` or `.md` file whose content is used as (or appended to) `context`
- `force` (query param, boolean, default `false`) — bypass duplicate detection (see the base `POST /upload` section)

Response gains `context: string` (the stored notes), `format: string` (the detected file format), and `auto_notes_status` (`"pending"` | `"done"` | `"failed"` | `null`).

---

### `GET /memory`

**Purpose:** Return the current global persistent memory string.

**Response:** `{"data": {"content": "fiscal year starts in April"}, "error": null}`

---

### `PATCH /memory`

**Purpose:** Replace the global persistent memory string. The content is injected into every `plan_action` prompt as authoritative background knowledge.

**Request:** `{"content": "fiscal year starts in April; revenue is always in USD"}`

**Response:** `{"data": {"content": "..."}, "error": null}`

---

### `POST /datasets/{dataset_id}/describe` *(C30)*

**Purpose:** Trigger (or re-trigger) auto-generation of context notes for a dataset. Sets `auto_notes_status = "pending"` and enqueues the background LLM call, which overwrites `context` regardless of current value.

**Request:** No body.

**Response:**
```json
{"data": {"dataset_id": "uuid", "auto_notes_status": "pending"}, "error": null}
```

**Error cases:**

| Status | Condition |
| ------ | --------- |
| 404 | dataset not found |

---

### `GET /datasets/{dataset_id}` — additional fields *(C29, C30)*

The response additionally includes:

```json
"auto_notes_status": "pending" | "done" | "failed" | null
```

`null` for datasets created before C30.

---

### `POST /datasets/{dataset_id}/re-derive` *(C25)*

**Purpose:** Re-execute the derivation code against the current versions of the parent datasets. Resolves stale status.

**Request:** No body.

**Response:** Same shape as `GET /datasets/{dataset_id}` with `stale: false` and updated `row_count`, `col_count`, `columns`, `columns_schema`.

**Behaviour:**

- Loads each parent from `derived_from_dataset_ids` (Parquet preferred, CSV fallback).
- Executes `derivation_code` in the same sandboxed namespace as `execute_action`.
- Overwrites `uploads/{dataset_id}.csv` with the result and regenerates `uploads/{dataset_id}.parquet`.
- Calls `_invalidate_dataset(dataset_id)` to evict stale cache entries (C27).
- Updates `row_count`, `col_count`, `columns_json`, `parquet_path`, `updated_at` in DB.

**Error cases:**

| Status | Condition |
| ------ | --------- |
| 404 | dataset not found |
| 400 | `not_derived` — dataset has `origin="uploaded"` |
| 404 | `parent_not_found` — one or more parent datasets deleted |
| 400 | `re_derive_error` — `derivation_code` raised an exception; body includes `error_message` |

---

### `POST /datasets/{dataset_id}/clean`

**Purpose:** Preview a natural-language data cleaning operation. Generates pandas code via LLM, executes it against a copy of the dataset, and returns a before/after preview without writing to disk.

**Request:** `{"instruction": "remove rows where revenue is null"}`

**Response:**
```json
{
  "data": {
    "code": "df = df.dropna(subset=['revenue'])\ndf",
    "row_count_before": 1000,
    "col_count_before": 8,
    "row_count_after": 987,
    "col_count_after": 8,
    "columns_after": ["date", "region", "revenue", "units"],
    "preview_before": [...],
    "preview_after": [...]
  },
  "error": null
}
```

**Error cases:**
| Status | Condition |
|--------|-----------|
| 404 | dataset not found |
| 400 | instruction is empty |
| 422 | generated code raised an exception |
| 500 | LLM error or file read failure |

---

### `POST /datasets/{dataset_id}/clean/apply`

**Purpose:** Apply previously previewed cleaning code to the dataset in-place. Overwrites the CSV on disk and updates `row_count`, `col_count`, and `columns_json` in the DB.

**Request:** `{"code": "df = df.dropna(subset=['revenue'])\ndf"}`

**Response:**
```json
{
  "data": {
    "row_count": 987,
    "col_count": 8,
    "columns": ["date", "region", "revenue", "units"]
  },
  "error": null
}
```

**Error cases:**
| Status | Condition |
|--------|-----------|
| 404 | dataset not found |
| 400 | code is empty |
| 422 | code execution raised an exception |
| 500 | file write failure |

---

## Authentication

None in v0.1 — single-user local tool.
