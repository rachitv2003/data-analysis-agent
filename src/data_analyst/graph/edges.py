from data_analyst.graph.state import AgentState


def after_setup(state: AgentState) -> str:
    if state.get("error") or state.get("status") == "failed":
        return "handle_error"
    return "plan_action"


def after_plan(state: AgentState) -> str:
    if state.get("error") or state.get("status") == "failed":
        return "handle_error"
    response = state.get("llm_response", "").strip()
    if response.upper().startswith("FINAL ANSWER:"):
        return "finalize"
    return "execute_action"


def after_execute(state: AgentState) -> str:
    # Always loop back to plan_action (errors are appended to history for self-correction)
    if state.get("error") or state.get("status") == "failed":
        return "handle_error"
    return "plan_action"
