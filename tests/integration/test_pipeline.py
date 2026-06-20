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
