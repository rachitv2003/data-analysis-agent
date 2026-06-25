"""Unit tests for prompt assembly in graph.nodes._build_prompt."""
from datetime import date

import pandas as pd
import pytest

from data_analyst.graph import nodes

# _tmp_db fixture is provided by conftest.py in this directory.


@pytest.fixture
def _df(monkeypatch):
    """Register a small DataFrame for a run and clean it up afterwards."""
    run_id = "test-prompt-run"
    df = pd.DataFrame({"value": [1, 2, 3], "date": ["2026-06-23", "2026-06-24", "2026-06-25"]})
    nodes._dataframes[run_id] = {"quizzes": df}
    yield run_id
    nodes._dataframes.pop(run_id, None)


def test_build_prompt_injects_current_date(_tmp_db, _df):
    """The prompt must state today's date and tell the LLM to resolve unqualified
    dates against it — the fix for the wrong-year disambiguation bug."""
    state = {
        "run_id": _df,
        "question": "how many quizzes were attempted on June 23rd?",
        "dataset_ids": ["d1"],
        "action_history": [],
        "conversation_history": [],
    }
    prompt, breakdown = nodes._build_prompt(state)

    assert date.today().isoformat() in prompt
    assert "interpret it relative to" in prompt.lower()
    # The date line is attributed to the residual system_overhead bucket.
    assert breakdown["system_overhead"] > 0
