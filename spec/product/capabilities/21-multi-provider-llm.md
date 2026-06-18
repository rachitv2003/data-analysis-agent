# C21 — Multi-Provider LLM Configuration

## Goal
Allow any LLM provider and model to be selected via `.env` without code changes.
OpenRouter is the first new provider; Gemini remains supported. Switching providers
requires only editing two environment variables.

## User-facing behaviour
- The model name displayed in the token counter widget reflects the active model.
- The "Est. cost" field shows a calculated value for known models; "N/A" for unknown.
- No other UI changes.

## Configuration

All settings use the existing `DATA_ANALYST_` prefix.

| Env var | Values | Default |
|---|---|---|
| `DATA_ANALYST_LLM_PROVIDER` | `gemini` \| `openrouter` \| `stub` \| _(empty)_ | _(empty = auto)_ |
| `DATA_ANALYST_LLM_MODEL` | any model string | `gemini-2.5-flash` |
| `DATA_ANALYST_GEMINI_API_KEY` | Gemini API key | _(empty)_ |
| `DATA_ANALYST_OPENROUTER_API_KEY` | OpenRouter API key | _(empty)_ |

### Provider auto-detection (when `LLM_PROVIDER` is empty)
1. If `OPENROUTER_API_KEY` is set → `openrouter`
2. Else if `GEMINI_API_KEY` is set → `gemini`
3. Else → `stub`

Explicit `LLM_PROVIDER` always takes precedence over auto-detection.

## OpenRouter provider

- **API base**: `https://openrouter.ai/api/v1/chat/completions`
- **Auth**: `Authorization: Bearer <OPENROUTER_API_KEY>`
- **Request body**: OpenAI-compatible chat completions format
  ```json
  {
    "model": "<LLM_MODEL>",
    "messages": [{"role": "user", "content": "<prompt>"}]
  }
  ```
- **Token counts**: read from `response.usage.prompt_tokens` and `completion_tokens`
- **Retries**: 3 attempts on 429/rate-limit, same back-off logic as Gemini provider
- **HTTP client**: `httpx` (already in the dependency tree via other packages)

## Pricing table extension (frontend)

The client-side `_PRICING` array in `base.html` is extended with common OpenRouter
model slugs so cost estimation works out of the box for popular choices.

Models to add (prices per million tokens, as of spec date):

| Model slug | $/M in | $/M out |
|---|---|---|
| `google/gemini-2.5-flash` | 0.30 | 2.50 |
| `google/gemini-2.5-pro` | 1.25 | 10.00 |
| `anthropic/claude-3-5-sonnet` | 3.00 | 15.00 |
| `anthropic/claude-3-5-haiku` | 0.80 | 4.00 |
| `openai/gpt-4o` | 2.50 | 10.00 |
| `openai/gpt-4o-mini` | 0.15 | 0.60 |
| `meta-llama/llama-3.1-8b-instruct` | 0.055 | 0.055 |

The existing Gemini short-slug patterns (`gemini-2.5-flash`, etc.) are kept for
backwards compatibility — model strings matching either the short or full slug will
price correctly.

## Files changed

| File | Change |
|---|---|
| `src/data_analyst/config/settings.py` | Add `llm_provider`, `openrouter_api_key` fields |
| `src/data_analyst/llm/providers/openrouter.py` | New `OpenRouterProvider` class |
| `src/data_analyst/llm/providers/factory.py` | Route on `llm_provider`; auto-detect logic |
| `src/data_analyst/templates/base.html` | Extend `_PRICING` array |
| `.env` | Add `DATA_ANALYST_LLM_PROVIDER` and `DATA_ANALYST_OPENROUTER_API_KEY` |

## Out of scope
- UI for provider selection (`.env` is the configuration surface)
- Streaming responses
- Multi-modal / image inputs
- Per-model context-length enforcement
