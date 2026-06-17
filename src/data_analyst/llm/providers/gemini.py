import google.generativeai as genai
from data_analyst.llm.providers.base import LLMProvider


class GeminiProvider(LLMProvider):
    def __init__(self, api_key: str, model: str) -> None:
        genai.configure(api_key=api_key)
        self._model = genai.GenerativeModel(model)

    def complete(self, prompt: str) -> str:
        response = self._model.generate_content(prompt)
        return response.text
