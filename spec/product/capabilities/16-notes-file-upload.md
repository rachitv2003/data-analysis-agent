# Capability: Dataset Notes as File Upload

**Status:** draft

## Purpose

When uploading a folder of files the inline notes textarea is impractical — the user can't type different notes per file in a queue. This capability lets users attach a notes file (plain `.txt` or `.md`) to a dataset instead of (or in addition to) typing notes inline. The notes file's content is treated identically to typed context and is injected into every agent prompt.

## Inputs

`POST /upload` gains an optional second file field:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| file | file | yes | Data file (.csv/.tsv/.txt/.json) |
| context | string (form) | no | Typed notes (existing C12 field) |
| notes_file | file | no | Plain-text notes file (.txt or .md). Max 4 000 chars after reading. |

If both `context` and `notes_file` are provided, their contents are concatenated with a blank line separator, subject to the 4 000 char limit on the combined result.

## Notes file discovery during folder upload (C13)

When uploading a folder, a notes file can be co-located with the data files using a naming convention:

- `<dataset-stem>.notes.txt` or `<dataset-stem>.notes.md` — applies to the matching data file only.  
  Example: `sales.csv` + `sales.notes.txt`
- `_notes.txt` or `_notes.md` (folder-level) — applies to all data files in the folder that do not have their own notes file.

During folder drop processing (C13), before queuing an upload for `sales.csv`, the client checks the dropped entries for a matching `sales.notes.txt`. If found, it reads the file content and passes it as the `context` form field of that upload. The folder-level `_notes.txt` is used as a fallback for files with no matching notes file.

Notes files themselves are not uploaded as datasets — they are filtered out of the upload queue.

## Behavior

1. **Upload with notes_file**: server reads the notes file bytes, decodes as UTF-8, strips leading/trailing whitespace.
2. **Merging**: if `context` is also non-empty, combine as `{context}\n\n{notes_file_content}`. If combined length > 4 000 chars, return HTTP 400 `context_too_long`.
3. **Storage**: merged text stored in `datasets.context` exactly as typed context is today.
4. **UI**: the "Add dataset notes" section gets a second option: *"Or attach a notes file (.txt / .md)"* — a small file input that accepts `.txt` and `.md`. Choosing a file previews its first 200 chars inline.

## API changes

- `POST /upload`: optional `notes_file` multipart file field
- No new endpoints needed

## Data model changes

None — stored in existing `datasets.context`.

## Acceptance criteria

- [ ] Upload with `notes_file` stores the file's text content in `datasets.context`
- [ ] Upload with both `context` and `notes_file` stores the concatenated text
- [ ] Combined context > 4 000 chars returns 400 `context_too_long`
- [ ] Non-UTF-8 notes file returns 400 `notes_file_encoding_error`
- [ ] Notes file with unsupported extension (e.g. `.pdf`) returns 400 `unsupported_notes_format`
- [ ] Folder upload: `sales.notes.txt` auto-attached to `sales.csv` upload
- [ ] Folder upload: `_notes.txt` applied as fallback to files with no matching notes file
- [ ] UI: notes file picker shows a short preview of file content before uploading
- [ ] Integration test: upload with `notes_file`, ask a question, verify notes appear in prompt
