# C30 — Auto-generated Dataset Notes

## Overview

Immediately after a dataset is uploaded, a background LLM call analyses the file structure and sample rows and writes a structured description into the dataset's context/notes field. The generated notes are editable — they serve as a pre-populated starting point, not a locked value.

This capability extends C12 (dataset context notes injected into prompts). Auto-generation fills the notes field the user would otherwise have to write by hand.

---

## Trigger and Background Task

`POST /upload` completes as normal (synchronous file parse + DB write). Before returning, it registers a FastAPI `BackgroundTasks` job:

```python
background_tasks.add_task(generate_dataset_notes, dataset_id)
```

`generate_dataset_notes` sets `DatasetRow.auto_notes_status = "pending"` immediately, then:
1. Loads the top 50 rows and the columns schema (name + dtype) from the uploaded file.
2. Calls the active LLM provider with a one-shot notes prompt (see below).
3. On success: writes the result to `DatasetRow.context` **only if `context` is currently NULL or empty**, then sets `auto_notes_status = "done"`.
4. On failure: sets `auto_notes_status = "failed"`, leaves `context` unchanged.

If the user has already typed notes before the background task completes, the task respects that: it **does not overwrite a non-empty `context` field**.

---

## Notes Prompt

```
You are a data analyst documenting a dataset. Given the structure and sample data below,
write concise notes (max 300 words) in plain text describing:

1. What this dataset appears to represent (one sentence overview).
2. For each column: what it contains, its inferred meaning, example or typical values.
3. Any data quality observations (null counts, suspicious values, date ranges, unit hints).

Dataset filename: {filename}
Columns:
{columns_schema_text}

Sample rows (up to 50):
{sample_rows_json}

Respond with plain text only. Be concise and factual.
```

`columns_schema_text` is the human-readable dtype list (e.g. `customer_id (integer), revenue (float)`).
`sample_rows_json` is `df.head(50).to_json(orient="records")`, truncated to 8 000 characters to fit within budget.

---

## Regeneration

A user can trigger fresh auto-generation at any time via:

```
POST /datasets/{dataset_id}/describe
```

No request body. Behaviour: sets `auto_notes_status = "pending"`, then runs the same background task. This time the task **overwrites** `context` regardless of current value (explicit user intent to regenerate).

---

## Data Model Changes

### `datasets`

| Column | Type | Nullable | Default | Description |
|--------|------|----------|---------|-------------|
| `auto_notes_status` | TEXT | yes | NULL | `"pending"` while background task runs; `"done"` on success; `"failed"` on error; `NULL` for datasets created before C30 |

The existing `context` column is unchanged (still stores the final human-readable notes).

---

## API Changes

### `POST /upload` response

Gains `"auto_notes_status": "pending"` (always `"pending"` on upload since the task has just been queued).

### `GET /datasets/{dataset_id}` response

Gains `"auto_notes_status": "pending" | "done" | "failed" | null`.

### `POST /datasets/{dataset_id}/describe` *(new)*

**Purpose:** Trigger regeneration of auto-generated notes for a dataset.

**Request:** No body.

**Response:**
```json
{"data": {"dataset_id": "uuid", "auto_notes_status": "pending"}, "error": null}
```

**Error cases:**
| Status | Condition |
|--------|-----------|
| 404 | dataset not found |

---

## UI

### Context notes field

While `auto_notes_status == "pending"`, the Context notes textarea in the Database tab right-panel shows a subtle spinner badge labelled *"Generating notes…"*. The UI polls `GET /datasets/{id}` every 2 seconds until status transitions out of `"pending"`.

When status becomes `"done"`, the textarea populates with the generated text and the spinner disappears. The user can then edit or clear the text normally. The "Regenerate notes" button (a small icon-button next to the field label) calls `POST /datasets/{id}/describe`.

### Sequence

1. User uploads file → upload response returns immediately.
2. Spinner appears in Context notes field.
3. UI polls every 2 s.
4. Background LLM call completes (typically 2–8 s depending on file size and provider latency).
5. `auto_notes_status` transitions to `"done"`; `context` now has generated text.
6. Next poll returns `"done"` → UI fills textarea, removes spinner.

---

## Interaction with C31 (Semantic Compression)

After the background task writes to `DatasetRow.context`, it immediately enqueues a C31 compression task to extract structured facts from the new notes into `DatasetRow.context_facts`. The human-readable notes stay in `context` (editable); the compressed facts in `context_facts` are what the LLM actually receives in the prompt (C12 injection path).

---

## Out of Scope

- Streaming the generated notes character by character into the textarea.
- Generating notes for datasets that already have user-written notes on first upload (the task skips non-empty `context` unless the user explicitly regenerates).
- Per-column extended profiling (e.g. histogram, correlation matrix) — notes are text-only.
