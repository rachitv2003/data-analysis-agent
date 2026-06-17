# Capability: Expanded Result Display Limits

**Status:** draft

## Purpose

When a pandas expression returns a DataFrame or Series, the agent currently shows pandas' default truncation (≈5 rows, ≈4 columns). This makes results unreadable for real datasets. This capability raises the limits to 100 rows and 20 columns so that meaningful tabular data is surfaced in the answer.

## Inputs

No new inputs — this is a behavioural change to `execute_action`.

## Outputs

Same structure as before, but `result` strings in `action_history` may be larger (up to 100 rows × 20 columns of data).

## Behavior

1. **Per-eval display options.** Before evaluating a pandas expression in `execute_action`, temporarily set:
   ```python
   pd.set_option("display.max_rows", 100)
   pd.set_option("display.max_columns", 20)
   pd.set_option("display.width", None)       # disable line-wrapping
   pd.set_option("display.max_colwidth", 100)  # prevent cell truncation
   ```
   Restore defaults after the eval (use a `try/finally` block).

2. **DataFrame → Markdown table.** If the result of an expression is a `pd.DataFrame`, convert it to a Markdown table via a helper:
   ```python
   def _result_to_str(result) -> str:
       if isinstance(result, pd.DataFrame):
           return result.head(100).to_markdown(index=True)
       if isinstance(result, pd.Series):
           return result.head(100).to_markdown()
       return str(result)
   ```
   This makes tabular output render as a proper HTML table when the LLM includes it in a FINAL ANSWER.

3. **Token budget awareness.** If a DataFrame has more than 100 rows, the first 100 are shown with a note appended: `"... (showing 100 of N rows)"`. Column limit: first 20 columns; if truncated, append `"... (showing 20 of N columns)"`.

4. **Dependency.** `tabulate` is required for `DataFrame.to_markdown()`. Add `tabulate>=0.9` to `pyproject.toml`.

## Failure modes

| Condition | Behavior |
|---|---|
| `to_markdown()` fails | Fall back to `df.head(100).to_string()` |
| Result is not a DataFrame/Series | `str(result)` unchanged |

## Data model changes

None.

## API changes

None.

## UI changes

None — the Markdown table rendered server-side by `markdown-it-py` will be styled by the existing `.answer-body table` CSS.

## Acceptance criteria

- [ ] A query returning a 50-row DataFrame shows all 50 rows in the answer
- [ ] A query returning a 200-row DataFrame shows exactly 100 rows with a trailing count note
- [ ] A query returning a DataFrame with 25 columns shows 20 columns with a trailing note
- [ ] The result is formatted as a Markdown table in `action_history`
- [ ] Integration test: expression `df.head(10)` returns a result string containing `|` (Markdown table pipe)
