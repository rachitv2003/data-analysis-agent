# Capability: Rich Text Responses

**Status:** draft

## Purpose

Agent answers are formatted as Markdown and rendered as styled HTML in the browser, making tables, numbers, and lists significantly easier to read than raw plain text.

## Inputs

| Field | Type | Required | Description |
|---|---|---|---|
| dataset_id | string (UUID) | yes | Dataset being queried |
| question | string | yes | User's natural language question |
| session_id | string (UUID) | no | Existing session to continue |

*(Same inputs as Capability 2 — this capability changes how the answer is produced and displayed, not the request shape.)*

## Outputs

| Field | Type | Description |
|---|---|---|
| answer_markdown | string | Agent's answer in Markdown format |
| answer_html | string | answer_markdown rendered to sanitised HTML (server-side) |

The existing `answer` field in the response is replaced by `answer_markdown`. `answer_html` is added as a convenience field for clients that want pre-rendered HTML.

## Behavior

1. **Prompt instruction.** The `plan_action` node injects a formatting instruction into every `<node:plan>` prompt:
   ```
   Format your FINAL ANSWER using Markdown:
   - Use **bold** for key numbers and column names
   - Use bullet lists or numbered lists where appropriate
   - Use a Markdown table if the answer is tabular data
   - Use headings (##) only if the answer has multiple distinct sections
   - Do NOT use code blocks for the answer prose — only for raw data if needed
   ```

2. **Storage.** The `answer` column in `query_runs` stores the raw Markdown string (no HTML). HTML is rendered on-the-fly.

3. **Server-side rendering.** The FastAPI `/ask` response includes:
   - `answer_markdown` — raw Markdown string
   - `answer_html` — Markdown converted to HTML via `markdown-it-py` with safe defaults (no raw HTML passthrough)

4. **Client rendering.** The Jinja2 template injects `answer_html` directly into the conversation thread `<div>` using `innerHTML`. A minimal CSS stylesheet scopes table, list, bold, and heading styles to the answer container.

5. **Stub compatibility.** The stub LLM provider's `FINAL ANSWER` output must also be valid Markdown (it currently returns plain text — update it to include at least one bold token and a line break so the rendering path is exercised in tests).

## Failure modes

| Condition | Behavior |
|---|---|
| Markdown rendering fails | Fall back to plain text wrapped in `<pre>` |
| LLM returns non-Markdown plain text | Display as-is — plain text is valid Markdown |

## Data model changes required

- **`query_runs.answer`** — no type change; field now stores Markdown string instead of plain text (backwards compatible)
- No new columns required

## API changes required

- `POST /ask` response: rename `answer` → `answer_markdown`; add `answer_html` field
- `GET /sessions/{session_id}` turns: same rename

## UI changes required

- Inject `answer_html` via `innerHTML` in the conversation thread (replace `textContent` assignment)
- Add scoped CSS for `.answer-body` element: table borders, `th` background, list indentation, bold colour
- Stub banner test must assert that `answer_html` contains at least one HTML tag

## Key library

| Library | Version | Purpose |
|---------|---------|---------|
| markdown-it-py | >=3.0 | Markdown → HTML, Python-native, no JS required |

## Acceptance criteria

- [ ] A question that produces tabular data (e.g. "total value by region") returns an HTML `<table>` in `answer_html`
- [ ] A question with a single-number answer returns `answer_html` with a `<strong>` tag around the number
- [ ] `answer_markdown` is stored verbatim in the DB; re-rendering it produces the same HTML
- [ ] Raw `<script>` or `<img>` tags injected by the LLM are stripped from `answer_html` (XSS safety)
- [ ] Stub LLM output contains Markdown (bold + newlines) and renders to valid HTML in tests
- [ ] Plain-text fallback renders without error if `markdown-it-py` raises
