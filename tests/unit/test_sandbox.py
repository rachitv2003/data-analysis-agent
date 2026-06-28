"""Unit tests for graph.sandbox helpers — no LLM key required."""

import pandas as pd
import pytest

from graph.sandbox import _stringify


# --- D4: column cap in _stringify -------------------------------------------


def test_stringify_wide_dataframe_caps_at_20_columns():
    """D4/C8: DataFrames with >20 columns are capped at 20 columns in _stringify output."""
    # Build a DataFrame with 30 columns (col_0 .. col_29), 5 rows.
    df = pd.DataFrame(
        {f"col_{i}": range(5) for i in range(30)}
    )
    result = _stringify(df)

    # Must contain the cap note.
    assert "[showing 20 of 30 columns]" in result, (
        f"Expected cap note not found in output:\n{result}"
    )

    # col_20 through col_29 must NOT appear in the output (they were sliced off).
    for col_idx in range(20, 30):
        assert f"col_{col_idx}" not in result, (
            f"Column col_{col_idx} should have been removed by the 20-col cap "
            f"but was found in output:\n{result}"
        )

    # col_0 through col_19 must still appear.
    for col_idx in range(20):
        assert f"col_{col_idx}" in result, (
            f"Column col_{col_idx} missing from capped output:\n{result}"
        )


def test_stringify_narrow_dataframe_no_col_cap():
    """D4/C8: DataFrames with <=20 columns are NOT truncated and carry no cap note."""
    df = pd.DataFrame({f"col_{i}": [1, 2] for i in range(20)})
    result = _stringify(df)

    assert "showing 20 of" not in result
    assert "columns]" not in result
    # All 20 columns present.
    for col_idx in range(20):
        assert f"col_{col_idx}" in result


def test_stringify_wide_dataframe_row_cap_still_applied():
    """D4/C8: row cap (100) and col cap (20) both apply when the frame exceeds both limits.

    When both caps fire, columns outside the first 20 must not appear and the
    col-cap note must be present.  The char cap may truncate the suffix in the
    extreme case (150 rows x 20 cols), so we verify column exclusion directly
    rather than relying on the suffix surviving.
    """
    df = pd.DataFrame(
        {f"col_{i}": range(150) for i in range(30)}
    )
    result = _stringify(df)

    # Columns beyond the cap must not appear.
    for col_idx in range(20, 30):
        assert f"col_{col_idx}" not in result, (
            f"col_{col_idx} should have been removed by the 20-col cap"
        )
    # col_0 .. col_19 must appear (they survive the cap).
    for col_idx in range(20):
        assert f"col_{col_idx}" in result


def test_stringify_char_cap_still_applied():
    """D4/C8: the 6000-char safety cap is still applied after row + col capping."""
    # 20 columns with long values to force the char cap.
    df = pd.DataFrame(
        {f"col_{i}": ["a" * 500 for _ in range(10)] for i in range(20)}
    )
    result = _stringify(df)
    assert len(result) <= 6100  # allow a small overshoot for the truncation marker


def test_stringify_series_unaffected_by_col_cap():
    """D4: a pd.Series is not subject to the column cap (it has no columns axis)."""
    s = pd.Series(range(5), name="values")
    result = _stringify(s)
    # No column cap note for a Series.
    assert "columns]" not in result
    # All values are present in the output.
    for v in range(5):
        assert str(v) in result
