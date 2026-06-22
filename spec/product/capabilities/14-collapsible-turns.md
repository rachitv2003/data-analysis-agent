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

Collapsed state is **not** persisted — it lives purely in the live DOM via `classList.toggle('collapsed')` on each `.turn` element. There is no `sessionStorage` (or any other) backing store.

This means:
- State is **lost** whenever the thread is re-rendered: switching tabs (Analyse ↔ Database), refreshing the page, or reloading the session.
- When a session is loaded via `resumeSession(sessionId)` → `GET /sessions/{id}`, the thread is cleared and every turn is re-appended in the default **expanded** state. No prior collapse state is restored.

> **Future work:** persisting collapsed state (e.g. in `sessionStorage` keyed by `run_id`) so it survives tab switches and session reloads is not yet implemented. See *Out of Scope* below.

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
| `src/data_analyst/templates/index.html` | Add chevron toggle (`.turn-collapse-btn`) in `appendTurn`; add `_toggleTurn(btn)`, `_collapseAll()`, `_expandAll()`, `_updateCollapseToolbar()`; thread toolbar (`#thread-toolbar` / `.btn-collapse-all`); CSS for the collapsed state (`.turn.collapsed .turn-body { display:none }`). No state-restore in `resumeSession`. |

No backend changes. No new API routes. No DB changes.

---

## Out of Scope

- Persisting collapsed state at all (across page refreshes, tab switches, or session reloads) — state is live-DOM only and is lost on any re-render. See *State Persistence* above.
- Per-section collapsing within a turn (e.g. hiding only the Steps inspector — that already exists via the existing steps toggle).
- Keyboard shortcut to collapse all.
