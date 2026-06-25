"""Unit tests for C29 token-breakdown accumulation (graph.nodes._update_prompt_breakdown).

The breakdown must SUM across every LLM call in a run so its `total_prompt`
reconciles with `run.tokens_input` (the headline "tokens in"), while keeping a
separate `last_prompt` (single-call size) for the context-window bar.
"""
import json

from data_analyst.graph import nodes


def _make_run():
    from data_analyst.db.session import init_db, create_db_session
    from data_analyst.db.models import QueryRunRow
    init_db()
    with create_db_session() as db:
        run = QueryRunRow(dataset_id="d1", question="q", status="running")
        db.add(run)
        db.flush()
        return run.id


def _breakdown(run_id):
    from data_analyst.db.session import create_db_session
    from data_analyst.db.models import QueryRunRow
    with create_db_session() as db:
        raw = db.get(QueryRunRow, run_id).prompt_breakdown
    return json.loads(raw) if raw else {}


def test_breakdown_accumulates_across_calls(_tmp_db):
    run_id = _make_run()

    # Two plan_action-style calls.
    nodes._update_prompt_breakdown(run_id, {"system_overhead": 5, "total_prompt": 10}, last_prompt=10)
    nodes._update_prompt_breakdown(run_id, {"system_overhead": 7, "total_prompt": 12}, last_prompt=12)

    bd = _breakdown(run_id)
    assert bd["system_overhead"] == 12   # 5 + 7 summed across calls
    assert bd["total_prompt"] == 22       # 10 + 12 cumulative
    assert bd["last_prompt"] == 12         # overwritten to the most recent call


def test_breakdown_folds_auxiliary_tokens(_tmp_db):
    run_id = _make_run()
    nodes._update_prompt_breakdown(run_id, {"system_overhead": 5, "total_prompt": 10}, last_prompt=10)

    # Selector / suggestion / force-finalize style auxiliary input tokens.
    nodes._update_prompt_breakdown(run_id, {"auxiliary": 3, "total_prompt": 3})

    bd = _breakdown(run_id)
    assert bd["auxiliary"] == 3
    assert bd["total_prompt"] == 13   # 10 + 3 — reconciles with run.tokens_input
    assert bd["last_prompt"] == 10     # auxiliary calls do not change context fullness
