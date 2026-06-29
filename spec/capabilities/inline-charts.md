# Capability: Inline Plotly Charts (C4)

## What It Does
Captures any Plotly figure the agent creates during analysis and returns it as JSON for inline rendering.

## Inputs
| Input | Type | Source | Required |
|-------|------|--------|----------|
| Plotly figure(s) | px/go object | execute_action sandbox | no |

## Outputs
| Output | Type | Destination |
|--------|------|-------------|
| charts | list[JSON] | `AgentState.charts` → answer |

## External Calls
None (figures rendered client-side).

## Business Rules
- `execute_action` detects Plotly figures (directly returned or reachable in the eval namespace) and serializes them to JSON.
- As a FALLBACK, the sandbox also converts any OPEN matplotlib figures via `plotly.tools.mpl_to_plotly`, so a model that used `.plot()`/`plt.*` instead of Plotly still yields a chart (the `plan_action` prompt still steers models to Plotly).
- `finalize` carries the captured chart JSON on the answer's `charts`; the UI renders them inline (Plotly.js).
- A chart-capture failure is non-fatal (step error, run continues).

## Success Criteria
- [ ] A question that asks for a distribution/plot yields at least one Plotly figure JSON in the response.
- [ ] The UI renders the chart inline within the answer turn.
