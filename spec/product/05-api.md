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
  "question": "What is the total revenue by region?"
}
```

**Response:**
```json
{
  "data": {
    "run_id": "uuid",
    "answer": "The total revenue by region is: North $1.2M, South $0.8M",
    "iteration_count": 3,
    "status": "completed"
  },
  "error": null
}
```

**Error cases:**
| Status | Condition |
|--------|-----------|
| 404 | dataset_id not found |
| 400 | question is empty |
| 500 | Agent failed after max iterations |

## Authentication

None in v0.1 — single-user local tool.
