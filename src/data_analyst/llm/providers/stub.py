from data_analyst.llm.providers.base import LLMProvider, LLMResponse

_STUB_TOKENS_IN = 10
_STUB_TOKENS_OUT = 20


class StubLLMProvider(LLMProvider):
    """Offline stub — no API key required. Returns Markdown-formatted output."""

    def complete(self, prompt: str) -> LLMResponse:
        if "<node:finalize>" in prompt:
            return LLMResponse(
                text=(
                    "**[stub mode — best-effort summary]**\n\n"
                    "The analysis loop ended before a definitive answer was reached. "
                    "Set `DATA_ANALYST_GEMINI_API_KEY` in your `.env` for real analysis."
                ),
                tokens_input=_STUB_TOKENS_IN,
                tokens_output=_STUB_TOKENS_OUT,
            )

        if "<node:select>" in prompt:
            import json as _json, re as _re
            # Extract the first dataset ID from the schema block
            match = _re.search(r'\(id: ([^)]+)\)', prompt)
            first_id = match.group(1) if match else ""
            return LLMResponse(
                text=_json.dumps([first_id]) if first_id else "[]",
                tokens_input=_STUB_TOKENS_IN,
                tokens_output=_STUB_TOKENS_OUT,
            )

        if "<node:plan>" not in prompt:
            return LLMResponse(
                text="FINAL ANSWER: [stub] Unable to process — missing plan tag.",
                tokens_input=_STUB_TOKENS_IN,
                tokens_output=_STUB_TOKENS_OUT,
            )

        iteration = prompt.count("Result:") + prompt.count("Error:")

        if iteration == 0:
            return LLMResponse(
                text="df.describe().to_string()",
                tokens_input=_STUB_TOKENS_IN,
                tokens_output=_STUB_TOKENS_OUT,
            )

        return LLMResponse(
            text=(
                "FINAL ANSWER: **[stub mode]** Here is a summary of your dataset:\n\n"
                "- The data was loaded and described successfully.\n"
                "- Set `DATA_ANALYST_GEMINI_API_KEY` in your `.env` for **real analysis**.\n\n"
                "| Metric | Value |\n"
                "|--------|-------|\n"
                "| Status | Stub |\n"
                "| Iterations | 2 |"
            ),
            tokens_input=_STUB_TOKENS_IN,
            tokens_output=_STUB_TOKENS_OUT,
        )
