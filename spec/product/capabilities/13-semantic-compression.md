# C31 — Semantic Context Compression

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

If the extraction LLM call fails or returns non-parseable JSON, `context_facts` / `global_memory_facts` is left as the previous value (or NULL). At query time, the fallback path injects the raw text instead (see Injection section).

---

## Trigger

Compression runs asynchronously (FastAPI `BackgroundTasks`) after the source text changes:

| Event | Compression triggered for |
|-------|--------------------------|
| `PATCH /datasets/{id}/context` saves | `DatasetRow.context_facts` for that dataset |
| C30 auto-notes background task completes | `DatasetRow.context_facts` for that dataset |
| `PATCH /memory` saves | `settings['global_memory_facts']` |

The triggering endpoint does not wait for compression — it responds immediately and the extraction happens in the background. The next prompt call will use whichever facts are available at that point.

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
