# Capability: Charts and Dashboards

**Status:** draft

## Purpose

The agent generates interactive charts and simple multi-chart dashboards inline in the conversation thread in response to natural language requests. No special command is required — the LLM decides, via the existing ReAct loop, when a chart is the right output for the user's question. Charts are rendered as interactive Plotly HTML embedded directly in the answer panel.

## Inputs

No new inputs. This capability uses the same `POST /ask` request shape as Capability 2. The user asks a question in natural language (e.g. "plot sales by month", "show me a bar chart of revenue by region", "give me a dashboard of the key metrics").

## Outputs

| Field | Type | Description |
|---|---|---|
| answer_html | string | Already-existing field; now may contain one or more embedded Plotly chart `<div>` blocks in addition to Markdown prose |

No new response fields are added. Chart HTML is part of `answer_html`, which is already persisted as part of the `QueryRun.answer` column.

## Behavior

### Chart generation

1. **LLM decision.** The `plan_action` node's system prompt is updated (see Prompt Changes below) to instruct the LLM that, when the user asks for a chart, graph, plot, or visualisation, it should write a Python expression using `plotly.express` or `plotly.graph_objects` and call `fig.to_html(include_plotlyjs=...)` as the final expression. The LLM uses its existing ReAct reasoning: it may first execute pandas to fetch/aggregate the data, then produce the chart in a subsequent action.

2. **Execution.** The `execute_action` node evaluates the Plotly expression in the same sandbox (Python `eval`) that already handles pandas. No sandbox changes are required — `plotly` is simply imported at eval time. The return value of `fig.to_html(...)` is a string of HTML, which `_result_to_str` will pass through as-is (it is not a DataFrame or Series).

3. **Chart HTML in the FINAL ANSWER.** Once the agent has the chart HTML string (from the action result), it emits it in the `FINAL ANSWER`. The LLM is instructed to embed the raw HTML directly in the final answer, not wrapped in a code block.

### Plotly CDN loading strategy

To avoid loading the 3 MB Plotly bundle more than once per session, the agent tracks whether Plotly JS has already been included in the session:

- The **first chart** in a session uses `include_plotlyjs='cdn'` — this inserts a `<script src="https://cdn.plot.ly/...">` tag.
- **Subsequent charts** in the same session use `include_plotlyjs=False` — the bundle is already present in the DOM.

The LLM is not responsible for this decision. The backend (FastAPI `/ask` handler or a post-processing step in `finalize`) inspects the session's prior turns: if no prior `QueryRun.answer` in the session contains `cdn.plot.ly`, the current answer uses `'cdn'`; otherwise it replaces `include_plotlyjs='cdn'` with `include_plotlyjs=False` in the generated expression before execution, or the finalize node patches the emitted HTML.

Implementation note: the simplest approach is a helper `_plotly_js_loaded(session_id)` that queries `query_runs` for the session and checks whether any existing `answer` contains the string `cdn.plot.ly`. The `execute_action` node (or a pre-execution step in `plan_action`) injects the appropriate `include_plotlyjs` value into the eval namespace or patches the LLM's expression string before eval.

### Dashboard (multiple charts in one answer)

A "dashboard" is defined as two or more charts produced in a single answer turn. The agent achieves this by executing multiple chart expressions in sequence within the same ReAct turn, accumulating the HTML strings in `action_history`, then combining them all into one `FINAL ANSWER`. No separate dashboard route, page, or persistence mechanism is needed.

The LLM is instructed (see Prompt Changes) that when asked for a dashboard or summary of multiple metrics, it should produce each chart as a separate action and then combine the resulting HTML strings in the final answer, separated by a `<div class="chart-gap"></div>` sentinel.

### Storage

Chart HTML is **not** stored separately. It is part of `answer_html` (the Markdown-rendered answer), which is already persisted in `query_runs.answer`. No new DB columns are added.

### Fallback: matplotlib

If a chart request fails with Plotly (e.g. import error), the system falls back to matplotlib:

1. The LLM (or the error-recovery path) writes a matplotlib expression that saves the figure to a temp file: `fig.savefig('/tmp/{run_id}_chart.png'); '/tmp/{run_id}_chart.png'`
2. The `execute_action` result is a file path string.
3. The `finalize` node detects a `.png` path result in `action_history`, reads the file, base64-encodes it, and embeds it as `<img src="data:image/png;base64,...">` in `answer_html`.
4. The temp file is deleted after embedding.

The matplotlib fallback is lower priority and may be implemented in a follow-up. The acceptance criteria gate on Plotly only.

## Prompt Changes

The `_build_prompt` function in `src/data_analyst/graph/nodes.py` must be updated to append the following chart instruction block after the existing `_MARKDOWN_INSTRUCTION`:

```
Chart / visualisation instructions:
- When the user asks for a chart, graph, plot, bar chart, line chart, scatter plot,
  histogram, pie chart, heatmap, or any visualisation, use plotly.express or
  plotly.graph_objects to create the figure.
- Produce the figure HTML by calling fig.to_html(include_plotlyjs=__PLOTLY_JS__) as
  the FINAL expression in your action sequence. The system will substitute the correct
  value for __PLOTLY_JS__.
- Do NOT use matplotlib unless plotly raises an ImportError.
- In the FINAL ANSWER, embed the raw HTML returned by fig.to_html() directly — do NOT
  wrap it in a code block or Markdown fence.
- For a dashboard (multiple charts), produce each chart as a separate action, then in
  the FINAL ANSWER concatenate the HTML strings separated by a newline.
- Keep chart titles concise (≤ 60 characters). Always set axis labels.
```

The placeholder `__PLOTLY_JS__` is replaced by the backend before the prompt is sent to the LLM (or the substitution happens in `execute_action` before eval).

## Dependencies

Add to project dependencies (`pyproject.toml`):

| Package | Purpose |
|---------|---------|
| `plotly` | Interactive chart generation; `fig.to_html()` produces self-contained HTML |
| `matplotlib` | Fallback static chart generation (PNG) |

`kaleido` (Plotly static image export) is NOT required — this capability uses HTML output only.

## API / DB changes

None. No new endpoints. No new DB columns. `answer_html` already carries chart HTML as part of the answer.

## UI changes

### Chart rendering

Chart HTML is injected via `innerHTML` on the `.answer-body` element, which is already implemented for Markdown HTML (C6). No JS changes required for basic rendering — Plotly's own JS handles interactivity.

### CSS constraints

Add the following CSS rules to the answer container stylesheet:

```css
/* C4: Chart sizing constraints */
.answer-body iframe,
.answer-body .plotly-graph-div {
    max-height: 420px;
    width: 100%;
}

/* C4: Dashboard multi-chart gap */
.answer-body .chart-gap,
.answer-body .plotly-graph-div + .plotly-graph-div {
    margin-top: 16px;
}
```

### Interactive features

Plotly charts are interactive by default (hover tooltips, zoom, pan, legend toggle). No additional JS wiring is required.

## Failure modes

| Condition | Behavior |
|---|---|
| `import plotly` fails in sandbox | Append error to `action_history`; LLM may retry with matplotlib fallback |
| `fig.to_html()` returns empty string | Include as-is; the answer will show an empty chart area |
| Plotly CDN unreachable (offline) | Chart renders as empty div; no server-side failure |
| LLM produces invalid Python for chart | `execute_action` catches exception, appends error, LLM self-corrects |
| Dashboard produces > 5 charts | No hard limit; CSS max-height prevents layout collapse |

## Acceptance criteria

- [ ] A question containing "plot", "chart", "bar chart", "line chart", "histogram", or "visualise" triggers a Plotly chart in the response: `answer_html` contains `<div` and `plotly` as substrings
- [ ] The first chart in a fresh session includes `cdn.plot.ly` in `answer_html`
- [ ] A second chart question in the same session does NOT include a second `cdn.plot.ly` reference — Plotly JS is loaded once per session
- [ ] A bar chart request ("bar chart of X by Y") produces a Plotly figure with the correct axis labels derived from the dataset columns
- [ ] A dashboard request ("give me a dashboard" / "show me 2 charts") produces `answer_html` containing at least two `<div` chart elements
- [ ] Charts are bounded to 420px max-height via CSS; the page does not scroll horizontally
- [ ] Multi-chart answers have a 16px gap between charts
- [ ] A non-chart question (e.g. "what is the average sales?") does NOT produce Plotly HTML in `answer_html`
- [ ] If Plotly raises an ImportError, the run does not crash — the error is appended to `action_history` and the LLM may self-correct
- [ ] `plotly` and `matplotlib` appear in `pyproject.toml` dependencies
