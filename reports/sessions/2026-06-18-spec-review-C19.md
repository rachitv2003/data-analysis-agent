# Spec Review — C19: Automatic Dataset Selection

**Date:** 2026-06-18
**Reviewer:** spec-reviewer sub-agent
**Branch:** feature/data-analyst-v0.1

---

## Status: NEEDS REVISION

One critical issue and several minor issues must be resolved before implementation begins.

---

## Critical Issues

### 1. Session dataset mismatch — C19 auto-selection breaks the C14 session constraint

**File:** `spec/product/capabilities/19-automatic-dataset-selection.md`, Functional Requirements §9 and Agent/Graph Changes

**Problem:** C14 enforces that all follow-up questions in a session use the same `dataset_ids` set as the first turn (`runner.py` lines 36-42: `sorted(sess_ids) != sorted(dataset_ids)` → 400). With C19, the first turn might auto-select `[uuid-1]` and store that set on the session. A follow-up question that happens to be relevant to `uuid-2` will auto-select `[uuid-1, uuid-2]` — or a different subset — causing a 400 `session_dataset_mismatch` error the user never asked for.

The spec does not address how C19 interacts with the C14 session constraint at all. Three valid resolutions exist (relax the constraint for auto-selected sessions, fix the selection set at session creation time, or always pass all dataset IDs for consistency-checking) — but the spec is silent. An implementer cannot make this call without product guidance.

**This is a blocker.** The interaction must be specified before coding begins.

---

## Minor Issues

### 2. `columns_json` stores column names only — no dtypes

**File:** `spec/product/capabilities/19-automatic-dataset-selection.md`, Data Model Changes assumption block

The spec itself notes this assumption: "If it also stores dtypes, those should be surfaced in the selector prompt." The codebase confirms `columns_json` is produced by `df.columns.tolist()` (`upload.py` line 111), which yields column names only, not dtypes. The selector prompt template shown in the spec includes column names (`id, product, revenue, region`) without dtypes — that is consistent with what's available. However, the assumption block says "agent-builder should confirm the exact shape" — this should be closed out as a confirmed fact, not left open, because it affects the prompt template and the integration test assertions.

**Recommendation:** Remove the assumption block and state definitively: `columns_json` is a JSON array of column name strings only; dtypes are not available without loading the file.

### 3. `dataset_ids_json` null-behaviour inconsistency with C14

**File:** `spec/product/capabilities/19-automatic-dataset-selection.md`, Functional Requirement §8; `src/data_analyst/graph/runner.py` line 26

C14's existing code sets `dataset_ids_json = None` when only one dataset is used (`len(dataset_ids) > 1`). FR §8 says `dataset_ids_json` is "populated with the final selected dataset IDs so the full run record is self-contained" — implying it should always be populated by C19. These two behaviours conflict when the selector returns a single dataset.

**Recommendation:** Clarify whether C19 always writes `dataset_ids_json` (overriding the C14 null-for-single convention) or only writes it for multi-dataset selections. The acceptance criterion "query_runs.dataset_ids_json is populated with the IDs the selector chose" implies always-populated, but the existing runner will need to be updated explicitly.

### 4. `selector_reasoning` not added to `AgentState` in `07-agent-graph.md`

**File:** `spec/product/07-agent-graph.md`; `spec/product/capabilities/19-automatic-dataset-selection.md`, Agent/Graph Changes

The C19 spec adds `selector_reasoning: str | None` to `AgentState`. The `07-agent-graph.md` state definition block does not reflect this. The actual `state.py` also does not have it yet. Either `07-agent-graph.md` needs updating as part of C19's spec, or the spec needs to explicitly state "07-agent-graph.md must be updated by the implementer" so it doesn't get missed.

### 5. `ask.py` response envelope does not include `selector_reasoning`

**File:** `spec/product/capabilities/19-automatic-dataset-selection.md`, API Changes — Response

The spec defines a response with `selector_reasoning` in the JSON. The current `ask.py` (line 54-64) constructs the `ok(...)` dict from `run` fields. The spec should call out explicitly that `run.selector_reasoning` must be added to the response dict — the existing code will not include it automatically.

### 6. `POST /ask` validation must be updated — current validator rejects no-dataset requests

**File:** `src/data_analyst/api/ask.py` lines 20-26; `spec/product/capabilities/19-automatic-dataset-selection.md`, Functional Requirement §1

The current `model_validator` raises `ValueError("Provide dataset_id or dataset_ids")` when both are `None`. FR §1 says "The endpoint no longer requires `dataset_ids` to be supplied." The spec shows the updated `AskRequest` model, which is correct — but it does not explicitly call out that the existing validator must be removed/replaced. An implementer reading the spec in isolation might miss this unless they diff the old and new `AskRequest` carefully.

**Recommendation:** Add a note: "The `model_validator` that rejects `(dataset_id=None, dataset_ids=None)` must be removed or replaced."

### 7. Stub provider detection heuristic is fragile

**File:** `spec/product/capabilities/19-automatic-dataset-selection.md`, Stub Provider Extension

The stub branches on `"dataset IDs"` appearing in the prompt text. The actual prompt template uses the phrase `"dataset IDs"` only once (in the instruction line). If that phrasing changes during implementation, the stub silently falls back to the default behaviour and every test fails in unexpected ways. A more robust signal (e.g., a dedicated `<node:select>` tag analogous to `<node:plan>`) should be specified.

---

## Assumptions to Confirm

1. **`columns_json` shape** — confirmed by code: column names only, no dtypes. The assumption block in the spec should be replaced with this stated fact.
2. **All datasets are globally accessible** — the spec assumes `GET /datasets` returns all datasets for all sessions. If multi-user support is ever added, this becomes a scoping issue. Acceptable for v0.1 single-user scope, but worth a note.

---

## Looks Good

- **Fallback behaviour is well-specified** (FR §6): empty array, malformed JSON, and exception each trigger the same safety-net path with a WARN log. This is unambiguous and testable.
- **Opt-out path is clean** (FR §9): explicit `dataset_ids` in the request body skips the selector entirely. The interaction with the existing C14 code path is clear for the non-session case.
- **Acceptance criteria are largely testable.** Most criteria have concrete, binary pass/fail conditions. The integration tests for non-overlapping columns and join queries (last two ACs) are particularly well-written.
- **`select_datasets` is correctly placed outside the LangGraph graph** — running it synchronously in the route handler before `run_agent` is the right design and the spec makes this explicit.
- **Data model change is minimal and consistent.** `selector_reasoning TEXT nullable` follows the exact same pattern as every other nullable TEXT field in `QueryRunRow`. No schema conflicts.
- **00-index.md** — C19 was added correctly at row 19, Phase 9, status `draft`.
- **02-architecture.md** — data flow step 3 was added correctly and matches the C19 spec description precisely.
- **No conflict with C14** for the single-turn, explicit-IDs case — C19 is strictly additive in that path.
