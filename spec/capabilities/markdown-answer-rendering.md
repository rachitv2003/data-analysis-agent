# Capability: Markdown → HTML Answer Rendering (C6)

## What It Does
Renders the agent's Markdown answer (tables, bold, bullets, code, headings) to HTML for display.

## Inputs
| Input | Type | Source | Required |
|-------|------|--------|----------|
| answer_markdown | string | finalize node | yes |

## Outputs
| Output | Type | Destination |
|--------|------|-------------|
| answer_html | string | `/ask` response |

## External Calls
| System | Operation | On Failure |
|--------|-----------|------------|
| markdown-it-py | render md → html | fall back to escaped text |

## Business Rules
- Server renders `answer_html` from `answer_markdown` (markdown-it-py); both are returned by the API.
- The live conversation UI renders the answer **Markdown client-side** (`react-markdown` + `remark-gfm`), NOT the server's `answer_html` — a deliberate safety choice (no raw-HTML passthrough) for model-generated content. `answer_html` remains available on the payload for other consumers.
- Tables, bold, bullets, fenced code, and headings must render.

## Success Criteria
- [ ] An answer containing a Markdown table renders as an HTML `<table>` in `answer_html`.
- [ ] Bold, bullets, code blocks, and headings render correctly.
- [ ] The UI renders the answer Markdown client-side (sanitised; not raw Markdown).
