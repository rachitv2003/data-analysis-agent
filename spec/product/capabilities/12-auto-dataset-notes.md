# C30 — On-demand Dataset Notes Generation

**Status:** implemented
**Covers:** C30 (on-demand dataset notes generation)

---

## Overview

A user-triggered LLM call analyses a dataset's structure and sample rows and writes a structured description into the dataset's `context` field. Notes are generated on demand (Database tab button), not automatically on upload. The generated notes are always editable and are overwritten each time the user triggers regeneration.

This capability extends C12 (dataset context notes injected into prompts). Generation fills the notes field the user would otherwise have to write by hand.

---

## Trigger

The user clicks **"Generate notes"** in the Database tab's Table Description panel (next to the Context notes field). There is no automatic trigger on upload.

`POST /datasets/{dataset_id}/describe` queues the background generation task.

`generate_dataset_notes` then:
1. Loads the top 50 rows and the columns schema (name + dtype).
2. Calls the active LLM provider with a one-shot notes prompt (see below).
3. On success: **always overwrites** `DatasetRow.context` with the generated text (explicit user intent).

4. On failure: leaves `context` unchanged, sets `auto_notes_status = "failed"`.

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

### `GET /datasets/{dataset_id}` response

Includes `"auto_notes_status": "pending" | "done" | "failed" | null`.

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

### Database tab panel

A **"Generate notes"** button appears in the Context notes section of the Database tab's Table Description panel. Clicking it calls `POST /datasets/{id}/describe` and immediately shows *"Generating…"* inline text next to the field label. The UI polls `GET /datasets/{id}` every 2 seconds until `auto_notes_status` transitions out of `"pending"`.

When done, the textarea is populated with the generated text and the "Generating…" indicator is removed. The user can then edit the textarea directly — changes are saved on blur via `PATCH /datasets/{id}/context`.

### Sequence

1. User clicks **Generate notes** in the Database tab.
2. `POST /datasets/{id}/describe` queued; "Generating…" label appears.
3. UI polls every 2 s.
4. Background LLM call completes (typically 2–8 s).
5. `auto_notes_status` transitions to `"done"`; `context` now has generated text.
6. Next poll → UI fills textarea, removes "Generating…" label.

---

## Interaction with C31 (Semantic Compression)

After the background task writes to `DatasetRow.context`, it immediately enqueues a C31 compression task to extract structured facts from the new notes into `DatasetRow.context_facts`. The human-readable notes stay in `context` (editable); the compressed facts in `context_facts` are what the LLM actually receives in the prompt (C12 injection path).

---

## Out of Scope

- Streaming the generated notes character by character into the textarea.
- Generating notes for datasets that already have user-written notes on first upload (the task skips non-empty `context` unless the user explicitly regenerates).
- Per-column extended profiling (e.g. histogram, correlation matrix) — notes are text-only.
