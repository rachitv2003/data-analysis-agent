"""Real-LLM integration test for multi-dataset cross-join Q&A (C14).

Requires an LLM key in `.env` (`AGENT_GEMINI_API_KEY`, `AGENT_ANTHROPIC_API_KEY`,
or `AGENT_OPENROUTER_API_KEY`). Auto-skips when none is present.

Uploads two small, self-contained CSVs — a products table and a sales table
sharing a `product_id` key — then asks a question that requires joining both.
Asserts the response has `status == "completed"`, a non-empty answer, and at
least one iteration. Cleans up both datasets after the test.
"""

import io

import pytest


# ---------------------------------------------------------------------------
# Inline CSV fixtures (no external files needed)
# ---------------------------------------------------------------------------

_PRODUCTS_CSV = """\
product_id,product_name,category
P001,Widget Alpha,Electronics
P002,Gadget Beta,Electronics
P003,Doohickey Gamma,Home
"""

_SALES_CSV = """\
product_id,units_sold,revenue
P001,120,2400.00
P002,45,900.00
P003,200,1000.00
P001,80,1600.00
P002,30,600.00
"""


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

@pytest.mark.usefixtures("_require_llm_key")
def test_multi_dataset_cross_join_answer(api_client):
    """Ask a join question across two datasets; assert a real completed answer."""

    # --- upload products dataset ---
    products_file = {"file": ("products.csv", io.BytesIO(_PRODUCTS_CSV.encode()), "text/csv")}
    up1 = api_client.post("/upload", files=products_file)
    assert up1.status_code == 200, up1.text
    products_id = up1.json()["data"]["dataset_id"]

    # --- upload sales dataset ---
    sales_file = {"file": ("sales.csv", io.BytesIO(_SALES_CSV.encode()), "text/csv")}
    up2 = api_client.post("/upload", files=sales_file)
    assert up2.status_code == 200, up2.text
    sales_id = up2.json()["data"]["dataset_id"]

    try:
        # --- ask a question requiring both datasets ---
        r = api_client.post(
            "/ask",
            json={
                "dataset_ids": [products_id, sales_id],
                "question": "Which product had the highest total sales revenue?",
                "skip_clarification": True,
            },
        )
        assert r.status_code == 200, r.text
        data = r.json()["data"]

        assert data["status"] == "completed", (
            f"Expected status 'completed', got {data['status']!r}. "
            f"Answer: {data.get('answer_markdown', '')!r}"
        )

        answer = data.get("answer_markdown") or data.get("answer", "")
        assert answer and answer.strip(), "answer must be non-empty real prose"

        assert data["iteration_count"] >= 1, (
            f"Expected at least 1 iteration, got {data['iteration_count']}"
        )

    finally:
        # --- cleanup: delete both datasets ---
        api_client.delete(f"/datasets/{products_id}")
        api_client.delete(f"/datasets/{sales_id}")
