from google import genai
from data_analyst.llm.providers.base import LLMProvider, LLMResponse


class GeminiProvider(LLMProvider):
    def __init__(self, api_key: str, model: str) -> None:
        self._client = genai.Client(api_key=api_key)
        self._model = model

    def complete(self, prompt: str) -> LLMResponse:
        response = self._client.models.generate_content(model=self._model, contents=prompt)
        usage = response.usage_metadata
        tokens_in = getattr(usage, "prompt_token_count", 0) or 0
        tokens_out = getattr(usage, "candidates_token_count", 0) or 0
        return LLMResponse(text=response.text, tokens_input=tokens_in, tokens_output=tokens_out)
