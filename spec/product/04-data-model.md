# Data Model

## Storage Technology

SQLite via SQLAlchemy 2.0. File-based, zero configuration, ships with Python. Sufficient for single-user workloads.

## Entities

### Entity: Dataset

Metadata about an uploaded CSV file.

| Field        | Type     | Required | Description |
|--------------|----------|----------|-------------|
| id           | TEXT PK  | yes      | UUID |
| filename     | TEXT     | yes      | Original filename from upload |
| file_path    | TEXT     | yes      | Absolute path to saved CSV on disk |
| row_count    | INTEGER  | yes      | Number of data rows |
| col_count    | INTEGER  | yes      | Number of columns |
| columns_json | TEXT     | yes      | JSON array of column names |
| created_at   | DATETIME | yes      | UTC timestamp |

### Entity: QueryRun

A single question asked against a dataset and the agent's answer.

| Field           | Type     | Required | Description |
|-----------------|----------|----------|-------------|
| id              | TEXT PK  | yes      | UUID |
| dataset_id      | TEXT FK  | yes      | References datasets.id |
| question        | TEXT     | yes      | User's natural language question |
| answer          | TEXT     | no       | Agent's final answer (null while running) |
| status          | TEXT     | yes      | pending / running / completed / failed |
| error_message   | TEXT     | no       | Set on failure |
| action_history  | TEXT     | no       | JSON array of {action, result, is_error} |
| iteration_count | INTEGER  | yes      | How many ReAct iterations ran (default 0) |
| created_at      | DATETIME | yes      | UTC timestamp |
| updated_at      | DATETIME | yes      | UTC, updated on status change |

### Relationships

- `QueryRun.dataset_id` → `Dataset.id` (many-to-one)
- A Dataset can have many QueryRuns

## Data Lifecycle

- Datasets persist indefinitely (no TTL in v0.1)
- QueryRuns persist indefinitely; status transitions: pending → running → completed/failed
- CSV files on disk remain until manually deleted

## Sensitive Data

- No PII stored in v0.1
- The uploaded CSV may contain user data — it is stored only on the local filesystem and never sent to any service other than the Gemini API (as context in prompts)
