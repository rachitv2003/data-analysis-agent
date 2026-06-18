# Data Ingestion

**Status:** implemented
**Covers:** C1 (CSV upload), C10 (duplicate detection), C11 (multi-format), C13 (multi-file / staged upload), C16 (notes file), C17 (deferred upload)

Accepts tabular data files from the browser, validates them, detects duplicates, parses them into pandas DataFrames, and persists metadata to SQLite. All files are normalised to CSV on disk regardless of source format.

---

## Supported Formats

| Extension | Parser |
|-----------|--------|
| `.csv`    | `pd.read_csv` (UTF-8, errors=replace) |
| `.tsv`    | `pd.read_csv` with `sep="\t"` |
| `.txt`    | `pd.read_csv` with `sep=None, engine="python"` (auto-detect delimiter) |
| `.json`   | Custom: tries array-of-objects → first list value in a dict → column-keyed dict |

`detect_format` in `src/data_analyst/utils/file_parser.py` maps the file extension to a format string and rejects anything not in `{.csv, .tsv, .txt, .json}`.

---

## Upload Endpoint

`POST /upload` — `multipart/form-data`

| Field        | Type   | Required | Description |
|--------------|--------|----------|-------------|
| `file`       | file   | yes      | The data file |
| `context`    | string | no       | User-typed notes for this dataset, max 4 000 chars |
| `notes_file` | file   | no       | `.txt` or `.md` file; its content is appended to `context` |
| `force`      | bool   | no (query param) | Skip duplicate check and overwrite |

### Duplicate Detection

Before parsing, the endpoint computes `SHA-256(raw_bytes)` and checks for an existing `DatasetRow` with a matching `content_hash` or `filename`. If a duplicate is found and `force=False`, the response is HTTP 409:

```json
{
  "detail": {
    "code": "duplicate_dataset",
    "message": "...",
    "match_type": "content | filename | both",
    "existing_dataset_id": "uuid",
    "existing_filename": "...",
    "existing_created_at": "..."
  }
}
```

The UI shows a modal with "Use existing" / "Upload anyway" / "Cancel" options.

### File Parsing

After duplicate check, `parse_file(raw, fmt)` returns a `pd.DataFrame`. If the DataFrame is empty or has zero columns, the upload is rejected with `400 empty_file`.

### Persistence

The file is saved as `uploads/{dataset_id}.csv` (always CSV regardless of source format). A `DatasetRow` is created with `filename`, `file_path`, `row_count`, `col_count`, `columns_json`, `content_hash`, `format`, and `context`.

Success response:
```json
{
  "data": {
    "dataset_id": "uuid",
    "filename": "sales.csv",
    "format": "csv",
    "context": "",
    "row_count": 1000,
    "col_count": 8,
    "columns": ["date", "region", "revenue", "units"]
  }
}
```

---

## Staged Upload (Deferred / Multi-file)

Files are not uploaded immediately on selection. The UI stages them in a client-side `Map` (`_staged`) so the user can review, annotate, and remove files before committing.

**Staging flow:**
1. User drops files or a folder onto the drop zone, or clicks "Choose files".
2. Each file appears in a staged row with filename, size, format badge, and optional notes.
3. User may attach a typed context note (textarea) or a `.txt`/`.md` notes file per file.
4. "Upload N file(s)" button fires `uploadStaged()`, which processes up to 3 files concurrently.

**Folder drop:** When a folder is dropped, the client reads it via `webkitGetAsEntry`. Files named `_notes.txt`, `context.txt`, `readme.txt`, `description.txt`, `info.txt` (and `.md` equivalents) are treated as folder-level notes and pre-populated into the context field of every data file in that folder. Files named `<stem>.notes.txt` / `<stem>.notes.md` are pre-populated into the context of the file with the matching stem.

**Notes file:** The `notes_file` form field accepts `.txt` or `.md`. The server reads it as UTF-8, appends its content to `context` (with a blank-line separator if context was already non-empty), and enforces the 4 000-char cap on the combined result.

---

## Implementation

| File | Role |
|------|------|
| `src/data_analyst/api/upload.py` | Route handler: duplicate check, parse, persist |
| `src/data_analyst/utils/file_parser.py` | `compute_hash`, `detect_format`, `parse_file`, `_parse_json` |
| `src/data_analyst/db/models.py` | `DatasetRow` ORM model |
| `src/data_analyst/templates/index.html` | Drop zone, staged-file list, upload queue, duplicate modal |
