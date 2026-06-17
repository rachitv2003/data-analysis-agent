# Capability: Dataset Deletion

**Status:** draft

## Purpose

Users can remove one or all datasets from the application — deleting the DB record, the stored CSV file on disk, and any associated sessions and query runs. This keeps the dataset list clean and frees disk space.

## Inputs / Triggers

- **Delete single dataset**: a delete button (🗑) on each dataset row in the checkbox list.
- **Clear all datasets**: a "Clear all" button at the top of the dataset list (requires a confirmation step).

## Behavior

### Single dataset deletion

1. User clicks the delete icon next to a dataset.
2. A small inline confirmation appears: *"Delete [filename]? This also removes all sessions for this dataset."* with **Delete** / **Cancel** buttons.
3. On confirm, `DELETE /datasets/{dataset_id}` is called.
4. The server:
   - Deletes all `QueryRunRow` records whose `dataset_id` (or `dataset_ids_json` contains) this ID.
   - Deletes all `ConversationSessionRow` records for this dataset.
   - Deletes the CSV file from disk (if it exists).
   - Deletes the `DatasetRow`.
5. The checkbox row is removed from the UI. If the deleted dataset was part of the active selection, the conversation thread is cleared and `currentSessionId` is reset.

### Clear all datasets

1. User clicks **Clear all**.
2. A modal confirmation: *"Delete all [N] datasets and their sessions? This cannot be undone."*
3. On confirm, `DELETE /datasets` (bulk) is called, which runs the same logic for every dataset.
4. The dataset list empties; the conversation panel resets.

### Safeguards

- Datasets currently in an active agent run (status = `running`) cannot be deleted; the server returns HTTP 409 with `code: dataset_in_use`.
- Multi-dataset sessions: if the deleted dataset is one of several in a session, the whole session (and its runs) is deleted. The user is warned in the confirmation message.

## Data model changes

None — deletion removes existing rows and files; no schema additions needed.

## API changes

- New: `DELETE /datasets/{dataset_id}` — delete one dataset and cascade
- New: `DELETE /datasets` — delete all datasets and cascade
- Both return `{"deleted_dataset_ids": [...], "deleted_session_count": N, "deleted_run_count": N}`

## UI changes

- Each dataset checkbox row gains a 🗑 icon button on the right (visible on hover).
- Inline confirmation replaces the icon on click (no full-screen modal for single delete).
- "Clear all" button appears at the bottom of the dataset list when ≥1 dataset exists.
- Full-screen confirmation modal for "Clear all" (destructive bulk action warrants it).

## Acceptance criteria

- [ ] `DELETE /datasets/{id}` removes the dataset row, CSV file, all sessions, and all query runs
- [ ] `DELETE /datasets/{id}` returns 409 if a run for that dataset is currently `running`
- [ ] `DELETE /datasets` removes every dataset and cascades to sessions and runs
- [ ] Deleted CSV file no longer exists on disk after deletion
- [ ] UI: clicking 🗑 shows inline confirm; confirming removes the row from the list
- [ ] UI: if the deleted dataset was selected, the conversation panel clears
- [ ] UI: "Clear all" button triggers modal; confirming empties the list
- [ ] Integration tests: delete one dataset, verify 404 on subsequent /ask; delete all, verify empty /datasets list
