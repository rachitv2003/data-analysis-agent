# C31 — Semantic Context Compression

**Status:** implemented
**Covers:** C31 (semantic context compression)

---

## Overview

Instead of injecting the full raw text of dataset notes and global memory into every agent prompt, an LLM extraction step distils each text into a compact, structured list of atomic facts. The facts are stored separately and injected at query time in place of the full text, dramatically reducing per-prompt token cost while preserving analytical fidelity.

---

## Scope

Two text sources are compressed:

| Source | Raw storage | Compressed storage |
|--------|-------------|-------------------|
| Dataset context notes (C12) | `DatasetRow.context` | `DatasetRow.context_facts` |
| Global memory | `settings['global_memory']` | `settings['global_memory_facts']` |

The raw text is always preserved (it is what the user sees and edits). Only the compressed representation changes what the agent receives in the prompt.

---

## Extraction Process

### Prompt

```
Extract the key analytical facts from the following text as a JSON array of strings.
Each element must be one atomic, self-contained fact written as a concise statement.
Do not include formatting notes, column type definitions that are already in the schema, or redundant phrasing.
Maximum 20 facts. Return ONLY a valid JSON array — no markdown fences, no other text.

Text:
{source_text}
```

### Response

```json
["Revenue is reported in USD thousands",
 "customer_id uniquely identifies each customer",
 "Dates span 2016-09-04 to 2018-08-29",
 "order_status has 8 values: delivered, shipped, processing, unavailable, canceled, invoiced, approved, created",
 "~3% of rows have null review_score"]
```

### Failure handling

If the extraction LLM call fails or returns non-parseable JSON, `context_facts` / `global_memory_facts` is left NULL. At query time, the fallback path injects the raw text instead (see Injection section), and the **lazy self-heal** (below) re-attempts compression the next time the agent loads that source — so a transient failure no longer strands a dataset on raw text permanently.

---

## Trigger

Compression runs asynchronously after the source text changes (write-path triggers) or is detected to be missing at read time (lazy self-heal):

| Event | Mechanism | Compression triggered for |
|-------|-----------|--------------------------|
| `PATCH /datasets/{id}/context` saves | FastAPI `BackgroundTasks` | `DatasetRow.context_facts` for that dataset |
| C30 auto-notes background task completes | in-task synchronous call | `DatasetRow.context_facts` for that dataset |
| `PATCH /memory` saves | FastAPI `BackgroundTasks` | `settings['global_memory_facts']` |
| Agent `setup()` loads a dataset with `context` set but `context_facts` NULL | lazy self-heal (daemon thread) | `DatasetRow.context_facts` for that dataset |
| `plan_action` injects global memory with `global_memory` set but `global_memory_facts` NULL | lazy self-heal (daemon thread) | `settings['global_memory_facts']` |

The triggering endpoint/node does not wait for compression — it responds immediately and the extraction happens in the background. The current prompt call uses whichever facts are available at that point (raw text if facts are not yet ready); the next call uses the freshly-compressed facts.

### Lazy on-read self-heal

The write-path triggers only fire when the user changes text through an endpoint. They do **not** cover:

- Datasets whose notes were set before C31 existed (`context_facts` permanently NULL).
- Any dataset whose compression failed once (transient LLM error / unparseable output).

To close this gap, the agent self-heals on read: whenever it loads a source that has raw text but no facts, it fires a fire-and-forget background compression (`compress_dataset_context_async` / `compress_memory_async`). That turn still injects raw text; the next turn uses the compact facts. This makes compression eventually-consistent across all existing data without a startup backfill or manual re-save.

### Concurrency guard

The write-path background task and the lazy self-heal can target the same source simultaneously (e.g. rapid-fire queries on a freshly-edited dataset). An in-process guard (`_inflight` set + lock in `compress.py`) ensures at most one compression runs per target at a time; redundant concurrent calls are skipped, not queued.

### Staleness — clear facts on write

When raw text is updated, the old facts describe the **previous** text and must not be served during the recompression window. Both `PATCH /datasets/{id}/context` and `PATCH /memory` therefore set the corresponding `*_facts` field to NULL **before** committing the new text. Until recompression completes, the injection fallback serves the fresh raw text (correct, slightly larger) rather than stale facts (compact but wrong). Recompression — via the background task and, as a backstop, the lazy self-heal — then repopulates the facts.

---

## Injection at Query Time (C12 override)

`plan_action` builds the prompt section for dataset notes and memory as follows:

**Dataset notes (per dataset):**
```
if context_facts is not None:
    inject "Dataset facts ({filename}):\n- {fact1}\n- {fact2}\n..."
else if context is not None:
    inject "Dataset notes ({filename}):\n{context}"   # fallback: raw text
```

**Global memory:**
```
if global_memory_facts is not None:
    inject "Memory facts:\n- {fact1}\n- {fact2}\n..."
else if global_memory is not None:
    inject "Memory:\n{global_memory}"   # fallback: raw text
```

This is the only change to the existing `plan_action` prompt build — no other nodes are affected.

---

## Data Model Changes

### `datasets`

| Column | Type | Nullable | Default | Description |
|--------|------|----------|---------|-------------|
| `context_facts` | TEXT | yes | NULL | JSON array of extracted fact strings; NULL until first extraction completes or if extraction failed |

### `settings` — new keys

| Key | Value | Description |
|-----|-------|-------------|
| `global_memory_facts` | TEXT (JSON array) | Extracted facts from `global_memory`; NULL until first extraction |

---

## API Changes

No new endpoints. The compression is fully transparent to API callers — they continue to read/write `context` and `memory` as before. Compressed facts are internal implementation detail.

---

## Effect on Token Usage

A typical 300-word dataset notes field (~400 tokens) compresses to ~10 facts (~150 tokens) — roughly 60% reduction. For memory, the savings depend on content length but are proportional. The C29 sidebar estimate does not reflect this saving (it uses raw text length for simplicity); the steps inspector actuals reflect the true injected size post-compression.

---

## Out of Scope

- Embedding-based retrieval (vector search over facts). Facts are always injected in full (they are already compact).
- Incremental fact extraction (detecting which sentences changed): full re-extraction on every save.
- User visibility into the extracted facts: facts are internal. The editable field always shows the human-readable raw text.
- Compressing `conversation_history` turns (they are already bounded by the session turn limit and are cleared per session).
