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
| 400 | File is not a .csv or cannot be parsed by pandas |
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

**Purpose:** Ask a natural language question about a dataset. Runs the ReAct agent synchronously.

**Request:**
```json
{
  "dataset_id": "uuid",
  "question": "What is the total revenue by region?",
  "session_id": "uuid (optional — omit to start a new session)"
}
```

**Response:**
```json
{
  "data": {
    "run_id": "uuid",
    "session_id": "uuid",
    "answer_markdown": "The total revenue by region is:\n\n| Region | Revenue |\n|--------|--------|\n| North | **$1.2M** |\n| South | **$0.8M** |",
    "answer_html": "<p>The total revenue by region is:</p><table>...",
    "iteration_count": 3,
    "tokens_input": 312,
    "tokens_output": 87,
    "status": "completed"
  },
  "error": null
}
```

**Error cases:**
| Status | Condition |
|--------|-----------|
| 404 | dataset_id not found |
| 404 | session_id not found |
| 400 | question is empty |
| 400 | session_id belongs to a different dataset_id |
| 400 | session has more than 20 turns |
| 500 | Agent failed after max iterations |

### `GET /sessions/{session_id}`

*(Added for Capability 3)*

**Purpose:** Return all turns in a conversation session in chronological order.

**Response:**
```json
{
  "data": {
    "session_id": "uuid",
    "dataset_id": "uuid",
    "turns": [
      {"run_id": "uuid", "question": "...", "answer": "...", "created_at": "..."},
      {"run_id": "uuid", "question": "...", "answer": "...", "created_at": "..."}
    ]
  },
  "error": null
}
```

**Error cases:**
| Status | Condition |
|--------|-----------|
| 404 | session_id not found |

## Authentication

None in v0.1 — single-user local tool.
