# Capability: Multi-File / Folder Upload

**Status:** draft

## Purpose

Let users upload several files in one action — either by selecting multiple files from the OS picker, or by dragging and dropping a folder. Each file is registered as its own dataset. A progress queue shows per-file upload status so the user knows what succeeded and what failed.

## Inputs

Same `POST /upload` endpoint, called once per file. The browser handles fan-out.

No new backend API is needed — the existing single-file endpoint is called N times in parallel.

## Behavior

### File selection

1. **Multi-select.** The file `<input>` gains the `multiple` attribute. Users can `Ctrl+Click` / `Shift+Click` to select many files.

2. **Folder drag-and-drop.** A drop zone (`<div id="drop-zone">`) accepts drag-and-drop. When a folder is dropped (detected via `DataTransferItem.webkitGetAsEntry()`), all files one level deep that have supported extensions are queued. Subdirectories are skipped with a warning.

3. **Upload queue.** A queue panel replaces the single upload status row when multiple files are selected. Each row shows:
   - Filename + format badge
   - A status indicator: pending → uploading → ✓ done / ✗ error
   - Error message inline on failure (e.g., duplicate detected, parse error)

4. **Concurrency.** Files are uploaded three at a time (parallelism = 3) to avoid saturating the server.

5. **Duplicate handling per file.** If a file returns 409, that row shows a "Use existing / Upload anyway" toggle inline in the queue row (no full-screen modal). The user can resolve each conflict independently.

6. **Auto-select on completion.** When all uploads finish, the dataset selector is populated with all successfully uploaded (or "use existing") datasets. The most recently uploaded dataset is selected by default.

7. **Context per file.** If the user has typed a context string before uploading, that same context string is sent with every file in the batch.

## Failure modes

| Condition | Behavior |
|---|---|
| One file fails parse | Other files continue; failed file shown as ✗ in queue |
| One file is a duplicate | 409 shown inline in that row; user resolves it without blocking others |
| Folder contains unsupported files | Skipped silently; a summary note shows how many files were skipped |

## Data model changes

None — each file still maps to one `DatasetRow`.

## API changes

None — fan-out is handled client-side.

## UI changes

- `<input type="file" multiple accept=".csv,.tsv,.txt,.json">`
- Drop zone `<div id="drop-zone">` covering the upload card, styled with dashed border on hover
- Upload queue panel (replaces single upload-status div when N > 1)
- Per-row inline duplicate resolution (Use existing / Upload anyway)
- Dataset selector populated with all results at the end

## Acceptance criteria

- [ ] Selecting 3 CSV files and clicking Upload registers 3 datasets
- [ ] Each file's status row updates independently (pending → done)
- [ ] A file that fails parse shows ✗ with the error; others complete normally
- [ ] A duplicate file shows 409 inline in its row; choosing "Use existing" adds the existing dataset to the selector
- [ ] Dropping a folder with 5 supported files queues all 5
- [ ] Dropping a folder with mixed files (2 supported, 3 `.xlsx`) queues only the 2 and shows a skip note
- [ ] At most 3 uploads run concurrently (verifiable via network tab)
- [ ] After all uploads complete, the dataset selector contains all successful datasets
