# Session DataFrame Cache and Parquet Pre-conversion

**Status:** planned
**Covers:** C27 (session-scoped DataFrame cache + Parquet pre-conversion)

Two complementary optimisations that eliminate repeated disk I/O across queries in the same session:

1. **Session cache** — DataFrames are kept in server RAM keyed by `session_id`. Subsequent queries in the same session skip disk reads entirely.
2. **Parquet pre-conversion** — files are written as Parquet on upload. Cache misses (first query in a session) load from Parquet instead of CSV, which is 3–10× faster.

These are independent; either can be active without the other, but together they make the `setup` node near-instant for all but the very first question in a session.

---

## Session-scoped DataFrame Cache

### Data structure

```python
# nodes.py — module-level
_session_cache: dict[str, dict[str, pd.DataFrame]] = {}
# key: session_id → {dataset_id: DataFrame}

_cache_lru: list[tuple[str, str]] = []
# ordered list of (session_id, dataset_id) pairs, newest-last
# per-DataFrame granularity — evicts the least-recently-used individual DataFrame

_cache_bytes: int = 0  # total bytes across all cached DataFrames
# configurable limit via DATA_ANALYST_CACHE_LIMIT_MB (default 1024 MB)
```

DataFrame memory is estimated via `df.memory_usage(deep=True).sum()`.

### Cache hit/miss in `setup`

For each `dataset_id` in `state["dataset_ids"]`:

1. If `session_id` is set and `_session_cache[session_id][dataset_id]` exists → **cache hit**, use it directly.
2. Otherwise → **cache miss**: load from disk (Parquet if available, else CSV), store in `_session_cache[session_id][dataset_id]`, update LRU and byte count.

Single-turn queries (no `session_id`) bypass the session cache entirely and use the existing run-scoped `_dataframes[run_id]` dict, which is cleared at finalize as before.

### LRU eviction (1 GB limit)

After every cache write:

```
while _cache_bytes > limit:
    oldest_session, oldest_dataset = _cache_lru.pop(0)   # list[0] = least recently used
    evicted = _session_cache[oldest_session].pop(oldest_dataset, None)
    if evicted is not None:
        _cache_bytes -= df_bytes(evicted)
    if not _session_cache[oldest_session]:
        del _session_cache[oldest_session]
```

On every cache **read**, the `(session_id, dataset_id)` pair is moved to the end of `_cache_lru` (mark as most recently used). Eviction is at DataFrame granularity — a single DataFrame is evicted per step, not an entire session.

### Session-delete eviction

When `DELETE /sessions/{id}` is called, `_evict_session(session_id)` is called immediately in addition to the database cascade. This frees RAM synchronously.

### Staleness invalidation

The following operations call `_invalidate_dataset(dataset_id)`, which removes the dataset from **every** session's cache entry:

| Trigger | Why stale |
|---------|-----------|
| `POST /datasets/{id}/clean/apply` (C24) | CSV on disk was rewritten |
| `POST /datasets/{id}/re-derive` (C25) | CSV on disk was rewritten |
| `DELETE /datasets/{id}` | Dataset gone; sessions using it will error anyway |
| C25 `save_dataset()` — re-save of existing name | New derived content replaces old |

`_invalidate_dataset` does not evict the whole session — it removes only the stale key from each session's sub-dict, leaving other DataFrames in that session intact.

### C25 synergy — zero first-load for derived datasets

When C25's `save_dataset(df, name)` creates a new derived dataset mid-session, it immediately injects the DataFrame into `_session_cache[session_id][new_dataset_id]`. The next query in the same session reads the derived DataFrame from RAM with no disk I/O.

---

## Parquet Pre-conversion

### On upload

After `parse_file()` produces a DataFrame and before the HTTP response is returned:

1. Write `df.to_parquet(f"uploads/{dataset_id}.parquet", index=False, engine="pyarrow")`.
2. If the write succeeds, set `DatasetRow.parquet_path = f"uploads/{dataset_id}.parquet"`.
3. If the write fails (missing `pyarrow`, disk error), log at WARN and continue — the CSV is the fallback. `parquet_path` stays NULL.

### Loading preference in `setup`

```
if dataset.parquet_path and os.path.exists(dataset.parquet_path):
    df = pd.read_parquet(dataset.parquet_path)
else:
    df = pd.read_csv(dataset.file_path)
```

### Parquet regeneration

When the underlying data changes, the Parquet is regenerated to stay in sync:

| Trigger | Action |
|---------|--------|
| `POST /datasets/{id}/clean/apply` (C24) | Re-write Parquet from the new CSV after apply succeeds |
| `POST /datasets/{id}/re-derive` (C25) | Write Parquet from the re-derived DataFrame |
| C25 `save_dataset(df, name)` | Write Parquet directly from the DataFrame (no intermediate CSV read) |

If Parquet regeneration fails, log at WARN and clear `parquet_path` to NULL — the CSV fallback ensures correctness.

### Why keep CSV alongside Parquet?

- C24 data cleaning currently writes the cleaned DataFrame to CSV. Parquet is derived from it.
- CSV is human-inspectable and universally compatible.
- `parquet_path = NULL` is a clean degraded state that keeps the system functional.
- Dropping CSV entirely would require migrating C24, C25, and the upload handler — deferred.

---

## New Environment Variable

| Variable | Default | Description |
|----------|---------|-------------|
| `DATA_ANALYST_CACHE_LIMIT_MB` | `1024` | Maximum total RAM for the session DataFrame cache in MB |

---

## Data Model

`DatasetRow` gains one new column — see `spec/product/04-data-model.md`.

---

## Implementation

| File | Role |
|------|------|
| `src/data_analyst/graph/nodes.py` | Session cache (`_session_cache`, `_cache_lru`, `_cache_bytes`); updated `setup` node; `_invalidate_dataset`; `_evict_session`; `save_dataset` injects into session cache |
| `src/data_analyst/api/upload.py` | Parquet write after parse; set `DatasetRow.parquet_path` |
| `src/data_analyst/api/clean.py` | Parquet regeneration after apply; call `_invalidate_dataset` |
| `src/data_analyst/api/datasets.py` | Call `_evict_session` on session delete; call `_invalidate_dataset` on dataset delete |
| `src/data_analyst/config/settings.py` | `cache_limit_mb: int = 1024` |
| `src/data_analyst/db/models.py` | `DatasetRow.parquet_path` column |
