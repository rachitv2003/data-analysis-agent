from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class LLMResponse:
    text: str
    tokens_input: int
    tokens_output: int


class LLMProvider(ABC):
    @abstractmethod
    def complete(self, prompt: str) -> LLMResponse:
        ...
