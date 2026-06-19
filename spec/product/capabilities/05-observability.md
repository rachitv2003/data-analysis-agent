# Observability

**Status:** implemented
**Covers:** C7 (per-run token tracking), C18 (token counter widget), C22 (query timer + progress)

Token counts are recorded per run and aggregated daily. The UI shows live progress during a query and a persistent usage widget tracking session and daily token spend.

---

## Per-Run Token Tracking (C7)

Every LLM call returns a `LLMResponse(text, tokens_input, tokens_output)`. Both `plan_action` and `force_finalize` accumulate these into `AgentState["tokens_input"]` / `["tokens_output"]` across iterations. The selector call (C19) also contributes token counts, which are added to the `QueryRunRow` by `ask.py` after the graph completes.

`_persist_run` saves the final accumulated counts to `QueryRunRow.tokens_input` and `QueryRunRow.tokens_output`.

**Token sources per run:**
- Dataset selector call (if triggered)
- Each `plan_action` iteration
- `force_finalize` synthesis call (if triggered)

---

## Token Usage Counter Widget (C18)

A dark-background widget in the top-right column of the UI shows:

**This session** (in-memory, `sessionStorage`):
- Tokens in / Tokens out / Queries / Est. cost

**Today (UTC)** (from `GET /stats/daily`):
- Tokens in / Tokens out / Queries / Est. cost

**Storage:**
- Datasets count / Total rows

### Daily Stats Endpoint

`GET /stats/daily` — aggregates `query_runs` where `status = 'completed'` and `DATE(created_at) = <today UTC>`. Returns `date`, `model`, `tokens_input`, `tokens_output`, `query_count`. Always returns 200 with zero values if no completed runs exist today.

### Cost Estimation

Client-side calculation using a hardcoded pricing table (`_PRICING` in `base.html`). Supported models include Gemini variants (gemini-2.5-flash, 2.5-pro, 2.0-flash, 1.5-flash, 1.5-pro) and OpenRouter models (Claude, GPT-4o, Llama, Mistral). Cost is `(tokens_in / 1e6) * price_in + (tokens_out / 1e6) * price_out`, formatted to 4 significant figures. Models not in the pricing table show "N/A".

After each successful ask, `tuAddRun(ti, to)` updates `sessionStorage` and re-renders session stats, then calls `tuRefreshDaily()` to refresh the daily panel.

---

## Query Timer and Progress (C22)

While a query is running, the UI shows a progress row below the Ask button:

- **Elapsed timer**: increments every second (`⏱ Ns`).
- **Progress bar**: filled proportionally to `iteration_count / max_iterations`.
- **Step counter**: `Step N / max` — updated by polling `GET /runs/current` every second.

`GET /runs/current` returns the most-recently-created `QueryRunRow` with `run_id`, `status`, `iteration_count`, and `max_iterations`. The `execute_action` node calls `_update_iteration_count` to write the current `iteration_count` to the DB on every iteration, making the progress accurate in real time.

The progress row is hidden when the query completes or errors.

---

## Implementation

| File | Role |
|------|------|
| `src/data_analyst/api/stats.py` | `GET /stats/daily` |
| `src/data_analyst/api/runs.py` | `GET /runs/current` |
| `src/data_analyst/graph/nodes.py` | `plan_action` and `force_finalize` accumulate tokens; `_update_iteration_count` writes mid-run progress; `_persist_run` saves final counts |
| `src/data_analyst/llm/providers/base.py` | `LLMResponse` dataclass with `tokens_input` / `tokens_output` |
| `src/data_analyst/templates/base.html` | Token usage widget HTML + JS (`tuAddRun`, `tuRefreshDaily`, pricing table) |
| `src/data_analyst/templates/index.html` | Progress row HTML + JS (elapsed timer, polling loop, progress bar) |
