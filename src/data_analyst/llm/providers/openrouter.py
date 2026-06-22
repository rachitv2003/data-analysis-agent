import time
import structlog
import httpx

from data_analyst.llm.providers.base import LLMProvider, LLMResponse

logger = structlog.get_logger()

_API_URL = "https://openrouter.ai/api/v1/chat/completions"
_MAX_RETRIES = 3


def _is_rate_limit(status: int) -> bool:
    return status == 429


class OpenRouterProvider(LLMProvider):
    def __init__(self, api_key: str, model: str) -> None:
        self._api_key = api_key
        self._model = model

    def complete(self, prompt: str) -> LLMResponse:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
        }

        last_exc: Exception | None = None
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                resp = httpx.post(_API_URL, headers=headers, json=payload, timeout=120)
                if _is_rate_limit(resp.status_code):
                    delay = 30 * attempt
                    logger.warning(
                        "openrouter.rate_limit",
                        attempt=attempt,
                        retry_in=delay,
                        model=self._model,
                    )
                    if attempt < _MAX_RETRIES:
                        time.sleep(delay)
                    last_exc = RuntimeError(f"OpenRouter rate limit (429) on attempt {attempt}")
                    continue

                resp.raise_for_status()
                data = resp.json()
                text = data["choices"][0]["message"]["content"]
                usage = data.get("usage", {})
                return LLMResponse(
                    text=text,
                    tokens_input=usage.get("prompt_tokens", 0),
                    tokens_output=usage.get("completion_tokens", 0),
                )
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (401, 403):
                    logger.error("openrouter.auth_error", model=self._model)
                    raise RuntimeError(
                        "OpenRouter authentication failed — the API key is missing, invalid, or "
                        "expired. OpenRouter keys start with 'sk-or-'. Get one at "
                        "https://openrouter.ai/keys and set DATA_ANALYST_OPENROUTER_API_KEY in your "
                        ".env, then restart the server."
                    ) from exc
                raise RuntimeError(
                    f"OpenRouter API error {exc.response.status_code}: {exc.response.text[:200]}"
                ) from exc
            except (KeyError, IndexError) as exc:
                raise RuntimeError(f"Unexpected OpenRouter response shape: {exc}") from exc

        raise RuntimeError(
            f"OpenRouter rate limit exceeded after {_MAX_RETRIES} attempts for model {self._model}."
        ) from last_exc
