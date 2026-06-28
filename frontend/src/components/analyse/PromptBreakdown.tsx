'use client'

import { useState } from 'react'

/**
 * PromptBreakdown (C29) — collapsible per-component prompt token breakdown.
 *
 * Shows `total_prompt` as a summary at the top, then each named component as a
 * labelled row with a proportional bar. Collapsed by default; a chevron toggles
 * it open. Consistent with the StepsInspector collapsible pattern.
 *
 * Only rendered when the parent passes a non-empty breakdown object containing
 * at least one key.
 */

/**
 * Human-readable label overrides for known breakdown keys (spec/capabilities/
 * context-window-display.md). Unknown keys fall back to a title-cased version
 * of the snake_case key name.
 */
const LABEL_MAP: Record<string, string> = {
  total_prompt: 'Total prompt',
  dataset_schemas: 'Dataset schemas',
  conversation_history: 'Conversation history',
  memory: 'Memory',
  action_history: 'Action history',
  system_overhead: 'System overhead',
}

function humanLabel(key: string): string {
  return (
    LABEL_MAP[key] ??
    key
      .replace(/_/g, ' ')
      .replace(/\b\w/g, c => c.toUpperCase())
  )
}

/** Bar colour: use blue-400 for regular components; a slightly darker shade for total. */
function barClass(isTotal: boolean): string {
  return isTotal ? 'bg-blue-500' : 'bg-blue-300'
}

export function PromptBreakdown({ breakdown }: { breakdown: Record<string, number> }) {
  const [open, setOpen] = useState(false)

  // Extract total_prompt for proportional bars. Fall back to summing all other
  // keys if total_prompt is absent so bars are still meaningful.
  const total = breakdown.total_prompt ?? Object.values(breakdown).reduce((a, b) => a + b, 0)

  // Separate total_prompt from the component rows; sort components by value desc.
  const componentEntries = Object.entries(breakdown)
    .filter(([k]) => k !== 'total_prompt')
    .sort(([, a], [, b]) => b - a)

  const hasComponents = componentEntries.length > 0

  return (
    <div className="mt-3">
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        aria-expanded={open}
        className="inline-flex items-center gap-1.5 text-xs font-medium text-gray-600 hover:text-gray-900"
      >
        <span aria-hidden="true">{open ? '▾' : '▸'}</span>
        Prompt breakdown
        {total > 0 && (
          <span className="rounded-full bg-gray-100 px-1.5 py-0.5 text-[10px] font-semibold text-gray-500 tabular-nums">
            {total.toLocaleString()} tok
          </span>
        )}
      </button>

      {open && (
        <div className="mt-2 rounded-md border border-gray-200 bg-gray-50 p-3">
          {/* Summary row: total_prompt */}
          <div className="mb-2 flex items-center justify-between gap-2 text-[11px]">
            <span className="font-semibold text-gray-700">
              {humanLabel('total_prompt')}
            </span>
            <span className="font-semibold tabular-nums text-gray-700">
              {total.toLocaleString()}
            </span>
          </div>

          {/* Bar for the total */}
          {total > 0 && (
            <div
              role="presentation"
              className="mb-3 h-1.5 w-full overflow-hidden rounded-full bg-gray-200"
            >
              <div className={`h-full w-full ${barClass(true)}`} />
            </div>
          )}

          {/* Per-component rows */}
          {hasComponents && (
            <ol className="space-y-2" aria-label="Prompt components">
              {componentEntries.map(([key, value]) => {
                const pct =
                  total > 0 ? Math.min(100, Math.round((value / total) * 100)) : 0
                return (
                  <li key={key}>
                    <div className="mb-0.5 flex items-center justify-between gap-2 text-[11px]">
                      <span className="text-gray-600">{humanLabel(key)}</span>
                      <span className="tabular-nums text-gray-600">
                        {value.toLocaleString()}
                        {total > 0 && (
                          <span className="ml-1 text-gray-400">({pct}%)</span>
                        )}
                      </span>
                    </div>
                    <div
                      role="progressbar"
                      aria-label={`${humanLabel(key)} token share`}
                      aria-valuenow={pct}
                      aria-valuemin={0}
                      aria-valuemax={100}
                      className="h-1 w-full overflow-hidden rounded-full bg-gray-200"
                    >
                      <div
                        className={`h-full transition-all ${barClass(false)}`}
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                  </li>
                )
              })}
            </ol>
          )}

          {!hasComponents && total === 0 && (
            <p className="text-[11px] text-gray-400">No breakdown data available.</p>
          )}
        </div>
      )}
    </div>
  )
}
