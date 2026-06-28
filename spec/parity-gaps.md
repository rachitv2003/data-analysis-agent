# Parity Gaps — reaching feature-parity with the reference build (A)

> **Reference (A):** `data-analysis-agent` (`feature/v0.5`) — the original, battle-tested build.
> **This repo (B):** the gen-3-harness rebuild of A. Source for capability IDs: `spec/capabilities/index.md` (C1–C32; C5/C28 don't exist → 30 capabilities).
>
> Audit date: **2026-06-29**, verified against actual code + tests (not commit messages).
> **Scoreboard: 20 / 30 at parity or ahead · 10 gaps remaining** (3 correctness/overclaim · 3 cheap functional · 4 ingestion-UX).
>
> Drive each item with `/zero-shot-fix` from this repo. Every fix must pass the phase gate against the **real Gemini key** in `.env` before it counts — a stubbed pass does not.

---

## Step 0 — re-verify against current HEAD (do first)

This branch is fully pushed to its upstream (`fork/feature/data-analysis-agent-v0.1`); no work is at risk. **But the audit predates several D-series `fix(drift)` commits** — at least **C10** (filename-dup) and **C16** (notes_file) already have fix commits on HEAD. Before grinding the list below, re-verify each open item against current HEAD (`8c25295`) — some Tier-2/Tier-3 gaps may already be closed.

---

## Tier 1 — Correctness & overclaims (cheap, highest trust-value)

- [x] **C31 — Semantic compression wired (2026-06-29).** `setup` now injects compressed `context_facts` into the dataset context (prefer-facts), with raw-notes fallback + lazy fire-and-forget self-heal when notes exist but facts don't — matching A. Tests in `tests/unit/test_graph_stub.py` (prefer-facts / fallback+self-heal / pure-decision). Offline gate green (197 passed). *Real-Gemini gate (`extract_facts` quality) still to run.*
  - Files: `src/graph/nodes.py` (`_dataset_notes_for_prompt`, `_facts_as_notes`, `_trigger_facts_self_heal`, `setup` call site)

- [ ] **C27 — Session DataFrame cache can serve stale frames.** Count-based LRU only; missing `_invalidate_dataset` on clean / re-derive / delete (and A's byte-bounded eviction via `memory_usage` / `cache_limit_mb`). A cleaned or re-derived dataset can return cached pre-change rows within a session. Add invalidation (and ideally the byte bound).
  - Files: `src/graph/nodes.py` (cache block), call sites in `src/api/datasets_ops.py`, `src/graph/derived.py`

- [ ] **C29 — Live context-window display computed but not rendered.** The per-component prompt breakdown is built (`runner.py`) and shipped to the browser but no UI renders it. Add the "Prompt breakdown" panel (A shows it in the steps inspector). Consider `last_prompt` accumulation across iterations to match A.
  - Files: `frontend/src/components/analyse/StepsInspector.tsx` (add panel), `frontend/src/components/analyse/ConversationCard.tsx`

---

## Tier 2 — Cheap functional gaps

- [ ] **C8 — No column cap on result stringify.** Rows + total chars are capped, but very wide DataFrames fed back to the LLM aren't column-bounded (A caps 20 cols with a "showing X of N columns" note). Add a column cap.
  - Files: `src/graph/sandbox.py` (`_stringify`)

- [ ] **C11 — Multi-format parsing is shallow + untested.** TXT is parsed tab-only (no delimiter sniff); JSON uses bare `pd.read_json` (fails on column-keyed / dict-of-lists shapes A handles). No non-CSV format has a test. Deepen parsing and add format tests (TSV / TXT-sniff / JSON-variants / Excel).
  - Files: `src/api/upload.py` (`_parse_dataframe`), `tests/unit/` + `tests/integration/`

- [ ] **C14 — Multi-dataset querying wired but unproven.** Sandbox builds `df` / `df1` / `df2` + filename aliases and `/ask` accepts `dataset_ids`, but no test drives an actual cross-dataset join/answer through the agent. Add an end-to-end multi-dataset test.
  - Files: `tests/integration/`

---

## Tier 3 — Ingestion UX (one workstream — do C16 + C13 + C17 together)

- [ ] **C9 — Session bulk multi-select.** Sidebar has per-row delete + clear-all, but no checkbox multi-select / bulk-delete (A has `sess-check` + `deleteSelectedSessions`). Add multi-select UI + a bulk-delete-by-ids endpoint.
  - Files: `frontend/src/components/analyse/SessionSidebar.tsx`, `src/api/sessions.py`

- [ ] **C16 — Notes file on upload.** ⚠️ **Re-verify first** — a `fix(drift): implement C16 notes_file` commit exists on HEAD; this may already be closed. Audit (pre-fix) found: UI never sends `notes_file`; backend *replaces* context instead of *appending*, no `.txt/.md` validation, no combined 4000-char cap. Confirm current state, then close any remainder.
  - Files: `frontend/src/components/analyse/UploadCard.tsx`, `frontend/src/lib/api.ts`, `src/api/upload.py`

- [ ] **C13 + C17 — Folder drop + staged upload queue.** No folder drop (`webkitGetAsEntry`) and no editable pre-commit staging queue — files upload immediately on select. Add the staged queue (review / annotate / rename / remove before commit) and folder drop; pairs with C16's per-file notes.
  - Files: `frontend/src/components/analyse/UploadCard.tsx`, `frontend/src/lib/api.ts`

---

## Already at parity (no action) — for reference

C1, C2, C3, C4, C6, C7, **C10** (filename-dup fixed 2026-06-29), C12, C15, C18, C19, C20, **C21** (B-ahead: adds Anthropic provider), C22, C23, C24, C25, C26, C30, C32.
