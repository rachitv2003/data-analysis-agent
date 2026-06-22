# LLM Configuration

**Status:** implemented
**Covers:** C21 (multi-provider), C12 (dataset context notes)

The LLM provider and model are configured entirely via environment variables. The same provider interface is used for both the ReAct loop and the dataset-selector pre-flight call. Dataset context notes are injected into every prompt for the relevant dataset(s).

---

## Provider Selection (C21)

Provider is selected at startup in `src/data_analyst/llm/providers/factory.py`:

1. If `DATA_ANALYST_LLM_PROVIDER` is set explicitly → use that provider.
2. Else if `DATA_ANALYST_OPENROUTER_API_KEY` is set → use OpenRouter.
3. Else if `DATA_ANALYST_GEMINI_API_KEY` is set → use Gemini.
4. Else → use Stub.

| Provider | Required env var | Transport |
|----------|-----------------|-----------|
| `gemini` | `DATA_ANALYST_GEMINI_API_KEY` | `google-genai` Python SDK |
| `openrouter` | `DATA_ANALYST_OPENROUTER_API_KEY` | `httpx` POST to `https://openrouter.ai/api/v1/chat/completions` |
| `stub` | none | Returns deterministic canned responses; no network |

### Model

`DATA_ANALYST_LLM_MODEL` sets the model name (default: `gemini-3.1-flash-lite`). The same model name is passed to both Gemini and OpenRouter; for OpenRouter, use full path slugs like `anthropic/claude-3-5-sonnet`.

### Rate-limit Retry

Both Gemini and OpenRouter providers retry up to 3 times on 429 responses. Gemini extracts the `retryDelay` from the error message and caps it at 120 seconds. OpenRouter uses a linear backoff (30s × attempt).

### Stub Provider

When no API key is available, the stub provider detects the prompt's node tag and returns:
- `<node:plan>`: first call → `df.describe().to_string()`; subsequent calls → `FINAL ANSWER: [stub] ...`
- `<node:select>`: returns a JSON array containing the first dataset ID.
- `<node:finalize>`: returns a canned best-effort summary string.

The stub is detected automatically and triggers the yellow banner in the UI: `⚠ Running in stub mode — set DATA_ANALYST_GEMINI_API_KEY in your .env for real answers`.

### Provider Interface

All providers implement `LLMProvider.complete(prompt: str) -> LLMResponse`. `LLMResponse` carries `text`, `tokens_input`, `tokens_output`. `LLMClient` is a thin wrapper around `LLMProvider`.

---

## Dataset Context Notes (C12)

Each dataset can have an optional `context` field (TEXT, max 4 000 chars) containing user-provided notes about the dataset — column definitions, units, caveats, known data quality issues, etc.

Context is set at upload time via the `context` form field or `notes_file`, and updated after upload via `PATCH /datasets/{dataset_id}/context`.

**Prompt injection:** `setup` in `nodes.py` loads the `context` field for each loaded dataset. When only one dataset is loaded, context is included directly. When multiple datasets are loaded, each dataset's context is prefixed with `[filename]`. The combined context is stored as `AgentState["dataset_context"]` and rendered in the prompt as:

```
Dataset context (treat as authoritative):
<combined context>
```

This block appears before the user's question, signalling to the LLM that it should treat these notes as ground truth rather than to be questioned.

**UI:** When exactly one dataset is selected (checked) in the datasets panel, a "Dataset notes" sub-panel appears below the dataset list. It shows the current context and offers an "Edit" toggle that reveals a textarea. Saving calls `PATCH /datasets/{id}/context`. Context is cached client-side in `_ctxCache` to avoid redundant fetches.

---

## Settings

All configuration is in `src/data_analyst/config/settings.py` via `pydantic-settings` with `env_prefix="DATA_ANALYST_"`:

| Setting | Env var | Default | Description |
|---------|---------|---------|-------------|
| `database_url` | `DATA_ANALYST_DATABASE_URL` | `sqlite:///data_analyst.db` | SQLAlchemy DB URL |
| `llm_provider` | `DATA_ANALYST_LLM_PROVIDER` | `""` (auto) | `gemini`, `openrouter`, `stub`, or empty for auto |
| `gemini_api_key` | `DATA_ANALYST_GEMINI_API_KEY` | `""` | |
| `openrouter_api_key` | `DATA_ANALYST_OPENROUTER_API_KEY` | `""` | |
| `llm_model` | `DATA_ANALYST_LLM_MODEL` | `gemini-3.1-flash-lite` | Model name passed to provider |
| `max_iterations` | `DATA_ANALYST_MAX_ITERATIONS` | `6` | ReAct loop iteration cap |
| `log_level` | `DATA_ANALYST_LOG_LEVEL` | `INFO` | structlog level |
| `upload_dir` | `DATA_ANALYST_UPLOAD_DIR` | `uploads` | Directory for saved CSV files |
| `cache_limit_mb` | `DATA_ANALYST_CACHE_LIMIT_MB` | `1024` | Max size of the C27 dataframe cache, in MB |

Settings are loaded once at startup (`get_settings()` caches the instance).

---

## Implementation

| File | Role |
|------|------|
| `src/data_analyst/config/settings.py` | `Settings` pydantic-settings class, `get_settings()` |
| `src/data_analyst/llm/providers/factory.py` | `create_llm_client()` — provider selection logic |
| `src/data_analyst/llm/providers/base.py` | `LLMProvider` ABC, `LLMResponse` dataclass |
| `src/data_analyst/llm/providers/gemini.py` | `GeminiProvider` with retry |
| `src/data_analyst/llm/providers/openrouter.py` | `OpenRouterProvider` with retry |
| `src/data_analyst/llm/providers/stub.py` | `StubLLMProvider` |
| `src/data_analyst/llm/client.py` | `LLMClient` thin wrapper |
| `src/data_analyst/api/datasets.py` | `PATCH /datasets/{id}/context` |
| `src/data_analyst/graph/nodes.py` | Context injection in `setup` and `_build_prompt` |
| `src/data_analyst/templates/index.html` | Dataset notes panel, edit/save UI |
