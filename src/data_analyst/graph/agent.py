from langgraph.graph import StateGraph, END

from data_analyst.graph.state import AgentState
from data_analyst.graph.nodes import setup, plan_action, execute_action, finalize, handle_error
from data_analyst.graph.edges import after_setup, after_plan, after_execute


def _build_graph() -> StateGraph:
    g = StateGraph(AgentState)

    g.add_node("setup", setup)
    g.add_node("plan_action", plan_action)
    g.add_node("execute_action", execute_action)
    g.add_node("finalize", finalize)
    g.add_node("handle_error", handle_error)

    g.set_entry_point("setup")

    g.add_conditional_edges("setup", after_setup, {
        "plan_action": "plan_action",
        "handle_error": "handle_error",
    })
    g.add_conditional_edges("plan_action", after_plan, {
        "execute_action": "execute_action",
        "finalize": "finalize",
        "handle_error": "handle_error",
    })
    g.add_conditional_edges("execute_action", after_execute, {
        "plan_action": "plan_action",
        "handle_error": "handle_error",
    })

    g.add_edge("finalize", END)
    g.add_edge("handle_error", END)

    return g.compile()


agent_graph = _build_graph()
