"""Golden-path smoke test — runs the full pipeline with stub LLM and SQLite."""
import io
import json
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from data_analyst.db.models import Base
from data_analyst.db import session as session_module


@pytest.fixture(autouse=True)
def _stub_env(monkeypatch, tmp_path):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATA_ANALYST_DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("DATA_ANALYST_GEMINI_API_KEY", "")  # force stub
    monkeypatch.setenv("DATA_ANALYST_UPLOAD_DIR", str(tmp_path / "uploads"))


@pytest.fixture(autouse=True)
def _use_sqlite(_stub_env, tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(session_module, "_engine", engine)
    monkeypatch.setattr(session_module, "_SessionLocal", factory)
    monkeypatch.setattr(session_module, "init_db", lambda: None)
    yield
    engine.dispose()


@pytest.fixture
def client(_use_sqlite, _stub_env, monkeypatch):
    # Reset LLM client so stub provider is picked up fresh
    import data_analyst.graph.nodes as nodes_module
    monkeypatch.setattr(nodes_module, "_llm_client", None)
    monkeypatch.setattr(nodes_module, "_llm_provider_name", "stub")

    from data_analyst.api import create_app
    app = create_app()
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


def _make_csv() -> bytes:
    return b"name,value,region\nalice,10,north\nbob,20,south\ncarol,30,north\n"


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "ok"


def test_upload(client, tmp_path):
    csv_bytes = _make_csv()
    resp = client.post(
        "/upload",
        files={"file": ("test.csv", io.BytesIO(csv_bytes), "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["row_count"] == 3
    assert data["col_count"] == 3
    assert "name" in data["columns"]
    return data["dataset_id"]


def test_ask_golden_path(client, tmp_path):
    # Upload
    csv_bytes = _make_csv()
    up = client.post(
        "/upload",
        files={"file": ("sales.csv", io.BytesIO(csv_bytes), "text/csv")},
    )
    assert up.status_code == 200, up.text
    dataset_id = up.json()["data"]["dataset_id"]

    # Ask
    resp = client.post("/ask", json={"dataset_id": dataset_id, "question": "What is the total value?"})
    assert resp.status_code == 200, resp.text
    result = resp.json()["data"]
    assert result["status"] == "completed"
    assert result["answer"] is not None
    assert len(result["answer"]) > 0
    assert result["iteration_count"] >= 1


def test_datasets_list(client):
    csv_bytes = _make_csv()
    client.post("/upload", files={"file": ("a.csv", io.BytesIO(csv_bytes), "text/csv")})
    resp = client.get("/datasets")
    assert resp.status_code == 200
    datasets = resp.json()["data"]
    assert len(datasets) >= 1
    assert datasets[0]["filename"] == "a.csv"


def test_ui_renders_with_stub_banner(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "stub mode" in resp.text.lower()
    assert "DATA_ANALYST_GEMINI_API_KEY" in resp.text


def test_ask_unknown_dataset(client):
    resp = client.post("/ask", json={"dataset_id": "nonexistent", "question": "hello?"})
    assert resp.status_code == 404


def test_upload_non_csv(client):
    resp = client.post(
        "/upload",
        files={"file": ("data.txt", io.BytesIO(b"hello world"), "text/plain")},
    )
    assert resp.status_code == 400
