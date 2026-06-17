from data_analyst.llm.providers.base import LLMProvider, LLMResponse


class LLMClient:
    def __init__(self, provider: LLMProvider) -> None:
        self._provider = provider

    def complete(self, prompt: str) -> LLMResponse:
        return self._provider.complete(prompt)
