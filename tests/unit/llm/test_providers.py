"""Unit tests for LLM provider rate-limit retry logic."""
import pytest
from unittest.mock import MagicMock, patch


# ── Gemini provider ──────────────────────────────────────────────────────────

class TestGeminiRateLimit:
    def _make_provider(self, api_key="test-key", model="gemini-test"):
        from data_analyst.llm.providers.gemini import GeminiProvider
        with patch("data_analyst.llm.providers.gemini.genai"):
            return GeminiProvider(api_key=api_key, model=model)

    def test_succeeds_on_first_attempt(self):
        from data_analyst.llm.providers.gemini import GeminiProvider
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "hello"
        mock_response.usage_metadata.prompt_token_count = 10
        mock_response.usage_metadata.candidates_token_count = 5
        mock_client.models.generate_content.return_value = mock_response

        with patch("data_analyst.llm.providers.gemini.genai") as mock_genai:
            mock_genai.Client.return_value = mock_client
            provider = GeminiProvider("key", "model")
            result = provider.complete("test prompt")

        assert result.text == "hello"
        assert result.tokens_input == 10
        assert result.tokens_output == 5
        assert mock_client.models.generate_content.call_count == 1

    def test_retries_on_rate_limit_then_succeeds(self):
        from data_analyst.llm.providers.gemini import GeminiProvider
        mock_client = MagicMock()
        rate_exc = RuntimeError("resource_exhausted: quota exceeded")
        good_response = MagicMock()
        good_response.text = "ok"
        good_response.usage_metadata.prompt_token_count = 1
        good_response.usage_metadata.candidates_token_count = 1
        mock_client.models.generate_content.side_effect = [rate_exc, good_response]

        with patch("data_analyst.llm.providers.gemini.genai") as mock_genai, \
             patch("data_analyst.llm.providers.gemini.time.sleep"):
            mock_genai.Client.return_value = mock_client
            provider = GeminiProvider("key", "model")
            result = provider.complete("prompt")

        assert result.text == "ok"
        assert mock_client.models.generate_content.call_count == 2

    def test_raises_after_max_retries_exhausted(self):
        from data_analyst.llm.providers.gemini import GeminiProvider, _MAX_RETRIES
        mock_client = MagicMock()
        rate_exc = RuntimeError("resource_exhausted: quota")
        mock_client.models.generate_content.side_effect = rate_exc

        with patch("data_analyst.llm.providers.gemini.genai") as mock_genai, \
             patch("data_analyst.llm.providers.gemini.time.sleep"):
            mock_genai.Client.return_value = mock_client
            provider = GeminiProvider("key", "model")
            with pytest.raises(RuntimeError, match="rate limit exceeded"):
                provider.complete("prompt")

        assert mock_client.models.generate_content.call_count == _MAX_RETRIES

    def test_non_rate_limit_error_raises_immediately(self):
        from data_analyst.llm.providers.gemini import GeminiProvider
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = ValueError("bad model name")

        with patch("data_analyst.llm.providers.gemini.genai") as mock_genai:
            mock_genai.Client.return_value = mock_client
            provider = GeminiProvider("key", "model")
            with pytest.raises(ValueError, match="bad model name"):
                provider.complete("prompt")

        assert mock_client.models.generate_content.call_count == 1

    def test_extracts_retry_delay_from_error_message(self):
        from data_analyst.llm.providers.gemini import GeminiProvider
        mock_client = MagicMock()
        rate_exc = RuntimeError("resource_exhausted retryDelay': '45s please wait")
        good_response = MagicMock()
        good_response.text = "done"
        good_response.usage_metadata.prompt_token_count = 0
        good_response.usage_metadata.candidates_token_count = 0
        mock_client.models.generate_content.side_effect = [rate_exc, good_response]

        sleep_calls = []
        with patch("data_analyst.llm.providers.gemini.genai") as mock_genai, \
             patch("data_analyst.llm.providers.gemini.time.sleep", side_effect=sleep_calls.append):
            mock_genai.Client.return_value = mock_client
            provider = GeminiProvider("key", "model")
            provider.complete("prompt")

        assert sleep_calls == [45]

    def test_auth_error_raises_clean_message_without_retry(self):
        from data_analyst.llm.providers.gemini import GeminiProvider
        mock_client = MagicMock()
        # Mirrors the real google-genai 401 when a non-API-key (e.g. OAuth token) is sent
        auth_exc = RuntimeError(
            "401 UNAUTHENTICATED. Expected OAuth 2 access token... "
            "reason: ACCESS_TOKEN_TYPE_UNSUPPORTED"
        )
        mock_client.models.generate_content.side_effect = auth_exc

        with patch("data_analyst.llm.providers.gemini.genai") as mock_genai, \
             patch("data_analyst.llm.providers.gemini.time.sleep") as mock_sleep:
            mock_genai.Client.return_value = mock_client
            provider = GeminiProvider("AQ.not-an-api-key", "model")
            with pytest.raises(RuntimeError, match="authentication failed"):
                provider.complete("prompt")

        # Auth errors are terminal — no retries, no sleeps, no raw error leakage.
        assert mock_client.models.generate_content.call_count == 1
        assert mock_sleep.call_count == 0

    def test_retries_on_network_error_then_succeeds(self):
        from data_analyst.llm.providers.gemini import GeminiProvider
        mock_client = MagicMock()
        net_exc = RuntimeError("[Errno 11001] getaddrinfo failed")  # DNS failure
        good = MagicMock()
        good.text = "ok"
        good.usage_metadata.prompt_token_count = 1
        good.usage_metadata.candidates_token_count = 1
        mock_client.models.generate_content.side_effect = [net_exc, good]

        with patch("data_analyst.llm.providers.gemini.genai") as mock_genai, \
             patch("data_analyst.llm.providers.gemini.time.sleep"):
            mock_genai.Client.return_value = mock_client
            provider = GeminiProvider("key", "model")
            result = provider.complete("prompt")

        assert result.text == "ok"
        assert mock_client.models.generate_content.call_count == 2

    def test_network_error_exhausted_raises_clean_message(self):
        from data_analyst.llm.providers.gemini import GeminiProvider, _MAX_RETRIES
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = RuntimeError(
            "[Errno 11001] getaddrinfo failed"
        )

        with patch("data_analyst.llm.providers.gemini.genai") as mock_genai, \
             patch("data_analyst.llm.providers.gemini.time.sleep"):
            mock_genai.Client.return_value = mock_client
            provider = GeminiProvider("key", "model")
            with pytest.raises(RuntimeError, match="Couldn't reach the Gemini API"):
                provider.complete("prompt")

        assert mock_client.models.generate_content.call_count == _MAX_RETRIES


# ── OpenRouter provider ──────────────────────────────────────────────────────

class TestOpenRouterRateLimit:
    def _make_response(self, status_code: int, json_body: dict | None = None, text: str = ""):
        import httpx
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = status_code
        mock_resp.text = text
        if json_body is not None:
            mock_resp.json.return_value = json_body
        return mock_resp

    def test_succeeds_on_first_attempt(self):
        from data_analyst.llm.providers.openrouter import OpenRouterProvider
        resp_json = {
            "choices": [{"message": {"content": "answer"}}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3},
        }
        mock_resp = self._make_response(200, resp_json)

        with patch("data_analyst.llm.providers.openrouter.httpx.post", return_value=mock_resp):
            provider = OpenRouterProvider("key", "model")
            result = provider.complete("q")

        assert result.text == "answer"
        assert result.tokens_input == 7
        assert result.tokens_output == 3

    def test_retries_on_429_then_succeeds(self):
        from data_analyst.llm.providers.openrouter import OpenRouterProvider
        rate_resp = self._make_response(429)
        good_resp = self._make_response(200, {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        })

        with patch("data_analyst.llm.providers.openrouter.httpx.post",
                   side_effect=[rate_resp, good_resp]), \
             patch("data_analyst.llm.providers.openrouter.time.sleep"):
            provider = OpenRouterProvider("key", "model")
            result = provider.complete("prompt")

        assert result.text == "ok"

    def test_raises_after_max_retries_exhausted(self):
        from data_analyst.llm.providers.openrouter import OpenRouterProvider, _MAX_RETRIES
        rate_resp = self._make_response(429)

        with patch("data_analyst.llm.providers.openrouter.httpx.post",
                   return_value=rate_resp), \
             patch("data_analyst.llm.providers.openrouter.time.sleep"):
            provider = OpenRouterProvider("key", "model")
            with pytest.raises(RuntimeError, match="rate limit exceeded"):
                provider.complete("prompt")

    def test_linear_backoff_delay(self):
        from data_analyst.llm.providers.openrouter import OpenRouterProvider
        rate_resp = self._make_response(429)
        good_resp = self._make_response(200, {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {},
        })

        sleep_calls = []
        with patch("data_analyst.llm.providers.openrouter.httpx.post",
                   side_effect=[rate_resp, rate_resp, good_resp]), \
             patch("data_analyst.llm.providers.openrouter.time.sleep",
                   side_effect=sleep_calls.append):
            provider = OpenRouterProvider("key", "model")
            provider.complete("prompt")

        # Linear: attempt 1 → sleep 30, attempt 2 → sleep 60
        assert sleep_calls == [30, 60]

    def test_non_429_http_error_raises_immediately(self):
        import httpx
        from data_analyst.llm.providers.openrouter import OpenRouterProvider

        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 500
        mock_resp.text = "internal error"
        http_err = httpx.HTTPStatusError("500", request=MagicMock(), response=mock_resp)
        mock_resp.raise_for_status.side_effect = http_err

        with patch("data_analyst.llm.providers.openrouter.httpx.post",
                   return_value=mock_resp):
            provider = OpenRouterProvider("key", "model")
            with pytest.raises(RuntimeError, match="OpenRouter API error 500"):
                provider.complete("prompt")

    def test_401_raises_clean_auth_message(self):
        import httpx
        from data_analyst.llm.providers.openrouter import OpenRouterProvider

        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 401
        mock_resp.text = "No auth credentials found"
        http_err = httpx.HTTPStatusError("401", request=MagicMock(), response=mock_resp)
        mock_resp.raise_for_status.side_effect = http_err

        with patch("data_analyst.llm.providers.openrouter.httpx.post",
                   return_value=mock_resp):
            provider = OpenRouterProvider("bad-key", "model")
            with pytest.raises(RuntimeError, match="authentication failed"):
                provider.complete("prompt")

    def test_retries_on_network_error_then_succeeds(self):
        import httpx
        from data_analyst.llm.providers.openrouter import OpenRouterProvider
        good_resp = self._make_response(200, {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        })
        net_exc = httpx.ConnectError("[Errno 11001] getaddrinfo failed")

        with patch("data_analyst.llm.providers.openrouter.httpx.post",
                   side_effect=[net_exc, good_resp]), \
             patch("data_analyst.llm.providers.openrouter.time.sleep"):
            provider = OpenRouterProvider("key", "model")
            result = provider.complete("prompt")

        assert result.text == "ok"

    def test_network_error_exhausted_raises_clean_message(self):
        import httpx
        from data_analyst.llm.providers.openrouter import OpenRouterProvider
        net_exc = httpx.ConnectError("[Errno 11001] getaddrinfo failed")

        with patch("data_analyst.llm.providers.openrouter.httpx.post", side_effect=net_exc), \
             patch("data_analyst.llm.providers.openrouter.time.sleep"):
            provider = OpenRouterProvider("key", "model")
            with pytest.raises(RuntimeError, match="Couldn't reach OpenRouter"):
                provider.complete("prompt")
