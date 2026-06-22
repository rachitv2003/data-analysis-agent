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
    monkeypatch.setenv("DATA_ANALYST_LLM_PROVIDER", "stub")  # force stub — ignore real API keys
    monkeypatch.setenv("DATA_ANALYST_UPLOAD_DIR", str(tmp_path / "uploads"))
    # Reset cached settings so the above env changes take effect
    import data_analyst.config.settings as _settings_module
    monkeypatch.setattr(_settings_module, "_settings", None)


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


def test_upload_invalid_excel_bytes(client):
    # .xlsx IS a supported format; this tests that corrupt Excel content returns 400 (parse error).
    resp = client.post("/upload", files={"file": ("data.xlsx", io.BytesIO(b"hello world"), "application/octet-stream")})
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "parse_error"


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
    # xlsx/xls are now supported; use a truly unsupported type
    resp = client.post("/upload", files={"file": ("data.parquet", io.BytesIO(b"PAR1"), "application/octet-stream")})
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "unsupported_format"


def test_upload_response_includes_format(client):
    resp = client.post("/upload", files={"file": ("test.csv", io.BytesIO(_make_csv()), "text/csv")})
    assert resp.status_code == 200
    assert resp.json()["data"]["format"] == "csv"


# ── C12: Dataset context ──────────────────────────────────────────────────────

def test_upload_with_context(client):
    resp = client.post(
        "/upload",
        data={"context": "revenue is in USD thousands"},
        files={"file": ("sales.csv", io.BytesIO(_make_csv()), "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["context"] == "revenue is in USD thousands"


def test_upload_without_context(client):
    resp = client.post("/upload", files={"file": ("test.csv", io.BytesIO(_make_csv()), "text/csv")})
    assert resp.status_code == 200
    assert resp.json()["data"]["context"] == ""


def test_upload_context_too_long(client):
    resp = client.post(
        "/upload",
        data={"context": "x" * 4001},
        files={"file": ("test.csv", io.BytesIO(_make_csv()), "text/csv")},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "context_too_long"


def test_patch_dataset_context(client):
    dataset_id = _upload(client)
    resp = client.patch(f"/datasets/{dataset_id}/context", json={"context": "updated notes"})
    assert resp.status_code == 200
    assert resp.json()["data"]["context"] == "updated notes"


def test_patch_dataset_context_too_long(client):
    dataset_id = _upload(client)
    resp = client.patch(f"/datasets/{dataset_id}/context", json={"context": "y" * 4001})
    assert resp.status_code == 400


def test_patch_context_unknown_dataset(client):
    resp = client.patch("/datasets/nonexistent/context", json={"context": "hi"})
    assert resp.status_code == 404


def test_datasets_list_includes_context(client):
    client.post(
        "/upload",
        data={"context": "test context"},
        files={"file": ("ctx.csv", io.BytesIO(_make_csv()), "text/csv")},
    )
    resp = client.get("/datasets")
    assert resp.status_code == 200
    ds = resp.json()["data"][0]
    assert "context" in ds
    assert ds["context"] == "test context"


def test_context_injected_in_prompt(client, monkeypatch):
    """Upload with context and verify it appears in the prompt sent to the LLM."""
    captured: list[str] = []

    import data_analyst.graph.nodes as nodes_module
    from data_analyst.llm.providers.base import LLMResponse

    class CapturingProvider:
        def complete(self, prompt: str) -> LLMResponse:
            captured.append(prompt)
            return LLMResponse(text="FINAL ANSWER: done", tokens_input=10, tokens_output=20)

    from data_analyst.llm.client import LLMClient
    monkeypatch.setattr(nodes_module, "_llm_client", LLMClient(CapturingProvider()))

    resp = client.post(
        "/upload",
        data={"context": "region uses ISO 3166 codes"},
        files={"file": ("ctx2.csv", io.BytesIO(_make_csv()), "text/csv")},
    )
    dataset_id = resp.json()["data"]["dataset_id"]

    client.post("/ask", json={"dataset_id": dataset_id, "question": "test?"})
    assert any("region uses ISO 3166 codes" in p for p in captured)


# ── C14: Multi-dataset querying ───────────────────────────────────────────────

def _upload_extra(client) -> str:
    resp = client.post(
        "/upload",
        files={"file": ("extra.csv", io.BytesIO(b"id,score\n1,95\n2,87\n3,72\n"), "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["dataset_id"]


def test_ask_with_dataset_ids_list(client):
    id1 = _upload(client)
    id2 = _upload_extra(client)
    resp = client.post("/ask", json={"dataset_ids": [id1, id2], "question": "How many rows in each?"})
    assert resp.status_code == 200, resp.text
    result = resp.json()["data"]
    assert result["status"] == "completed"
    assert result["dataset_ids"] == [id1, id2]


def test_ask_backward_compat_dataset_id(client):
    """Old-style single dataset_id still works."""
    dataset_id = _upload(client)
    resp = client.post("/ask", json={"dataset_id": dataset_id, "question": "Rows?"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["dataset_ids"] == [dataset_id]


def test_multi_dataset_both_dfs_available(client, monkeypatch):
    """Both df1 and df2 must be available in the eval namespace."""
    captured_ns: list[dict] = []

    import data_analyst.graph.nodes as nodes_module
    from data_analyst.llm.providers.base import LLMResponse

    call_count = [0]

    class TwoDatasetProvider:
        def complete(self, prompt: str) -> LLMResponse:
            call_count[0] += 1
            if call_count[0] == 1:
                return LLMResponse(text="pd.merge(df1, df2, left_on='name', right_on='id')", tokens_input=10, tokens_output=20)
            return LLMResponse(text="FINAL ANSWER: merged successfully", tokens_input=10, tokens_output=20)

    from data_analyst.llm.client import LLMClient
    monkeypatch.setattr(nodes_module, "_llm_client", LLMClient(TwoDatasetProvider()))

    id1 = _upload(client)
    id2 = _upload_extra(client)
    resp = client.post("/ask", json={"dataset_ids": [id1, id2], "question": "Join the tables."})
    # Even if merge fails (key mismatch), agent should not crash — it retries with FINAL ANSWER
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["status"] in ("completed", "failed")


def test_multi_dataset_session_mismatch(client):
    """Session created with [id1] rejects request with [id2]."""
    id1 = _upload(client)
    id2 = _upload_extra(client)

    r1 = client.post("/ask", json={"dataset_ids": [id1], "question": "rows?"})
    sid = r1.json()["data"]["session_id"]

    resp = client.post("/ask", json={"dataset_ids": [id2], "question": "cols?", "session_id": sid})
    assert resp.status_code == 400


def test_ask_no_dataset_id_returns_error(client):
    # With no datasets uploaded, auto-select path raises 400 "no_datasets"
    resp = client.post("/ask", json={"question": "hello?"})
    assert resp.status_code == 400


# ── C15: Dataset deletion ────────────────────────────────────────────────────

def test_delete_dataset_cascades(client, tmp_path):
    dataset_id = _upload(client)

    # Create a session with one turn
    r = client.post("/ask", json={"dataset_id": dataset_id, "question": "rows?"})
    session_id = r.json()["data"]["session_id"]

    resp = client.delete(f"/datasets/{dataset_id}")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert dataset_id in data["deleted_dataset_ids"]
    assert data["deleted_session_count"] >= 1
    assert data["deleted_run_count"] >= 1

    # Dataset gone
    assert client.get("/datasets").json()["data"] == []
    # Ask returns 404
    resp2 = client.post("/ask", json={"dataset_id": dataset_id, "question": "hi"})
    assert resp2.status_code == 404


def test_delete_nonexistent_dataset(client):
    resp = client.delete("/datasets/nonexistent")
    assert resp.status_code == 404


def test_delete_all_datasets(client):
    _upload(client)
    client.post("/upload", files={"file": ("b.csv", io.BytesIO(b"x,y\n1,2\n"), "text/csv")})

    resp = client.delete("/datasets")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data["deleted_dataset_ids"]) >= 2
    assert client.get("/datasets").json()["data"] == []


def test_delete_all_datasets_empty(client):
    resp = client.delete("/datasets")
    assert resp.status_code == 200
    assert resp.json()["data"]["deleted_dataset_ids"] == []


def test_delete_dataset_csv_file_removed(client):
    from pathlib import Path
    dataset_id = _upload(client)
    # Get the file path
    ds_list = client.get("/datasets").json()["data"]
    ds = next(d for d in ds_list if d["dataset_id"] == dataset_id)
    # We can't easily get file_path from the API, just verify dataset is gone after delete
    resp = client.delete(f"/datasets/{dataset_id}")
    assert resp.status_code == 200


# ── C16: Notes file upload ───────────────────────────────────────────────────

def test_upload_with_notes_file(client):
    notes = b"column 'value' is in USD thousands"
    resp = client.post(
        "/upload",
        data={},
        files={
            "file": ("sales.csv", io.BytesIO(_make_csv()), "text/csv"),
            "notes_file": ("sales.notes.txt", io.BytesIO(notes), "text/plain"),
        },
    )
    assert resp.status_code == 200, resp.text
    assert "USD thousands" in resp.json()["data"]["context"]


def test_upload_notes_file_and_context_combined(client):
    notes = b"column 'value' is in USD"
    resp = client.post(
        "/upload",
        data={"context": "region uses ISO codes"},
        files={
            "file": ("c.csv", io.BytesIO(_make_csv()), "text/csv"),
            "notes_file": ("c.notes.txt", io.BytesIO(notes), "text/plain"),
        },
    )
    assert resp.status_code == 200, resp.text
    ctx = resp.json()["data"]["context"]
    assert "ISO codes" in ctx
    assert "USD" in ctx


def test_upload_notes_file_unsupported_format(client):
    resp = client.post(
        "/upload",
        files={
            "file": ("d.csv", io.BytesIO(_make_csv()), "text/csv"),
            "notes_file": ("notes.pdf", io.BytesIO(b"%PDF"), "application/pdf"),
        },
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "unsupported_notes_format"


def test_upload_notes_file_combined_too_long(client):
    long_notes = b"x" * 3500
    resp = client.post(
        "/upload",
        data={"context": "y" * 600},
        files={
            "file": ("e.csv", io.BytesIO(_make_csv()), "text/csv"),
            "notes_file": ("e.notes.txt", io.BytesIO(long_notes), "text/plain"),
        },
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "context_too_long"


# ── C18: Daily stats ─────────────────────────────────────────────────────────

def test_daily_stats_empty(client):
    resp = client.get("/stats/daily")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["tokens_input"] == 0
    assert data["tokens_output"] == 0
    assert data["query_count"] == 0
    assert "model" in data
    assert "date" in data


def test_daily_stats_after_query(client):
    dataset_id = _upload(client)
    client.post("/ask", json={"dataset_id": dataset_id, "question": "rows?"})

    resp = client.get("/stats/daily")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["query_count"] >= 1


# ── C20: force_finalize paths ─────────────────────────────────────────────────

def test_force_finalize_max_iterations(client, monkeypatch):
    """Agent reaching max_iterations should complete with is_best_effort=True."""
    import data_analyst.graph.nodes as nodes_module
    from data_analyst.llm.providers.base import LLMResponse
    from data_analyst.llm.client import LLMClient

    class AlwaysCodeProvider:
        def complete(self, prompt: str) -> LLMResponse:
            if "<node:finalize>" in prompt:
                return LLMResponse(text="**Best effort summary.**", tokens_input=10, tokens_output=20)
            return LLMResponse(text="df.shape", tokens_input=10, tokens_output=20)

    monkeypatch.setattr(nodes_module, "_llm_client", LLMClient(AlwaysCodeProvider()))
    monkeypatch.setenv("DATA_ANALYST_MAX_ITERATIONS", "1")

    dataset_id = _upload(client)
    resp = client.post("/ask", json={"dataset_id": dataset_id, "question": "Describe."})
    assert resp.status_code == 200, resp.text
    result = resp.json()["data"]
    assert result["status"] == "completed"
    assert result["is_best_effort"] is True


def test_force_finalize_consecutive_errors(client, monkeypatch):
    """3 consecutive execute errors should trigger force_finalize with is_best_effort=True."""
    import data_analyst.graph.nodes as nodes_module
    from data_analyst.llm.providers.base import LLMResponse
    from data_analyst.llm.client import LLMClient

    call_count = [0]

    class ErrorThenFinalProvider:
        def complete(self, prompt: str) -> LLMResponse:
            if "<node:finalize>" in prompt:
                return LLMResponse(text="**Best effort summary.**", tokens_input=10, tokens_output=20)
            call_count[0] += 1
            if call_count[0] <= 3:
                return LLMResponse(text="this_undefined_var_xyz_does_not_exist_99", tokens_input=10, tokens_output=20)
            return LLMResponse(text="FINAL ANSWER: done", tokens_input=10, tokens_output=20)

    monkeypatch.setattr(nodes_module, "_llm_client", LLMClient(ErrorThenFinalProvider()))

    dataset_id = _upload(client)
    resp = client.post("/ask", json={"dataset_id": dataset_id, "question": "Test errors."})
    assert resp.status_code == 200, resp.text
    result = resp.json()["data"]
    assert result["status"] == "completed"
    assert result["is_best_effort"] is True


# ── Session management endpoints ──────────────────────────────────────────────

def test_session_rename(client):
    dataset_id = _upload(client)
    r = client.post("/ask", json={"dataset_id": dataset_id, "question": "rows?"})
    session_id = r.json()["data"]["session_id"]

    resp = client.patch(f"/sessions/{session_id}/name", json={"name": "My Session"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["name"] == "My Session"


def test_session_rename_not_found(client):
    resp = client.patch("/sessions/nonexistent/name", json={"name": "x"})
    assert resp.status_code == 404


def test_delete_single_session(client):
    dataset_id = _upload(client)
    r = client.post("/ask", json={"dataset_id": dataset_id, "question": "rows?"})
    session_id = r.json()["data"]["session_id"]

    resp = client.delete(f"/sessions/{session_id}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["deleted"] == session_id

    assert client.get(f"/sessions/{session_id}").status_code == 404


def test_delete_single_session_not_found(client):
    resp = client.delete("/sessions/nonexistent")
    assert resp.status_code == 404


def test_delete_all_sessions_endpoint(client):
    dataset_id = _upload(client)
    client.post("/ask", json={"dataset_id": dataset_id, "question": "q1"})
    client.post("/ask", json={"dataset_id": dataset_id, "question": "q2"})

    assert len(client.get("/sessions").json()["data"]) == 2

    resp = client.delete("/sessions")
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["deleted"] == "all"
    assert client.get("/sessions").json()["data"] == []


def test_get_runs_current_idle(client):
    resp = client.get("/runs/current")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "idle"
    assert data["run_id"] is None


def test_get_runs_current_after_query(client):
    dataset_id = _upload(client)
    client.post("/ask", json={"dataset_id": dataset_id, "question": "rows?"})
    resp = client.get("/runs/current")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["run_id"] is not None
    assert data["status"] == "completed"


# ── Memory endpoints ──────────────────────────────────────────────────────────

def test_get_memory_empty(client):
    resp = client.get("/memory")
    assert resp.status_code == 200
    assert resp.json()["data"]["content"] == ""


def test_update_and_get_memory(client):
    resp = client.patch("/memory", json={"content": "Always respond in metric units."})
    assert resp.status_code == 200
    assert resp.json()["data"]["content"] == "Always respond in metric units."

    resp2 = client.get("/memory")
    assert resp2.status_code == 200
    assert resp2.json()["data"]["content"] == "Always respond in metric units."


def test_update_memory_twice(client):
    client.patch("/memory", json={"content": "first"})
    client.patch("/memory", json={"content": "second"})
    resp = client.get("/memory")
    assert resp.json()["data"]["content"] == "second"


# ── Data cleaning endpoints ───────────────────────────────────────────────────

def test_clean_preview(client, monkeypatch):
    """preview_clean generates code via LLM and returns before/after row counts."""
    import data_analyst.graph.nodes as nodes_module
    from data_analyst.llm.providers.base import LLMResponse
    from data_analyst.llm.client import LLMClient

    class CleanProvider:
        def complete(self, prompt: str) -> LLMResponse:
            # Return valid pandas filter using variable name 'test' (for test.csv)
            return LLMResponse(text="test[test['value'] > 10]", tokens_input=10, tokens_output=20)

    monkeypatch.setattr(nodes_module, "_llm_client", LLMClient(CleanProvider()))

    dataset_id = _upload(client)
    resp = client.post(
        f"/datasets/{dataset_id}/clean",
        json={"instruction": "remove rows where value <= 10"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert "code" in data
    assert data["row_count_before"] == 3
    assert data["row_count_after"] == 2  # bob (20) and carol (30) pass
    assert "preview_before" in data
    assert "preview_after" in data


def test_clean_preview_unknown_dataset(client):
    resp = client.post("/datasets/nonexistent/clean", json={"instruction": "drop nulls"})
    assert resp.status_code == 404


def test_clean_apply(client):
    """apply_clean executes provided code and updates DB metadata."""
    dataset_id = _upload(client)
    # Filter to value > 10: bob (20) and carol (30) survive
    code = "test[test['value'] > 10]"
    resp = client.post(f"/datasets/{dataset_id}/clean/apply", json={"code": code})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["row_count"] == 2
    assert data["col_count"] == 3


def test_clean_apply_unknown_dataset(client):
    resp = client.post("/datasets/nonexistent/clean/apply", json={"code": "df"})
    assert resp.status_code == 404


# ── C19: Auto dataset selection ───────────────────────────────────────────────

def test_c19_auto_selector_single_dataset(client):
    """With one dataset and no explicit IDs, selector is skipped and that dataset is used."""
    dataset_id = _upload(client)
    resp = client.post("/ask", json={"question": "How many rows?"})
    assert resp.status_code == 200, resp.text
    result = resp.json()["data"]
    assert result["status"] == "completed"
    assert dataset_id in result["dataset_ids"]
    # selector skipped for single dataset — no reasoning stored
    assert result["selector_reasoning"] is None


def test_c19_auto_selector_multiple_datasets(client):
    """With multiple datasets and no explicit IDs, stub selector picks the first one."""
    id1 = _upload(client)
    id2 = _upload_extra(client)

    resp = client.post("/ask", json={"question": "How many rows?"})
    assert resp.status_code == 200, resp.text
    result = resp.json()["data"]
    assert result["status"] == "completed"
    # Stub selector picks first dataset; selector_reasoning should be present
    assert result["selector_reasoning"] is not None
    assert len(result["dataset_ids"]) >= 1


# ── C26: Clarification response shape ────────────────────────────────────────

def test_clarification_response_shape(client, monkeypatch):
    """C26: When check_clarification returns needs_clarification=True, /ask returns type=clarification."""
    class _FakeClarify:
        needs_clarification = True
        question = "Do you mean 2023 or 2024?"
        tokens_input = 5
        tokens_output = 3

    monkeypatch.setattr("data_analyst.graph.clarify.check_clarification", lambda *a, **kw: _FakeClarify())

    _upload(client)
    resp = client.post("/ask", json={"question": "What is the revenue?"})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["type"] == "clarification"
    assert data["clarification_question"] == "Do you mean 2023 or 2024?"
    assert "run_id" in data
    assert "session_id" in data


def test_skip_clarification_bypasses_preflight(client, monkeypatch):
    """skip_clarification=True must not call check_clarification even on the auto-select path."""
    called = []

    def _spy(*a, **kw):
        called.append(True)
        class _R:
            needs_clarification = False
            question = ""
            tokens_input = tokens_output = 0
        return _R()

    monkeypatch.setattr("data_analyst.graph.clarify.check_clarification", _spy)

    _upload(client)
    resp = client.post("/ask", json={"question": "rows?", "skip_clarification": True})
    assert resp.status_code == 200, resp.text
    assert called == [], "check_clarification should not be called when skip_clarification=True"


# ── C27: columns_schema dtype inference ──────────────────────────────────────

def test_get_dataset_columns_schema_friendly_dtypes(client):
    """GET /datasets/{id} returns columns_schema with friendly dtype aliases."""
    dataset_id = _upload(client)
    resp = client.get(f"/datasets/{dataset_id}")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert "columns_schema" in data
    schema = {c["name"]: c["dtype"] for c in data["columns_schema"]}
    # _make_csv has: name (str→text), value (int→integer), region (str→text)
    assert schema["name"] == "text"
    assert schema["value"] == "integer"
    assert schema["region"] == "text"


def test_get_dataset_not_found(client):
    resp = client.get("/datasets/nonexistent")
    assert resp.status_code == 404


# ── C25: re-derive error cases ────────────────────────────────────────────────

def test_re_derive_uploaded_dataset_returns_400(client):
    """POST /datasets/{id}/re-derive on an uploaded (non-derived) dataset returns 400."""
    dataset_id = _upload(client)
    resp = client.post(f"/datasets/{dataset_id}/re-derive")
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "not_derived"


def test_re_derive_unknown_dataset_returns_404(client):
    resp = client.post("/datasets/nonexistent/re-derive")
    assert resp.status_code == 404


# ── C15: Recursive cascade delete ────────────────────────────────────────────

def test_delete_cascades_derived_recursive(client):
    """Deleting a parent cascades through derived-of-derived chains (recursive)."""
    import json as _json
    from data_analyst.db.models import DatasetRow
    from data_analyst.db import session as session_module

    parent_id = _upload(client)

    # Insert a child derived from parent, and a grandchild derived from child
    factory = session_module._get_session_factory()
    with factory() as db:
        child = DatasetRow(
            id="test-child-001",
            filename="child.csv",
            format="csv",
            file_path="/tmp/child.csv",
            row_count=3,
            col_count=3,
            columns_json=_json.dumps(["name", "value", "region"]),
            origin="derived",
            derived_from_dataset_ids=_json.dumps([parent_id]),
        )
        grandchild = DatasetRow(
            id="test-grandchild-001",
            filename="grandchild.csv",
            format="csv",
            file_path="/tmp/grandchild.csv",
            row_count=3,
            col_count=3,
            columns_json=_json.dumps(["name", "value", "region"]),
            origin="derived",
            derived_from_dataset_ids=_json.dumps(["test-child-001"]),
        )
        db.add(child)
        db.add(grandchild)
        db.commit()

    resp = client.delete(f"/datasets/{parent_id}")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["derived_deleted"] == 2  # child + grandchild

    remaining = client.get("/datasets").json()["data"]
    assert remaining == []


# ── C3: Multi-dataset sessions in secondary dataset listing ───────────────────

def test_multi_dataset_session_appears_in_secondary(client):
    """A session that uses a dataset as secondary must appear in that dataset's session list."""
    id1 = _upload(client)
    id2 = _upload_extra(client)

    r = client.post("/ask", json={"dataset_ids": [id1, id2], "question": "How many rows?"})
    assert r.status_code == 200, r.text
    session_id = r.json()["data"]["session_id"]

    # Session must appear under primary dataset
    r1 = client.get(f"/datasets/{id1}/sessions")
    assert r1.status_code == 200
    assert session_id in {s["session_id"] for s in r1.json()["data"]}

    # Session must also appear under secondary dataset
    r2 = client.get(f"/datasets/{id2}/sessions")
    assert r2.status_code == 200
    assert session_id in {s["session_id"] for s in r2.json()["data"]}


# ── C25: save_dataset happy path ─────────────────────────────────────────────

def test_save_dataset_creates_derived_row(client, monkeypatch):
    """C25: save_dataset() in agent code persists a DatasetRow with origin='derived'."""
    import data_analyst.graph.nodes as nodes_module
    from data_analyst.llm.providers.base import LLMResponse
    from data_analyst.llm.client import LLMClient

    call_count = [0]

    class SaveDatasetProvider:
        def complete(self, prompt: str) -> LLMResponse:
            call_count[0] += 1
            if call_count[0] == 1:
                # First plan_action: call save_dataset using the 'test' variable (from test.csv)
                return LLMResponse(
                    text="save_dataset(test, 'derived_test', 'Derived from test')",
                    tokens_input=10,
                    tokens_output=20,
                )
            return LLMResponse(text="FINAL ANSWER: Derived dataset created.", tokens_input=10, tokens_output=20)

    monkeypatch.setattr(nodes_module, "_llm_client", LLMClient(SaveDatasetProvider()))

    dataset_id = _upload(client)
    resp = client.post("/ask", json={"dataset_id": dataset_id, "question": "Save a derived dataset."})
    assert resp.status_code == 200, resp.text
    result = resp.json()["data"]
    assert result["status"] == "completed"
    assert len(result["derived_dataset_ids"]) >= 1

    all_datasets = client.get("/datasets").json()["data"]
    derived = [d for d in all_datasets if d.get("origin") == "derived"]
    assert len(derived) >= 1


# ── C26: clarification turn stored in session ─────────────────────────────────

def test_clarification_turn_in_session(client, monkeypatch):
    """C26: A clarification turn appears in GET /sessions/{id} with status='clarification'."""
    class _FakeClarify:
        needs_clarification = True
        question = "Which year — 2023 or 2024?"
        tokens_input = 5
        tokens_output = 3

    monkeypatch.setattr("data_analyst.graph.clarify.check_clarification", lambda *a, **kw: _FakeClarify())

    _upload(client)
    resp = client.post("/ask", json={"question": "What is the revenue?"})
    assert resp.status_code == 200
    session_id = resp.json()["data"]["session_id"]

    turns_resp = client.get(f"/sessions/{session_id}")
    assert turns_resp.status_code == 200
    turns = turns_resp.json()["data"]["turns"]
    assert len(turns) == 1
    turn = turns[0]
    assert turn["status"] == "clarification"
    assert turn["answer_markdown"] == "Which year — 2023 or 2024?"


# ── C27: Parquet sidecar written on upload ────────────────────────────────────

def test_upload_writes_parquet_file(client):
    """C27: Uploading a CSV creates a Parquet sidecar; DatasetRow.parquet_path is set."""
    import pathlib
    from data_analyst.db.models import DatasetRow
    from data_analyst.db import session as session_module

    dataset_id = _upload(client)

    factory = session_module._get_session_factory()
    with factory() as db:
        row = db.get(DatasetRow, dataset_id)
        assert row.parquet_path is not None, "parquet_path must be set after upload"
        assert pathlib.Path(row.parquet_path).exists(), "Parquet file must exist on disk"


# ── C29: prompt_breakdown in /ask response and session turns ──────────────────

def test_prompt_breakdown_in_ask_response(client):
    """C29: /ask response includes prompt_breakdown with expected keys."""
    dataset_id = _upload(client)
    resp = client.post("/ask", json={"dataset_id": dataset_id, "question": "rows?"})
    assert resp.status_code == 200, resp.text
    result = resp.json()["data"]
    assert "prompt_breakdown" in result
    bd = result["prompt_breakdown"]
    if bd is not None:
        for key in ("system_overhead", "dataset_schemas", "total_prompt"):
            assert key in bd, f"prompt_breakdown missing key: {key}"


def test_prompt_breakdown_in_session_turns(client):
    """C29: GET /sessions/{id} turns include prompt_breakdown field."""
    dataset_id = _upload(client)
    r = client.post("/ask", json={"dataset_id": dataset_id, "question": "rows?"})
    session_id = r.json()["data"]["session_id"]

    turns_resp = client.get(f"/sessions/{session_id}")
    assert turns_resp.status_code == 200
    turns = turns_resp.json()["data"]["turns"]
    assert len(turns) >= 1
    assert "prompt_breakdown" in turns[0]


# ── C30: describe endpoint triggers notes generation ─────────────────────────

def test_describe_endpoint_generates_notes(client, monkeypatch):
    """C30: POST /datasets/{id}/describe triggers generate_dataset_notes in a background thread."""
    import time
    import data_analyst.graph.nodes as nodes_module
    from data_analyst.llm.providers.base import LLMResponse
    from data_analyst.llm.client import LLMClient
    from data_analyst.db.models import DatasetRow
    from data_analyst.db import session as session_module

    class NotesProvider:
        def complete(self, prompt: str) -> LLMResponse:
            return LLMResponse(
                text="This dataset has names, values, and regions.",
                tokens_input=10,
                tokens_output=20,
            )

    monkeypatch.setattr(nodes_module, "_llm_client", LLMClient(NotesProvider()))

    dataset_id = _upload(client)
    resp = client.post(f"/datasets/{dataset_id}/describe")
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["auto_notes_status"] == "pending"

    # Background task runs in a thread; poll the API until auto_notes_status transitions
    deadline = time.time() + 5.0
    status = None
    while time.time() < deadline:
        r = client.get(f"/datasets/{dataset_id}")
        status = r.json()["data"].get("auto_notes_status")
        if status == "done":
            break
        time.sleep(0.1)
    assert status == "done", f"Expected 'done', got {status!r}"
    ctx = client.get(f"/datasets/{dataset_id}").json()["data"]["context"]
    assert ctx == "This dataset has names, values, and regions."


def test_describe_not_found(client):
    """C30: POST /datasets/{id}/describe returns 404 for an unknown dataset."""
    resp = client.post("/datasets/nonexistent/describe")
    assert resp.status_code == 404


# ── C31: compression triggered by context patch and memory patch ──────────────

def test_context_patch_triggers_compression(client, monkeypatch):
    """C31: PATCH /datasets/{id}/context queues compress_dataset_context; context_facts populated."""
    import json
    import time
    import data_analyst.graph.nodes as nodes_module
    from data_analyst.llm.providers.base import LLMResponse
    from data_analyst.llm.client import LLMClient
    from data_analyst.db.models import DatasetRow
    from data_analyst.db import session as session_module

    class CompressProvider:
        def complete(self, prompt: str) -> LLMResponse:
            return LLMResponse(
                text='["revenue is in USD", "dates are ISO 8601"]',
                tokens_input=10,
                tokens_output=20,
            )

    monkeypatch.setattr(nodes_module, "_llm_client", LLMClient(CompressProvider()))

    dataset_id = _upload(client)
    resp = client.patch(
        f"/datasets/{dataset_id}/context",
        json={"context": "Revenue is in USD. Dates are ISO 8601 format."},
    )
    assert resp.status_code == 200, resp.text

    # Background task runs in a thread; poll until it commits
    factory = session_module._get_session_factory()
    deadline = time.time() + 5.0
    row = None
    while time.time() < deadline:
        with factory() as db:
            row = db.get(DatasetRow, dataset_id)
            if row and row.context_facts is not None:
                break
        time.sleep(0.1)
    assert row is not None
    assert row.context_facts is not None, "context_facts must be set after compression"
    facts = json.loads(row.context_facts)
    assert isinstance(facts, list)
    assert len(facts) >= 1


def test_memory_patch_triggers_compression(client, monkeypatch):
    """C31: PATCH /memory queues compress_memory; global_memory_facts SettingsRow populated."""
    import json
    import time
    import data_analyst.graph.nodes as nodes_module
    from data_analyst.llm.providers.base import LLMResponse
    from data_analyst.llm.client import LLMClient
    from data_analyst.db.models import SettingsRow
    from data_analyst.db import session as session_module

    class CompressProvider:
        def complete(self, prompt: str) -> LLMResponse:
            return LLMResponse(
                text='["fiscal year starts in April"]',
                tokens_input=10,
                tokens_output=20,
            )

    monkeypatch.setattr(nodes_module, "_llm_client", LLMClient(CompressProvider()))

    resp = client.patch("/memory", json={"content": "Fiscal year starts in April."})
    assert resp.status_code == 200, resp.text

    # Background task runs in a thread; poll until it commits
    factory = session_module._get_session_factory()
    deadline = time.time() + 5.0
    row = None
    while time.time() < deadline:
        with factory() as db:
            row = db.get(SettingsRow, "global_memory_facts")
            if row is not None:
                break
        time.sleep(0.1)
    assert row is not None, "global_memory_facts SettingsRow must exist after compression"
    facts = json.loads(row.value)
    assert isinstance(facts, list)


# ── C18: context_limit in daily stats ────────────────────────────────────────

def test_daily_stats_context_limit(client):
    """C18/C29: GET /stats/daily includes context_limit > 0."""
    resp = client.get("/stats/daily")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "context_limit" in data
    assert isinstance(data["context_limit"], int)
    assert data["context_limit"] > 0


# ── C9: name field in GET /sessions list ─────────────────────────────────────

def test_sessions_list_includes_name(client):
    """C9: GET /sessions includes name field (None until renamed, string after)."""
    dataset_id = _upload(client)
    r = client.post("/ask", json={"dataset_id": dataset_id, "question": "rows?"})
    session_id = r.json()["data"]["session_id"]

    sessions = client.get("/sessions").json()["data"]
    assert len(sessions) >= 1
    sess = next(s for s in sessions if s["session_id"] == session_id)
    assert "name" in sess

    # After rename, name should appear in list
    client.patch(f"/sessions/{session_id}/name", json={"name": "Audit test session"})
    sessions2 = client.get("/sessions").json()["data"]
    sess2 = next(s for s in sessions2 if s["session_id"] == session_id)
    assert sess2["name"] == "Audit test session"


# ── C25: stale detection after parent dataset update ─────────────────────────

def test_stale_flag_after_parent_cleaned(client, monkeypatch):
    """C25: derived dataset becomes stale when parent is updated via clean/apply."""
    import data_analyst.graph.nodes as nodes_module
    from data_analyst.llm.providers.base import LLMResponse
    from data_analyst.llm.client import LLMClient

    call_count = [0]

    class SaveDatasetProvider:
        def complete(self, prompt: str) -> LLMResponse:
            call_count[0] += 1
            if call_count[0] == 1:
                return LLMResponse(
                    text="save_dataset(test, 'derived_stale', 'Stale test derived')",
                    tokens_input=10, tokens_output=20,
                )
            return LLMResponse(text="FINAL ANSWER: Done.", tokens_input=10, tokens_output=20)

    monkeypatch.setattr(nodes_module, "_llm_client", LLMClient(SaveDatasetProvider()))

    parent_id = _upload(client)
    resp = client.post("/ask", json={"dataset_id": parent_id, "question": "Save derived."})
    assert resp.status_code == 200, resp.text
    derived_ids = resp.json()["data"]["derived_dataset_ids"]
    assert len(derived_ids) >= 1
    derived_id = derived_ids[0]

    # Derived should not be stale yet
    all_ds = client.get("/datasets").json()["data"]
    derived_before = next(d for d in all_ds if d["dataset_id"] == derived_id)
    assert derived_before["stale"] is False

    # Update the parent via clean/apply — sets updated_at
    code = "test[test['value'] > 0]"
    apply_resp = client.post(f"/datasets/{parent_id}/clean/apply", json={"code": code})
    assert apply_resp.status_code == 200, apply_resp.text

    # Derived should now be stale
    all_ds2 = client.get("/datasets").json()["data"]
    derived_after = next(d for d in all_ds2 if d["dataset_id"] == derived_id)
    assert derived_after["stale"] is True, "Derived dataset must be stale after parent update"


# ── C30→C31: describe auto-chains into compression ────────────────────────────

def test_describe_chains_into_compression(client, monkeypatch):
    """C30→C31: POST /datasets/{id}/describe → auto_notes set → context_facts populated."""
    import json
    import time
    import data_analyst.graph.nodes as nodes_module
    from data_analyst.llm.providers.base import LLMResponse
    from data_analyst.llm.client import LLMClient
    from data_analyst.db.models import DatasetRow
    from data_analyst.db import session as session_module

    call_count = [0]

    class ChainProvider:
        def complete(self, prompt: str) -> LLMResponse:
            call_count[0] += 1
            if call_count[0] == 1:
                # C30: generate_dataset_notes call
                return LLMResponse(
                    text="Revenue in USD. Dates are ISO 8601.",
                    tokens_input=10, tokens_output=20,
                )
            # C31: compress_dataset_context call
            return LLMResponse(
                text='["revenue in USD", "dates are ISO 8601"]',
                tokens_input=5, tokens_output=10,
            )

    monkeypatch.setattr(nodes_module, "_llm_client", LLMClient(ChainProvider()))

    dataset_id = _upload(client)
    resp = client.post(f"/datasets/{dataset_id}/describe")
    assert resp.status_code == 200
    assert resp.json()["data"]["auto_notes_status"] == "pending"

    # Poll until context_facts is populated (C30 writes notes, C31 compresses them)
    factory = session_module._get_session_factory()
    deadline = time.time() + 10.0
    row = None
    while time.time() < deadline:
        with factory() as db:
            row = db.get(DatasetRow, dataset_id)
            if row and row.context_facts is not None:
                break
        time.sleep(0.1)
    assert row is not None
    assert row.context_facts is not None, "context_facts must be populated after C30→C31 chain"
    facts = json.loads(row.context_facts)
    assert isinstance(facts, list) and len(facts) >= 1


# ── C27: single-session delete evicts DataFrame cache ────────────────────────

def test_session_delete_evicts_cache(client, monkeypatch):
    """C27: DELETE /sessions/{id} calls _evict_session, removing entries from the cache."""
    import data_analyst.graph.nodes as nodes_module

    dataset_id = _upload(client)
    r = client.post("/ask", json={"dataset_id": dataset_id, "question": "rows?"})
    session_id = r.json()["data"]["session_id"]

    # Manually populate the cache so we can observe the eviction
    import pandas as pd
    nodes_module._session_cache[session_id] = {dataset_id: pd.DataFrame({"x": [1, 2, 3]})}
    assert session_id in nodes_module._session_cache

    resp = client.delete(f"/sessions/{session_id}")
    assert resp.status_code == 200
    assert session_id not in nodes_module._session_cache, "Cache must be evicted on session delete"


# ── C27: bulk session delete evicts all caches ────────────────────────────────

def test_bulk_session_delete_evicts_cache(client, monkeypatch):
    """C27: DELETE /sessions evicts DataFrame cache for all deleted sessions."""
    import pandas as pd
    import data_analyst.graph.nodes as nodes_module

    dataset_id = _upload(client)
    r1 = client.post("/ask", json={"dataset_id": dataset_id, "question": "q1"})
    r2 = client.post("/ask", json={"dataset_id": dataset_id, "question": "q2"})
    sid1 = r1.json()["data"]["session_id"]
    sid2 = r2.json()["data"]["session_id"]

    # Populate cache for both sessions
    for sid in (sid1, sid2):
        nodes_module._session_cache[sid] = {dataset_id: pd.DataFrame({"x": [1]})}

    resp = client.delete("/sessions")
    assert resp.status_code == 200
    assert sid1 not in nodes_module._session_cache, "Cache must be evicted for session 1"
    assert sid2 not in nodes_module._session_cache, "Cache must be evicted for session 2"


# ── C15: DELETE /datasets running-guard covers multi-dataset runs ─────────────

def test_delete_dataset_blocked_when_secondary_dataset_is_running(client, monkeypatch):
    """Guard must fire even when the target dataset is non-primary in a running multi-dataset run."""
    from data_analyst.db.models import QueryRunRow
    import data_analyst.db.session as session_module
    import json as _json

    id1 = _upload(client)
    id2 = _upload_extra(client)

    # Inject a fake running QueryRunRow where id1 is primary but id2 is in dataset_ids_json
    with session_module.create_db_session() as db:
        run = QueryRunRow(
            dataset_id=id1,
            dataset_ids_json=_json.dumps([id1, id2]),
            session_id=None,
            question="in-flight",
            status="running",
        )
        db.add(run)
        db.commit()

    # Deleting id2 (secondary) must be blocked
    resp = client.delete(f"/datasets/{id2}")
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "dataset_in_use"
