from typing import TypedDict


class AgentState(TypedDict, total=False):
    run_id: str
    dataset_ids: list[str]              # C14: one or more datasets
    dataset_context: str | None         # C12: combined context from all datasets
    session_id: str | None
    question: str
    conversation_history: list[dict]
    action_history: list[dict]
    iteration_count: int
    llm_response: str
    tokens_input: int
    tokens_output: int
    charts: list[str]            # C4: Plotly JSON specs captured during this run
    answer: str | None
    error: str | None
    status: str
    selector_reasoning: str | None      # C19: raw LLM output from dataset-selector call
