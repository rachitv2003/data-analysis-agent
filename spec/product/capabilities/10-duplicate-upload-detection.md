# Capability: Duplicate Upload Detection

**Status:** draft

## Purpose

Prevent silent duplication of datasets by detecting when a newly uploaded file has the same name or identical contents as one already stored. The user is warned and given the choice to proceed or reuse the existing dataset.

## Inputs

The upload endpoint (`POST /upload`) receives the file as before. No new request fields are required.

## Outputs

### New response: duplicate detected (HTTP 409)
```json
{
  "data": null,
  "error": {
    "code": "duplicate_dataset",
    "message": "A dataset with the same filename already exists.",
    "detail": {
      "match_type": "filename",          // "filename" | "content" | "both"
      "existing_dataset_id": "uuid",
      "existing_filename": "sales.csv",
      "existing_created_at": "2026-06-17T10:00:00Z"
    }
  }
}
```

The frontend uses this to show a confirmation dialog. If the user chooses to proceed, the same file is re-submitted with `?force=true` appended to the upload URL, which bypasses the duplicate check.

## Behavior

1. **Content hashing.** When a file is uploaded, compute `SHA-256(file_bytes)` before writing to disk. This is the `content_hash`.

2. **Duplicate check order:**
   a. Query `datasets` for a row where `content_hash = <hash>` → match type `"content"`
   b. Query `datasets` for a row where `filename = <uploaded_filename>` → match type `"filename"`
   c. If both match the same row → match type `"both"`
   d. If no match → proceed with normal upload

3. **Force flag.** If `?force=true` is present on the request, skip the duplicate check entirely and upload normally.

4. **Client confirmation flow:**
   - On 409: show a dialog with the match type, the existing dataset name and upload date, and two buttons:
     - **Use existing** — close the dialog; set the dataset selector to the existing dataset (no upload)
     - **Upload anyway** — re-submit the same file with `?force=true`
   - Cancel dismisses the dialog with no action.

5. **"Use existing" shortcut.** The 409 response includes `existing_dataset_id`. The frontend can select this dataset in the dropdown immediately without a second request.

## Data model changes

- **`datasets`**: add `content_hash TEXT NOT NULL DEFAULT ''`
  - Populated on every new upload; empty string for datasets uploaded before this capability was added (backward compatible)

## API changes

- `POST /upload` accepts optional query param `?force=true`
- New 409 response shape (see above)

## UI changes

- On 409: render a confirmation dialog (not a browser `confirm()`) with:
  - Match description: *"A file named **sales.csv** was already uploaded on Jun 17, 2026."*
  - **Use existing** button (primary) + **Upload anyway** button (secondary) + **Cancel** link
- The dialog is rendered in-page (a `<div>` overlay), not a native browser dialog.

## Acceptance criteria

- [ ] Uploading the same file twice returns 409 on the second attempt
- [ ] 409 body includes `match_type`, `existing_dataset_id`, `existing_filename`, `existing_created_at`
- [ ] Uploading with `?force=true` succeeds even when duplicate exists
- [ ] Uploading a file with the same content but a different name returns 409 with `match_type: "content"`
- [ ] Uploading a file with the same name but different content returns 409 with `match_type: "filename"`
- [ ] Datasets uploaded before this capability (empty `content_hash`) do not trigger false positive content matches
- [ ] UI confirmation dialog appears on 409; "Use existing" selects the dataset without re-uploading; "Upload anyway" completes the upload
