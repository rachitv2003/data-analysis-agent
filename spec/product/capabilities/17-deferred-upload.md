# Capability: Deferred Upload (Stage then Upload)

**Status:** draft

## Purpose

Currently files are uploaded the moment they are dropped or selected. This is surprising and gives the user no chance to review what will be uploaded, add notes, or remove unwanted files before they hit the server. This capability changes the flow to: *select/drop files → review staged list → click Upload*.

## Behavior

### Staging phase

1. When the user drops files/folder or uses the file picker, the files are added to a **staged file list** — no network request is made yet.
2. The staged list shows each file as a row:
   - Filename + auto-detected format badge
   - File size
   - A notes textarea (one per file, collapsed by default; click "Add notes" to expand)
   - A "Attach notes file" button (C16)
   - A ✕ remove button to drop that file from the staged list
3. Additional files can be added by dropping again or using the picker again — they are appended to the staged list.
4. The drop zone remains active while files are staged; it shows a "Add more files" label.

### Upload phase

5. An **Upload [N] file(s)** button appears below the staged list (disabled when list is empty).
6. Clicking Upload begins the same fan-out queue as C13 (3 concurrent, per-file status rows, duplicate detection, inline dup resolution). The staged list transitions to the upload queue in-place.
7. After all uploads complete, the staged list is cleared and the dataset checkbox list is updated.

### Cancellation

- The **Clear staged** button (or ✕ on every row) removes files from the staged list without uploading.
- Navigating away (e.g. refreshing) discards staged files — they exist only in browser memory.

## State machine

```
idle
  → (drop / pick) → staging
staging
  → (drop more / pick more) → staging (append)
  → (click Upload) → uploading
  → (click Clear staged) → idle
uploading
  → (all complete) → idle (dataset list updated)
  → (partial failure) → idle (failed files shown as ✗, succeeded ones registered)
```

## UI changes

- Drop zone stays visible during staging (shows "Drop more files or click Upload")
- Staged list rendered below drop zone, above dataset checkbox list
- Per-file: name, size, format badge, notes textarea (collapsed), attach notes file, ✕
- **Upload [N] file(s)** primary button + **Clear staged** secondary button
- When uploading begins, staged list rows transition to queue-row format (spinner → ✓/✗)

## Backend changes

None — the server API is unchanged. Staging is entirely client-side.

## Acceptance criteria

- [ ] Dropping files does NOT immediately upload them; they appear in a staged list
- [ ] Per-file notes textarea is visible and pre-populated into the `context` form field on upload
- [ ] Clicking ✕ on a staged row removes it; the Upload button count updates
- [ ] Dropping more files while staged appends them (no duplicates in staged list by filename)
- [ ] Clicking "Upload [N]" starts the fan-out queue and shows per-file status
- [ ] After upload completes, staged list clears and dataset checkboxes update
- [ ] "Clear staged" discards all staged files without uploading
- [ ] C16 "Attach notes file" works per staged row (reads content, previews it, sends as context)
- [ ] If a staged file is already a duplicate (same name as an existing dataset), warn inline in the staged row *before* upload (not just at upload time)
