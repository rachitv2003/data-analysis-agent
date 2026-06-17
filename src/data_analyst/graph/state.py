from typing import TypedDict


class AgentState(TypedDict, total=False):
    run_id: str
    dataset_id: str
    session_id: str | None
    question: str
    conversation_history: list[dict]  # [{"question": str, "answer": str}] from prior turns
    action_history: list[dict]        # [{"action": str, "result": str, "is_error": bool}]
    iteration_count: int
    llm_response: str                 # raw last LLM output — router checks for FINAL ANSWER
    tokens_input: int                 # running total prompt tokens across all iterations
    tokens_output: int                # running total completion tokens across all iterations
    answer: str | None
    error: str | None
    status: str                       # completed | failed
