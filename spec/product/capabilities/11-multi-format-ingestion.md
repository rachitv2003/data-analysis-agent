# Capability: Multi-Format Data Ingestion

**Status:** draft

## Purpose

Extend the upload endpoint to accept TSV, TXT (delimiter-sniffed), and JSON files in addition to CSV. All formats are normalised to a pandas DataFrame in memory before the dataset is registered — the rest of the pipeline (agent loop, token tracking, session management) is unchanged.

## Supported formats

| Extension | Description | Parsing strategy |
|-----------|-------------|------------------|
| `.csv`    | Comma-separated values | `pd.read_csv(path)` |
| `.tsv`    | Tab-separated values   | `pd.read_csv(path, sep='\t')` |
| `.txt`    | Text, delimiter unknown | `pd.read_csv(path, sep=None, engine='python')` — pandas auto-detects the delimiter |
| `.json`   | JSON data | See JSON rules below |

### JSON parsing rules (in order)

1. **Array of objects** — `[{"col": val}, ...]` → `pd.read_json(path, orient="records")`
2. **Object with list value** — `{"data": [{...}, ...]}` — take the first key whose value is a list and parse it
3. **Nested orient fallback** — try `pd.read_json(path)` with pandas default orient (handles column-keyed `{"col": [v1, v2]}` form)
4. If all three fail → return HTTP 400: `{"code": "unsupported_json_shape", "message": "..."}`

## Inputs

`POST /upload` — same as today. The file MIME type and extension are both checked.

## Outputs

Same as the current CSV upload response:
```json
{
  "data": {
    "dataset_id": "uuid",
    "filename": "events.json",
    "format": "json",
    "row_count": 500,
    "col_count": 8,
    "columns": ["id", "name", "..."]
  }
}
```

`format` is a new field added to the response (and stored in the DB).

## Behavior

1. **Extension-based dispatch.** The upload handler maps the lowercased file extension to a parsing function. Unknown extensions return HTTP 400.

2. **MIME type is advisory only.** Browsers sometimes send `text/plain` for TSV files or `application/octet-stream` for JSON. Do not reject based on MIME type alone; use extension as the primary signal.

3. **Encoding.** All formats are read with `encoding="utf-8"` with `errors="replace"` fallback. Non-UTF-8 files are accepted but replacement characters may appear in data.

4. **Validation after parsing.** After loading into a DataFrame, apply the same validations as CSV:
   - At least 1 row and 1 column
   - At least 1 column with a non-empty name
   - Row count ≤ 1,000,000 (soft limit — warn but allow)

5. **File storage.** The raw uploaded file is stored as-is in `uploads/`. The format is recorded in the DB so the file can be re-read later if needed (e.g., for re-ingestion).

6. **UI file picker.** The `<input type="file">` `accept` attribute is updated to include `.csv,.tsv,.txt,.json`.

## Data model changes

- **`datasets`**: add `format TEXT NOT NULL DEFAULT 'csv'`
  - Values: `'csv'`, `'tsv'`, `'txt'`, `'json'`
  - Backward-compatible default for existing rows

## API changes

- `POST /upload`: accepts `.csv`, `.tsv`, `.txt`, `.json`
- `POST /upload` response: add `format` field
- New error code: `unsupported_json_shape` (HTTP 400)
- New error code: `unsupported_format` (HTTP 400) for unknown extensions

## UI changes

- File picker `accept` attribute: `".csv,.tsv,.txt,.json"`
- Dataset info display after upload: show `format` badge alongside filename

## Acceptance criteria

- [ ] `.tsv` file uploads successfully; `row_count` and `col_count` are correct
- [ ] `.txt` file with comma delimiter is parsed correctly (auto-sniff)
- [ ] `.txt` file with pipe delimiter (`|`) is parsed correctly
- [ ] `.json` array-of-objects format uploads and parses correctly
- [ ] `.json` column-keyed format (`{"col": [v1, v2]}`) uploads and parses correctly
- [ ] `.json` with unknown shape returns 400 with `unsupported_json_shape`
- [ ] Unknown extension (e.g. `.xlsx`) returns 400 with `unsupported_format`
- [ ] `format` field present in upload response and stored in DB
- [ ] Existing CSV datasets are unaffected (format defaults to `'csv'`)
- [ ] UI file picker shows `.tsv`, `.txt`, `.json` as selectable types
