# Capability: Dataset Context / Instructions

**Status:** draft

## Purpose

When uploading a dataset the user can attach plain-text notes — column definitions, business rules, known caveats, measurement units, etc. The agent injects this context into every prompt for that dataset, producing more accurate and domain-aware answers without requiring the user to repeat themselves in every question.

## Inputs

`POST /upload` gains an optional form field:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| file | file | yes | Data file (unchanged) |
| context | string (form field) | no | Free-text notes about the dataset. Max 4000 characters. |

The context can also be updated after upload via a new endpoint (see API changes).

## Outputs

Upload response gains a `context` field:
```json
{
  "data": {
    "dataset_id": "uuid",
    "filename": "sales.csv",
    "format": "csv",
    "context": "revenue is in USD thousands. 'region' uses ISO codes.",
    ...
  }
}
```

## Behavior

1. **Storage.** Context is stored as `datasets.context TEXT` (nullable). Empty string and null are treated equivalently.

2. **Prompt injection.** In `_build_prompt`, if the dataset has a non-empty context, prepend it to the prompt:
   ```
   Dataset context (provided by the user — treat as authoritative):
   <context text>
   ```
   This appears before the "Current question:" line, after the conversation history block.

3. **Context is loaded at run time.** `run_agent()` fetches the dataset row and passes `context` into `AgentState`. The `setup` node stores it; `_build_prompt` reads it.

4. **Edit context endpoint.** `PATCH /datasets/{dataset_id}/context` accepts `{"context": "..."}` and updates the stored text. This lets users refine context without re-uploading.

5. **Context shown in UI.** After upload, an expandable "Dataset notes" section appears below the dataset info block. It shows the current context (read-only) and an edit button that opens an inline textarea for updating it.

6. **Character limit.** If context > 4000 characters, the upload/patch returns HTTP 400 with `context_too_long`. This prevents runaway token costs from very large context blobs.

## Data model changes

- **`datasets`**: add `context TEXT` (nullable, default null)

## API changes

- `POST /upload`: optional `context` form field
- New: `PATCH /datasets/{dataset_id}/context` — update context text
- `GET /datasets` response: include `context` field per dataset

## AgentState changes

- Add `dataset_context: str | None` field
- `setup` node: reads `DatasetRow.context` and sets it in state
- `_build_prompt`: injects context block when non-empty

## UI changes

- Upload form: optional textarea labelled "Dataset notes (optional)" below the file picker
- After upload: collapsible "Dataset notes" panel under dataset info
- Edit button opens inline textarea + Save/Cancel buttons (calls `PATCH /datasets/{id}/context`)

## Acceptance criteria

- [ ] Upload with context stores it in `datasets.context`
- [ ] Upload without context stores null — no error
- [ ] Context > 4000 chars returns 400 `context_too_long`
- [ ] `_build_prompt` includes the context block when context is non-empty
- [ ] `_build_prompt` does NOT include the block when context is null/empty
- [ ] `PATCH /datasets/{id}/context` updates the context; subsequent queries use the new text
- [ ] Integration test: upload with context, ask a question, verify context appears in the prompt (via monkeypatched LLM capture)
- [ ] `GET /datasets` returns `context` field
