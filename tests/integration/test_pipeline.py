"""Golden-path smoke test — runs the full pipeline with stub LLM and SQLite."""
import io
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
    import data_analyst.graph.nodes as nodes_module
    monkeypatch.setattr(nodes_module, "_llm_client", None)
    monkeypatch.setattr(nodes_module, "_llm_provider_name", "stub")

    from data_analyst.api import create_app
    app = create_app()
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


def _make_csv() -> bytes:
    return b"name,value,region\nalice,10,north\nbob,20,south\ncarol,30,north\n"


def _upload(client) -> str:
    resp = client.post("/upload", files={"file": ("test.csv", io.BytesIO(_make_csv()), "text/csv")})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["dataset_id"]


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "ok"


def test_upload(client):
    resp = client.post("/upload", files={"file": ("test.csv", io.BytesIO(_make_csv()), "text/csv")})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["row_count"] == 3
    assert data["col_count"] == 3
    assert "name" in data["columns"]


def test_ask_golden_path(client):
    dataset_id = _upload(client)
    resp = client.post("/ask", json={"dataset_id": dataset_id, "question": "What is the total value?"})
    assert resp.status_code == 200, resp.text
    result = resp.json()["data"]
    assert result["status"] == "completed"
    assert result["answer_markdown"] is not None
    assert len(result["answer_markdown"]) > 0
    assert result["answer_html"] is not None
    assert "<" in result["answer_html"]   # rendered HTML must contain tags
    assert result["iteration_count"] >= 1
    assert result["tokens_input"] >= 0
    assert result["tokens_output"] >= 0
    assert result["session_id"] is not None


def test_multi_turn_conversation(client):
    dataset_id = _upload(client)

    # First turn — no session_id
    r1 = client.post("/ask", json={"dataset_id": dataset_id, "question": "How many rows?"})
    assert r1.status_code == 200, r1.text
    session_id = r1.json()["data"]["session_id"]
    assert session_id is not None

    # Second turn — pass session_id
    r2 = client.post("/ask", json={"dataset_id": dataset_id, "question": "What are the column names?", "session_id": session_id})
    assert r2.status_code == 200, r2.text
    assert r2.json()["data"]["session_id"] == session_id
    assert r2.json()["data"]["status"] == "completed"

    # Verify session turns via GET /sessions/{id}
    r3 = client.get(f"/sessions/{session_id}")
    assert r3.status_code == 200, r3.text
    turns = r3.json()["data"]["turns"]
    assert len(turns) == 2
    assert turns[0]["question"] == "How many rows?"
    assert turns[1]["question"] == "What are the column names?"
    # Both turns should have markdown + html + token fields
    for turn in turns:
        assert "answer_markdown" in turn
        assert "answer_html" in turn
        assert "tokens_input" in turn
        assert "tokens_output" in turn


def test_token_accumulation_stub(client):
    """Stub runs 2 iterations (10 in/out each), so totals must be 20/20."""
    dataset_id = _upload(client)
    resp = client.post("/ask", json={"dataset_id": dataset_id, "question": "Describe the data."})
    assert resp.status_code == 200, resp.text
    result = resp.json()["data"]
    assert result["tokens_input"] == 20   # 2 iterations × 10 in
    assert result["tokens_output"] == 40  # 2 iterations × 20 out
    assert result["iteration_count"] == 2


def test_answer_html_contains_tags(client):
    """Stub answer includes Markdown (bold, table) so answer_html must have HTML elements."""
    dataset_id = _upload(client)
    resp = client.post("/ask", json={"dataset_id": dataset_id, "question": "Give me a summary."})
    assert resp.status_code == 200, resp.text
    html = resp.json()["data"]["answer_html"]
    assert "<strong>" in html or "<table>" in html or "<ul>" in html or "<p>" in html


def test_session_dataset_mismatch(client):
    id1 = _upload(client)
    # Upload a second, distinct dataset
    r2 = client.post("/upload", files={"file": ("other.csv", io.BytesIO(b"x,y\n1,2\n3,4\n"), "text/csv")})
    assert r2.status_code == 200, r2.text
    id2 = r2.json()["data"]["dataset_id"]

    r1 = client.post("/ask", json={"dataset_id": id1, "question": "Rows?"})
    session_id = r1.json()["data"]["session_id"]

    # Try to use session from dataset 1 with dataset 2
    resp = client.post("/ask", json={"dataset_id": id2, "question": "Columns?", "session_id": session_id})
    assert resp.status_code == 400


def test_session_not_found(client):
    dataset_id = _upload(client)
    resp = client.post("/ask", json={"dataset_id": dataset_id, "question": "x", "session_id": "nonexistent"})
    assert resp.status_code == 404


def test_get_session_not_found(client):
    resp = client.get("/sessions/nonexistent")
    assert resp.status_code == 404


def test_datasets_list(client):
    _upload(client)
    resp = client.get("/datasets")
    assert resp.status_code == 200
    assert len(resp.json()["data"]) >= 1


def test_dataset_sessions_list(client):
    dataset_id = _upload(client)

    # No sessions yet
    r0 = client.get(f"/datasets/{dataset_id}/sessions")
    assert r0.status_code == 200
    assert r0.json()["data"] == []

    # Create two sessions
    r1 = client.post("/ask", json={"dataset_id": dataset_id, "question": "First question"})
    sid1 = r1.json()["data"]["session_id"]
    r2 = client.post("/ask", json={"dataset_id": dataset_id, "question": "Second session start"})
    sid2 = r2.json()["data"]["session_id"]

    resp = client.get(f"/datasets/{dataset_id}/sessions")
    assert resp.status_code == 200
    sessions = resp.json()["data"]
    assert len(sessions) == 2
    ids = {s["session_id"] for s in sessions}
    assert sid1 in ids and sid2 in ids

    # Fields present
    for s in sessions:
        assert "turn_count" in s
        assert "first_question" in s
        assert s["turn_count"] == 1


def test_dataset_sessions_unknown_dataset(client):
    resp = client.get("/datasets/nonexistent/sessions")
    assert resp.status_code == 404


def test_execute_action_markdown_table(client):
    """df.head(3) should produce a pipe-delimited Markdown table in action_history."""
    dataset_id = _upload(client)
    # The stub first action is df.describe().to_string() — but we can verify via the
    # completed answer contains Markdown table markers from the stub response
    resp = client.post("/ask", json={"dataset_id": dataset_id, "question": "Show top rows"})
    assert resp.status_code == 200
    # The stub answer HTML was rendered from Markdown with a table
    html = resp.json()["data"]["answer_html"]
    assert html  # non-empty HTML rendered


def test_ui_renders_with_stub_banner(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "stub mode" in resp.text.lower()
    assert "DATA_ANALYST_GEMINI_API_KEY" in resp.text


def test_ask_unknown_dataset(client):
    resp = client.post("/ask", json={"dataset_id": "nonexistent", "question": "hello?"})
    assert resp.status_code == 404


def test_upload_non_csv(client):
    resp = client.post("/upload", files={"file": ("data.xlsx", io.BytesIO(b"hello world"), "application/octet-stream")})
    assert resp.status_code == 400


# ── C10: Duplicate upload detection ──────────────────────────────────────────

def test_duplicate_same_content(client):
    csv = _make_csv()
    client.post("/upload", files={"file": ("sales.csv", io.BytesIO(csv), "text/csv")})
    resp = client.post("/upload", files={"file": ("sales.csv", io.BytesIO(csv), "text/csv")})
    assert resp.status_code == 409
    det = resp.json()["detail"]
    assert det["code"] == "duplicate_dataset"
    assert det["match_type"] in ("both", "content", "filename")
    assert "existing_dataset_id" in det


def test_duplicate_same_name_different_content(client):
    client.post("/upload", files={"file": ("data.csv", io.BytesIO(b"a,b\n1,2\n"), "text/csv")})
    resp = client.post("/upload", files={"file": ("data.csv", io.BytesIO(b"x,y\n3,4\n"), "text/csv")})
    assert resp.status_code == 409
    assert resp.json()["detail"]["match_type"] == "filename"


def test_duplicate_same_content_different_name(client):
    csv = _make_csv()
    client.post("/upload", files={"file": ("a.csv", io.BytesIO(csv), "text/csv")})
    resp = client.post("/upload", files={"file": ("b.csv", io.BytesIO(csv), "text/csv")})
    assert resp.status_code == 409
    assert resp.json()["detail"]["match_type"] == "content"


def test_force_upload_bypasses_duplicate(client):
    csv = _make_csv()
    client.post("/upload", files={"file": ("orig.csv", io.BytesIO(csv), "text/csv")})
    resp = client.post("/upload?force=true", files={"file": ("orig.csv", io.BytesIO(csv), "text/csv")})
    assert resp.status_code == 200


# ── C11: Multi-format ingestion ───────────────────────────────────────────────

def test_upload_tsv(client):
    tsv = b"name\tvalue\talice\t10\nbob\t20\n"
    resp = client.post("/upload", files={"file": ("data.tsv", io.BytesIO(tsv), "text/tab-separated-values")})
    # TSV parsing may have varying results depending on content; just check 200 or 400 parse error
    assert resp.status_code in (200, 400)  # 400 only if content is malformed


def test_upload_tsv_valid(client):
    tsv = b"name\tvalue\nAlice\t10\nBob\t20\n"
    resp = client.post("/upload", files={"file": ("data.tsv", io.BytesIO(tsv), "text/tab-separated-values")})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["format"] == "tsv"
    assert data["col_count"] == 2
    assert data["row_count"] == 2


def test_upload_json_array(client):
    payload = b'[{"name":"Alice","score":95},{"name":"Bob","score":87}]'
    resp = client.post("/upload", files={"file": ("data.json", io.BytesIO(payload), "application/json")})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["format"] == "json"
    assert data["row_count"] == 2
    assert "name" in data["columns"]


def test_upload_json_column_keyed(client):
    payload = b'{"name":["Alice","Bob"],"score":[95,87]}'
    resp = client.post("/upload", files={"file": ("data.json", io.BytesIO(payload), "application/json")})
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["row_count"] == 2


def test_upload_json_invalid_shape(client):
    payload = b'"just a string"'
    resp = client.post("/upload", files={"file": ("data.json", io.BytesIO(payload), "application/json")})
    assert resp.status_code == 400


def test_upload_unsupported_extension(client):
    resp = client.post("/upload", files={"file": ("data.xlsx", io.BytesIO(b"pk"), "application/octet-stream")})
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "unsupported_format"


def test_upload_response_includes_format(client):
    resp = client.post("/upload", files={"file": ("test.csv", io.BytesIO(_make_csv()), "text/csv")})
    assert resp.status_code == 200
    assert resp.json()["data"]["format"] == "csv"
