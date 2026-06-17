import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from data_analyst.db.models import Base, DatasetRow, QueryRunRow


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        yield session
    engine.dispose()


def test_create_dataset(db):
    row = DatasetRow(
        filename="test.csv",
        file_path="/tmp/test.csv",
        row_count=100,
        col_count=3,
        columns_json='["a","b","c"]',
    )
    db.add(row)
    db.commit()
    assert row.id is not None
    assert row.created_at is not None


def test_create_query_run(db):
    dataset = DatasetRow(
        filename="test.csv",
        file_path="/tmp/test.csv",
        row_count=10,
        col_count=2,
        columns_json='["x","y"]',
    )
    db.add(dataset)
    db.flush()

    run = QueryRunRow(
        dataset_id=dataset.id,
        question="What is the max x?",
        status="pending",
    )
    db.add(run)
    db.commit()
    assert run.id is not None
    assert run.status == "pending"
    assert run.iteration_count == 0
