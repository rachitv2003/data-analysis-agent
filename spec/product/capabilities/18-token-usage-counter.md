# Capability: API Token Usage Counter

**Status:** draft

## Purpose

A persistent, always-visible widget fixed at the top-right corner of the UI that surfaces token consumption and estimated cost at a glance. Users can see how much of the LLM quota they have spent in the current browser session and today, without leaving the page or running a separate query.

## UX

### Widget appearance

- A compact badge/chip pinned to the top-right corner of every page, overlaying the main layout with `position: fixed`.
- Always visible — it does not scroll away with the page content.
- **Collapsed (default) state:** shows a single line with the model name and today's total token count (e.g. `gemini-2.5-flash · 4 210 tokens today`).
- **Expanded state:** clicking or hovering the badge reveals a small panel (≈ 220 px wide) with the full breakdown (see below). Clicking elsewhere collapses it.
- The widget must not obscure primary UI controls; it must not exceed 260 px wide when expanded.
- Colour: neutral/muted background (grey or translucent) so it is visible but not distracting.

### Expanded panel layout

```
┌──────────────────────────────────┐
│  gemini-2.5-flash                │
├──────────────────────────────────┤
│  This session                    │
│    Tokens in:    1 240           │
│    Tokens out:     870           │
│    Queries:          3           │
│    Est. cost:   $0.000 43        │
├──────────────────────────────────┤
│  Today (UTC)                     │
│    Tokens in:    3 100           │
│    Tokens out:   2 410           │
│    Queries:         11           │
│    Est. cost:   $0.001 08        │
└──────────────────────────────────┘
```

Fields shown:
- **Model name** — from app config (`llm_model` setting), displayed at the top.
- **This session** — accumulated since the current browser tab was loaded (stored in JS `sessionStorage`; resets on tab close or hard refresh).
- **Today (UTC)** — aggregated from the database for the calendar day in UTC; fetched from a new `GET /stats/daily` endpoint on page load and after every `/ask` call.
- Per scope: `tokens_input`, `tokens_output`, `query_count`, and `estimated_cost_usd`.

### Cost estimation

Cost is calculated client-side using hardcoded per-token rates for known model names. The formula is:

```
cost = (tokens_input / 1_000_000) * input_rate_per_mtok
     + (tokens_output / 1_000_000) * output_rate_per_mtok
```

Initial pricing table (USD per 1 M tokens, non-thinking / standard tier):

| Model name pattern | Input ($/MTok) | Output ($/MTok) |
|---|---|---|
| `gemini-2.5-flash` | 0.30 | 2.50 |
| `gemini-2.5-pro` | 1.25 | 10.00 |
| `gemini-2.0-flash` | 0.10 | 0.40 |
| `gemini-1.5-flash` | 0.075 | 0.30 |
| `gemini-1.5-pro` | 1.25 | 5.00 |
| (unknown model) | — | — |

If the model name does not match a known pattern, the cost cells show `N/A` instead of a dollar amount.

Matching is by prefix/substring: `gemini-2.5-flash` matches model strings like `gemini-2.5-flash-preview-05-20`.

### Session accumulation

The browser accumulates session-scoped stats in `sessionStorage` (not `localStorage`, so they reset per tab):

```js
// Key: "tokenUsage"
{
  "tokens_input": 1240,
  "tokens_output": 870,
  "query_count": 3
}
```

After every successful `/ask` response, the client reads `tokens_input` and `tokens_output` from the response JSON and adds them to the running totals in `sessionStorage`. The widget re-renders immediately.

### Daily stats

On page load the client calls `GET /stats/daily`. After every successful `/ask` call the client also refreshes `GET /stats/daily` to reflect the new run. The widget updates the "Today" section with the fresh values.

## API changes required

### New endpoint: `GET /stats/daily`

**Purpose:** Return aggregated token and query-run statistics for the current UTC calendar day, plus the active model name.

**Query parameters:** none (always returns today's date, computed server-side).

**Response:**
```json
{
  "data": {
    "date": "2026-06-17",
    "model": "gemini-2.5-flash",
    "tokens_input": 3100,
    "tokens_output": 2410,
    "query_count": 11
  },
  "error": null
}
```

Implementation notes:
- Queries `query_runs` where `status = 'completed'` and `DATE(created_at) = today_utc`.
- `model` is read from `Settings.llm_model` at request time (not stored per-run).
- Returns zero-values (all counts = 0) when no completed runs exist for today — never a 404.

**Error cases:** none expected; always returns 200.

## Data model changes required

None. All data required for `GET /stats/daily` already exists in `QueryRunRow`:
- `tokens_input`, `tokens_output` — added in C7
- `created_at` — present since C1
- `status` — present since C2

No new columns or tables are needed.

## UI changes required

- Add a `<div id="token-usage-widget">` fixed overlay element to the base Jinja2 template, rendered on every page.
- On `DOMContentLoaded`:
  1. Read `sessionStorage["tokenUsage"]` (initialise to zeros if absent).
  2. Fetch `GET /stats/daily` and populate the "Today" section.
  3. Read model name from a `<meta name="llm-model" content="...">` tag injected by Jinja2 (avoids a round-trip — the model is already known at render time).
- After every successful `POST /ask` response:
  1. Add the response's `tokens_input` + `tokens_output` to `sessionStorage["tokenUsage"]`.
  2. Re-fetch `GET /stats/daily`.
  3. Re-render the widget.
- The widget toggle (click to expand/collapse) is handled by a CSS class toggle; no JS framework required.
- Numbers are formatted with `toLocaleString()` for readability (thousands separators).
- Cost is formatted to 6 significant digits, e.g. `$0.000 432`.

## Failure modes

| Condition | Behavior |
|---|---|
| `GET /stats/daily` returns a network error | "Today" section shows `—` for all values; no crash |
| Model name not in pricing table | Cost cells show `N/A`; all token counts still display |
| `sessionStorage` unavailable (private mode in some browsers) | Session stats silently disabled; today stats from API still work |
| `/ask` response missing `tokens_input` or `tokens_output` | Treat missing fields as 0; do not crash |

## Acceptance criteria

- [ ] The token usage widget is visible on the main page without scrolling, positioned at the top-right
- [ ] Collapsed state displays the model name and today's total token count
- [ ] Clicking the widget expands the full panel; clicking outside collapses it
- [ ] `GET /stats/daily` returns `{"date": "...", "model": "...", "tokens_input": N, "tokens_output": N, "query_count": N}` with zeros when no runs exist today
- [ ] After a completed `/ask` call, the "This session" token counts increase by the run's `tokens_input` and `tokens_output`
- [ ] After a completed `/ask` call, the "Today" section reflects the updated daily total (re-fetched from `GET /stats/daily`)
- [ ] Refreshing the page resets "This session" to zero but preserves "Today" values (fetched from API)
- [ ] Estimated cost is shown for `gemini-2.5-flash` runs and matches the formula (`tokens_input * 0.30/1e6 + tokens_output * 2.50/1e6`)
- [ ] Unknown model names show `N/A` for cost rather than `$0.00` or an error
- [ ] `GET /stats/daily` only counts `status = 'completed'` runs (not `pending`, `running`, or `failed`)
- [ ] The widget does not obscure the upload button or the query input field
