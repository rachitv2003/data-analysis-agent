# Data Model

## Storage

SQLite via SQLAlchemy 2.0 ORM (`DeclarativeBase`). File at `data_analyst.db`. Schema is created by `init_db()` → `Base.metadata.create_all(engine)` on startup — no migration tool.

---

## Entities

### `datasets`

Metadata about an uploaded file.

| Column | SQLAlchemy type | Nullable | Default | Description |
|--------|----------------|----------|---------|-------------|
| `id` | TEXT PK | no | `uuid4()` | UUID |
| `filename` | TEXT | no | — | Original filename from the upload |
| `file_path` | TEXT | no | — | Absolute path to the saved CSV on disk |
| `row_count` | INTEGER | no | — | Number of data rows |
| `col_count` | INTEGER | no | — | Number of columns |
| `columns_json` | TEXT | no | — | JSON array of column name strings |
| `content_hash` | TEXT | no | `""` | SHA-256 hex digest of raw uploaded bytes (empty string for rows created before C10) |
| `format` | TEXT | no | `"csv"` | Source format: `csv`, `tsv`, `txt`, or `json` |
| `context` | TEXT | yes | NULL | User-provided notes injected into prompts (max 4 000 chars) |
| `created_at` | TIMESTAMP(tz) | no | `now(UTC)` | UTC creation timestamp |

### `query_runs`

A single question/answer pair produced by one agent invocation.

| Column | SQLAlchemy type | Nullable | Default | Description |
|--------|----------------|----------|---------|-------------|
| `id` | TEXT PK | no | `uuid4()` | UUID |
| `dataset_id` | TEXT | no | — | Primary dataset (first ID; backward compat) |
| `session_id` | TEXT | yes | NULL | `conversation_sessions.id`; null for single-turn runs |
| `question` | TEXT | no | — | User's natural language question |
| `answer` | TEXT | yes | NULL | Agent's final answer in Markdown; null while running |
| `status` | TEXT | no | `"pending"` | `pending`, `running`, `completed`, or `failed` |
| `error_message` | TEXT | yes | NULL | Set on failure or force-finalize (`"max_iterations"`, `"consecutive_errors"`) |
| `action_history` | TEXT | yes | NULL | JSON array of `{action, result, is_error}` objects |
| `iteration_count` | INTEGER | no | `0` | ReAct iterations completed; written mid-run for progress polling |
| `tokens_input` | INTEGER | no | `0` | Total prompt tokens across all LLM calls for this run |
| `tokens_output` | INTEGER | no | `0` | Total completion tokens across all LLM calls for this run |
| `dataset_ids_json` | TEXT | yes | NULL | JSON array of all session dataset IDs; null for single-dataset runs |
| `selector_reasoning` | TEXT | yes | NULL | Raw LLM output from C19 selector call; null when selection was skipped |
| `created_at` | TIMESTAMP(tz) | no | `now(UTC)` | UTC creation timestamp |
| `updated_at` | TIMESTAMP(tz) | no | `now(UTC)` | UTC; `onupdate=_now` |

### `conversation_sessions`

A session groups multiple `query_runs` into a conversation thread.

| Column | SQLAlchemy type | Nullable | Default | Description |
|--------|----------------|----------|---------|-------------|
| `id` | TEXT PK | no | `uuid4()` | UUID |
| `dataset_id` | TEXT | no | — | Primary dataset (backward compat; always `dataset_ids[0]`) |
| `dataset_ids_json` | TEXT | yes | NULL | JSON array of all session dataset IDs; null for single-dataset sessions |
| `name` | TEXT | yes | NULL | User-assigned display name for the session |
| `created_at` | TIMESTAMP(tz) | no | `now(UTC)` | UTC creation timestamp |
| `updated_at` | TIMESTAMP(tz) | no | `now(UTC)` | UTC; `onupdate=_now` |

### `settings`

Single-row key-value store for app-wide configuration and persistent memory.

| Column | SQLAlchemy type | Nullable | Default | Description |
|--------|----------------|----------|---------|-------------|
| `key` | TEXT PK | no | — | Setting key (e.g. `global_memory`) |
| `value` | TEXT | yes | NULL | Setting value |
| `updated_at` | TIMESTAMP(tz) | no | `now(UTC)` | UTC; `onupdate=_now` |

---

## Relationships

- `query_runs.dataset_id` → `datasets.id` (many-to-one; no FK constraint in SQLite, enforced in code)
- `query_runs.session_id` → `conversation_sessions.id` (nullable many-to-one)
- `conversation_sessions.dataset_id` → `datasets.id` (many-to-one)
- A dataset may have many sessions and many runs
- A session has many runs (turns), ordered by `created_at`

---

## Data Lifecycle

- Datasets persist indefinitely (no TTL).
- Deleting a dataset cascades to its sessions and runs and deletes the CSV file from disk.
- `query_runs.status` transitions: `pending` → `running` → `completed` | `failed`.
- CSV files in `uploads/` are the source of truth for DataFrames; `DatasetRow.file_path` is the pointer.
