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
    if state.get("error") or state.get("status") == "failed":
        return "handle_error"

    # C20: 3+ consecutive errors → force best-effort synthesis immediately
    history = state.get("action_history", [])
    if len(history) >= 3 and all(h.get("is_error") for h in history[-3:]):
        return "force_finalize"

    # C20: max iterations reached → force best-effort synthesis
    from data_analyst.config.settings import get_settings
    if state.get("iteration_count", 0) >= get_settings().max_iterations:
        return "force_finalize"

    return "plan_action"
