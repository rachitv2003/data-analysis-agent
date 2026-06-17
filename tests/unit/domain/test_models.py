from datetime import datetime, timezone
from data_analyst.domain.models import Dataset, QueryRun


def test_dataset_model():
    now = datetime.now(timezone.utc)
    d = Dataset(
        id="abc",
        filename="data.csv",
        file_path="/uploads/data.csv",
        row_count=50,
        col_count=4,
        columns=["a", "b", "c", "d"],
        created_at=now,
    )
    assert d.row_count == 50
    assert len(d.columns) == 4


def test_query_run_model():
    now = datetime.now(timezone.utc)
    r = QueryRun(
        id="run1",
        dataset_id="abc",
        question="What is the total?",
        answer=None,
        status="pending",
        error_message=None,
        action_history=[],
        iteration_count=0,
        created_at=now,
        updated_at=now,
    )
    assert r.status == "pending"
    assert r.answer is None
