# Capabilities Index

All capabilities are implemented unless noted otherwise.

| File | Capabilities covered | Status |
|------|---------------------|--------|
| [01-data-ingestion.md](01-data-ingestion.md) | C1 CSV upload, C10 duplicate detection, C11 multi-format (CSV/TSV/TXT/JSON), C13 multi-file / folder drop, C16 notes file, C17 staged upload, C24 NL data cleaning (preview + apply) | implemented |
| [02-query-and-analysis.md](02-query-and-analysis.md) | C2 NL Q&A / ReAct loop, C8 expanded result display (100 rows / 20 cols), C19 automatic dataset selection, C20 early exit / force-finalize | implemented |
| [03-conversation.md](03-conversation.md) | C3 multi-turn conversation history, C9 session management UI, C14 multi-dataset querying, C15 dataset deletion + cascade | implemented |
| [04-response-rendering.md](04-response-rendering.md) | C6 rich text / Markdown → HTML, C4 Plotly charts embedded inline, C23 agent steps inspector | implemented |
| [05-observability.md](05-observability.md) | C7 per-run token tracking, C18 token usage counter widget + daily stats, C22 query timer + live progress bar | implemented |
| [06-llm-configuration.md](06-llm-configuration.md) | C21 multi-provider LLM (Gemini / OpenRouter / stub), C12 dataset context notes injected into prompts | implemented |
| [08-derived-datasets.md](08-derived-datasets.md) | C25 autonomous derived dataset persistence (`save_dataset`), cross-query state continuity, lineage tracking, staleness detection | implemented |
| [09-clarification.md](09-clarification.md) | C26 pre-flight clarification check — lightweight LLM call before agent loop; asks user when question is genuinely ambiguous | implemented |
| [10-dataframe-cache.md](10-dataframe-cache.md) | C27 session-scoped DataFrame cache (LRU 1 GB) + Parquet pre-conversion on upload — eliminates repeated disk I/O across queries | implemented |
| [11-context-window.md](11-context-window.md) | C29 live context window display — sidebar token budget estimate at rest + steps-inspector per-component breakdown after each run | implemented |
| [12-auto-dataset-notes.md](12-auto-dataset-notes.md) | C30 on-demand dataset notes generation — user-triggered LLM call from Database tab; pre-fills editable context/notes field with column descriptions + quality observations | implemented |
| [13-semantic-compression.md](13-semantic-compression.md) | C31 semantic context compression — LLM structured fact extraction for dataset notes and global memory; compact facts replace raw text in agent prompts | implemented |
| [14-collapsible-turns.md](14-collapsible-turns.md) | C32 collapsible conversation turns — per-turn collapse/expand toggle + "Collapse all / Expand all"; pure client-side; state in sessionStorage | planned |
