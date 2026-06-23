import re
import time
import structlog
from google import genai
from data_analyst.llm.providers.base import LLMProvider, LLMResponse

logger = structlog.get_logger()

_RETRY_DELAY_RE = re.compile(r"retryDelay['\"]:\s*['\"](\d+)s")
_MAX_RETRIES = 3


def _extract_retry_delay(exc: Exception) -> int | None:
    """Pull retryDelay seconds from a Gemini 429 error message, if present."""
    m = _RETRY_DELAY_RE.search(str(exc))
    return int(m.group(1)) if m else None


def _is_rate_limit(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "resource_exhausted" in msg or "429" in msg or "quota" in msg


def _is_auth_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return (
        "unauthenticated" in msg
        or "access_token_type_unsupported" in msg
        or "api key not valid" in msg
        or "api_key_invalid" in msg
        or "permission_denied" in msg
        or "401" in msg
        or "403" in msg
    )


def _is_network_error(exc: Exception) -> bool:
    """Transient connectivity failure (DNS / connection / timeout) reaching the API."""
    msg = str(exc).lower()
    return (
        "getaddrinfo" in msg              # DNS lookup failed (Errno 11001 on Windows)
        or "11001" in msg
        or "name or service not known" in msg
        or "temporary failure in name resolution" in msg
        or "failed to establish" in msg
        or "connection" in msg            # refused / reset / aborted / error
        or "network is unreachable" in msg
        or "timed out" in msg
        or "timeout" in msg
        or "max retries exceeded" in msg
    )


_AUTH_HELP = (
    "Gemini authentication failed — the API key is missing, expired, or the wrong type. "
    "Gemini API keys start with 'AIzaSy' (a value starting with 'AQ.' is a short-lived OAuth "
    "token, not an API key). Get a key at https://aistudio.google.com/apikey and set "
    "DATA_ANALYST_GEMINI_API_KEY in your .env, then restart the server."
)

_NETWORK_HELP = (
    "Couldn't reach the Gemini API — a network or DNS lookup failed (getaddrinfo). "
    "This usually means no internet connection, a VPN/proxy blocking the request, or a "
    "brief network blip. Check your connection and try again."
)


class GeminiProvider(LLMProvider):
    def __init__(self, api_key: str, model: str) -> None:
        self._client = genai.Client(api_key=api_key)
        self._model = model

    def complete(self, prompt: str) -> LLMResponse:
        last_exc: Exception | None = None
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                response = self._client.models.generate_content(
                    model=self._model, contents=prompt
                )
                usage = response.usage_metadata
                tokens_in = getattr(usage, "prompt_token_count", 0) or 0
                tokens_out = getattr(usage, "candidates_token_count", 0) or 0
                return LLMResponse(
                    text=response.text,
                    tokens_input=tokens_in,
                    tokens_output=tokens_out,
                )
            except Exception as exc:
                last_exc = exc
                if _is_auth_error(exc):
                    logger.error("gemini.auth_error", model=self._model)
                    raise RuntimeError(_AUTH_HELP) from exc
                is_rate_limit = _is_rate_limit(exc)
                is_network = _is_network_error(exc)
                if not is_rate_limit and not is_network:
                    raise

                if is_rate_limit:
                    delay = _extract_retry_delay(exc) or (30 * attempt)
                    delay = min(delay, 120)  # cap at 2 min
                    logger.warning("gemini.rate_limit", attempt=attempt, retry_in=delay, model=self._model)
                else:
                    delay = min(4 * attempt, 15)  # network blips resolve fast — short backoff
                    logger.warning("gemini.network_error", attempt=attempt, retry_in=delay, model=self._model)
                if attempt < _MAX_RETRIES:
                    time.sleep(delay)

        # All retries exhausted — raise a clean message matching the failure kind
        if last_exc is not None and _is_network_error(last_exc) and not _is_rate_limit(last_exc):
            raise RuntimeError(_NETWORK_HELP) from last_exc
        delay_hint = _extract_retry_delay(last_exc) if last_exc else None
        hint = f" Try again in ~{delay_hint}s." if delay_hint else ""
        raise RuntimeError(
            f"Gemini API rate limit exceeded (free-tier quota).{hint} "
            "Reduce the number of selected datasets, or upgrade to a paid API key."
        ) from last_exc
