from data_analyst.llm.providers.base import LLMProvider


class LLMClient:
    def __init__(self, provider: LLMProvider) -> None:
        self._provider = provider

    def complete(self, prompt: str) -> str:
        return self._provider.complete(prompt)
