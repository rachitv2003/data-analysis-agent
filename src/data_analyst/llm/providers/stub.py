from data_analyst.llm.providers.base import LLMProvider


class StubLLMProvider(LLMProvider):
    """Offline stub — no API key required. Branches on iteration to avoid identical output."""

    def complete(self, prompt: str) -> str:
        # Detect which iteration we're on by counting how many results are in history
        # The prompt includes previous action/result pairs — count them
        iteration = prompt.count("Result:") + prompt.count("Error:")

        if "<node:plan>" not in prompt:
            return "FINAL ANSWER: [stub] Unable to process — missing plan tag."

        if iteration == 0:
            # First iteration: return a real pandas expression
            return "df.describe().to_string()"

        # Second iteration onwards: return final answer
        return "FINAL ANSWER: [stub] Based on the data summary, the dataset contains numeric columns with the statistics shown above. Set GEMINI_API_KEY for real analysis."
