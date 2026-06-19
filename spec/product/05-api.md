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

**Response:**
```json
{
  "data": {
    "dataset_id": "uuid",
    "filename": "sales.csv",
    "row_count": 1000,
    "col_count": 8,
    "columns": ["date", "region", "revenue", "units"]
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
| 500 | Filesystem write failure |

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
      "created_at": "2026-06-17T12:00:00Z"
    }
  ],
  "error": null
}
```

### `POST /ask`

**Purpose:** Ask a natural language question about a dataset. Runs a pre-flight clarification check (C26), then the ReAct agent.

**Request:**
```json
{
  "dataset_id": "uuid (optional — backward compat; treated as dataset_ids: [uuid])",
  "dataset_ids": ["uuid", "uuid2"],
  "question": "What is the total revenue by region?",
  "session_id": "uuid (optional — omit to start a new session)"
}
```

`dataset_id` and `dataset_ids` are both optional. If neither is supplied, C19 auto-selects from all uploaded datasets.

**Response:**
```json
{
  "data": {
    "run_id": "uuid",
    "session_id": "uuid",
    "dataset_ids": ["uuid"],
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
    "suggested_questions": ["What is the revenue trend over time?", "Which product has the highest margin?", "How does North compare to South YoY?"]
  },
  "error": null
}
```

**Clarification response (C26):** When the pre-flight check detects genuine ambiguity, `/ask` returns HTTP 200 with a distinct shape instead of running the agent:

```json
{
  "data": {
    "clarification_needed": true,
    "clarification_question": "Which time period are you referring to — 2016, 2017, or 2018?",
    "run_id": "uuid",
    "session_id": "uuid",
    "tokens_input": 45,
    "tokens_output": 18
  },
  "error": null
}
```

The `run_id` references a thin `QueryRunRow(status="clarification")`. The user answers in the thread; the frontend re-submits with the same `session_id` and the original question. The pre-flight check sees the clarification exchange in conversation history and proceeds.

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
    "deleted_run_count": 12
  },
  "error": null
}
```

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

### `GET /stats/daily` *(C18)*

**Purpose:** Return aggregated token usage statistics and the active model name for the current UTC calendar day. Used by the token usage counter widget.

**Query parameters:** none.

**Response:**
```json
{
  "data": {
    "date": "2026-06-17",
    "model": "gemini-2.5-flash",
    "tokens_input": 3100,
    "tokens_output": 2410,
    "query_count": 11
  },
  "error": null
}
```

**Implementation notes:**
- Aggregates `query_runs` rows where `status = 'completed'` and `DATE(created_at) = <today UTC>`.
- `model` is read from `Settings.llm_model` at request time.
- Returns zero values (all counts = 0) when no completed runs exist for today — never a 404.

**Error cases:** none; always returns 200.

---

### `POST /upload` — extended fields *(C12, C16)*

In addition to `file`, accepts:
- `context` (form field, string, optional) — typed dataset notes, max 4 000 chars
- `notes_file` (file, optional) — `.txt` or `.md` file whose content is used as (or appended to) `context`

Response gains `context: string` field.

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
