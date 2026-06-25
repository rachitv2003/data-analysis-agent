# C32 — Collapsible Conversation Turns

**Status:** implemented
**Covers:** C32 (collapsible conversation turns)

---

## Overview

Each query turn in the conversation thread can be individually collapsed and expanded, hiding the full answer body while keeping the question visible. This mirrors Jupyter notebook cell folding — useful for long multi-turn sessions where earlier answers take up significant screen space.

No API changes. Pure client-side state toggled by a chevron button in the turn header.

---

## Behaviour

### Per-turn toggle

A **▼ / ▶** chevron button appears at the far right of every completed turn's question header row. Clicking it toggles the turn's collapsed state:

| State | Visible |
|-------|---------|
| Expanded (default) | Full answer body: rendered Markdown, charts, iteration/token counts, Datasets used, Steps inspector, follow-up chips, Export MD |
| Collapsed | Question text, timestamp, best-effort badge (if any), the chevron button |

The collapsed turn shows a single summary line below the question in muted text:

```
▶  [answer truncated — click to expand]
```

Collapsed turns do **not** hide the question row itself — the user can always read what was asked.

### Restrictions

| Turn type | Collapsible? | Reason |
|-----------|-------------|--------|
| Completed turn | ✓ | Normal case |
| Clarification turn (C26) | ✗ | No answer body to hide; the clarification question must remain visible so the user can respond |
| Running turn (in-progress) | ✗ | Progress row and spinner must stay visible during the query |
| Best-effort turn | ✓ | Same as completed; the ⚠ badge stays visible in collapsed state |

The chevron button is not rendered at all for clarification and running turns.

### Collapse all / Expand all

When the thread contains **≥ 2 completed turns**, a pair of buttons appears in the thread toolbar area (above the first turn, or in the thread card header):

- **Collapse all** — collapses every collapsible turn; label changes to **Expand all** when all are collapsed
- **Expand all** — expands every turn

The toolbar only shows when ≥ 2 completed turns exist. A single-turn session does not need it.

---

## State Persistence

Collapsed state is persisted in **`sessionStorage`**, keyed by `run_id` (globally unique), so it survives tab switches, page refreshes, and session reloads within the browser tab.

- A single `sessionStorage` entry `collapsedTurns` holds a JSON array of the `run_id`s that are currently collapsed (`_loadCollapsedSet` / `_saveCollapsedSet`).
- `_toggleTurn`, `_collapseAll`, and `_expandAll` update that set after mutating the DOM (`_persistTurnCollapsed`).
- Each turn element carries its identity as `div.dataset.runId` (set from the `run_id` passed into `appendTurn`). On (re-)render, `appendTurn` re-applies the collapsed class + chevron glyph if the run's id is in the stored set.
- When a session is loaded via `resumeSession(sessionId)` → `GET /sessions/{id}`, each turn is re-appended with its `run_id`, so previously collapsed turns come back collapsed. Turns whose `run_id` is absent from the set render expanded (the default).

`sessionStorage` (not `localStorage`) is intentional: collapse state is a transient view preference scoped to the tab session, and it is cleared when the tab closes.

---

## UI Details

### Chevron button

```
┌─────────────────────────────────────────────────────────────┐
│  Q: What is the total revenue by region?      12:34   ▼    │  ← expanded
└─────────────────────────────────────────────────────────────┘
│  Total revenue by region:                                    │
│  | Region | Revenue |                                        │
│  ...                                                         │
│  [Datasets used ▸] [2 steps ▸] [💬 …] [Export MD]          │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  Q: What is the total revenue by region?      12:34   ▶    │  ← collapsed
│  ▶  [answer truncated — click to expand]                    │
└─────────────────────────────────────────────────────────────┘
```

- Button: `type="button"`. Its accessible state lives on the button itself — `aria-label` and `title` flip between `"Collapse turn"`/`"Collapse"` (expanded) and `"Expand turn"`/`"Expand"` (collapsed), and the glyph toggles between `▼` and `▶`. There is **no** `aria-expanded` attribute on the turn element.
- The summary line uses muted text (`color: #9ca3af`, `font-size: 12px`).
- Collapse/expand is an **instant** show/hide via CSS — `.turn.collapsed .turn-body { display: none; }`. There is no `max-height` (or any) transition animation.

### Toolbar placement

Appears at the top of `#thread`, right-aligned, as a compact button group:

```
[ Collapse all ]   ← only when ≥ 1 expanded; changes to [ Expand all ] when all collapsed
```

Rendered once by `_updateCollapseToolbar()` which is called after every turn append and after any collapse/expand action.

---

## Implementation

| File | Change |
|------|--------|
| `src/data_analyst/templates/index.html` | Add chevron toggle (`.turn-collapse-btn`) in `appendTurn`; add `_toggleTurn(btn)`, `_collapseAll()`, `_expandAll()`, `_updateCollapseToolbar()`; thread toolbar (`#thread-toolbar` / `.btn-collapse-all`); CSS for the collapsed state (`.turn.collapsed .turn-body { display:none }`). `sessionStorage` persistence keyed by `run_id` (`_loadCollapsedSet`/`_saveCollapsedSet`/`_persistTurnCollapsed`); `appendTurn` takes a `runId` arg, stores it on `div.dataset.runId`, and restores collapsed state on render. |

No backend changes. No new API routes. No DB changes. (`appendTurn` consumes the existing `run_id` already returned by `POST /ask` and `GET /sessions/{id}`.)

---

## Out of Scope

- Cross-device / cross-browser persistence of collapse state — it lives in `sessionStorage`, scoped to the browser tab, and is cleared when the tab closes (see *State Persistence* above).
- Per-section collapsing within a turn (e.g. hiding only the Steps inspector — that already exists via the existing steps toggle).
- Keyboard shortcut to collapse all.
