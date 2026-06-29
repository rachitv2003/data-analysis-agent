'use client'

import { useCallback, useEffect, useState } from 'react'
import { api, type DailyStats, type DatasetSummary } from '@/lib/api'
import type { LastQueryTokens } from '@/components/analyse/AnalyseTab'

/**
 * Token usage widget (C18) — REAL.
 *
 * The "Last query (In / Out)" row is wired to the most recent answer's token
 * counts (passed down from the Conversation card). The provider/mode line
 * reflects GET /health. The daily totals (model, today In/Out/Queries) and the
 * C29 context-budget bar are driven by GET /stats/daily — re-fetched on mount
 * and whenever a new answer lands (the `lastTokens` reference changes).
 *
 * Cost (C18): a client-side pricing table (below) turns token counts into a
 * currency estimate for the last query and for today. When the active model is
 * not in the table — or its price is deliberately left `null` because we are not
 * confident of the real rate — the cost shows "N/A" rather than a wrong number.
 *
 * User-configured pricing (D4): if the user sets price_input_per_million and
 * price_output_per_million in Settings, those values override the hardcoded
 * table. `settingsVersion` bumps whenever the user saves settings, triggering a
 * re-fetch of the user-configured price.
 */

/**
 * Client-side pricing table — USD price PER 1,000,000 TOKENS, split by input
 * (prompt) and output (completion). Keyed by the model id reported in
 * GET /stats/daily (`stats.model`).
 *
 * Gemini rates are Google's published list prices as of June 2026
 * (ai.google.dev/gemini-api/docs/pricing); the tiered Pro models (3.1 Pro,
 * 2.5 Pro) use the standard ≤200k-token tier here. This mirrors the table in
 * SettingsPanel so the sidebar cost estimate works out of the box; the
 * user-configured price from Settings (D4) still takes precedence when set.
 *
 * If a model's real rate is genuinely unknown, map it to `null` so the UI shows
 * "N/A" rather than fabricating a number.
 */
interface ModelPrice {
  /** USD per 1,000,000 input (prompt) tokens. */
  inputPerMillion: number
  /** USD per 1,000,000 output (completion) tokens. */
  outputPerMillion: number
}

const PRICING_USD_PER_MILLION_TOKENS: Record<string, ModelPrice | null> = {
  'gemini-3.5-flash': { inputPerMillion: 1.5, outputPerMillion: 9.0 },
  'gemini-3.1-pro-preview': { inputPerMillion: 2.0, outputPerMillion: 12.0 },
  'gemini-3.1-flash-lite': { inputPerMillion: 0.25, outputPerMillion: 1.5 },
  'gemini-3-pro-preview': { inputPerMillion: 2.0, outputPerMillion: 12.0 },
  'gemini-2.5-pro': { inputPerMillion: 1.25, outputPerMillion: 10.0 },
  'gemini-2.5-flash': { inputPerMillion: 0.3, outputPerMillion: 2.5 },
  'gemini-2.5-flash-lite': { inputPerMillion: 0.1, outputPerMillion: 0.4 },
  'gemini-2.0-flash': { inputPerMillion: 0.1, outputPerMillion: 0.4 },
  'gemini-2.0-flash-lite': { inputPerMillion: 0.075, outputPerMillion: 0.3 },
  'claude-opus-4-8': { inputPerMillion: 15.0, outputPerMillion: 75.0 },
  'claude-sonnet-4-6': { inputPerMillion: 3.0, outputPerMillion: 15.0 },
  'claude-haiku-4-5-20251001': { inputPerMillion: 0.8, outputPerMillion: 4.0 },
}

/** Resolve a model's price; `undefined` (not in table) and `null` both → no price. */
function priceFor(model: string | undefined): ModelPrice | null {
  if (!model) return null
  return PRICING_USD_PER_MILLION_TOKENS[model] ?? null
}

/** Compute a USD cost from token counts, or null when the model has no price. */
function costUsd(
  price: ModelPrice | null,
  inputTokens: number,
  outputTokens: number,
): number | null {
  if (!price) return null
  return (
    (inputTokens * price.inputPerMillion) / 1_000_000 +
    (outputTokens * price.outputPerMillion) / 1_000_000
  )
}

/** Format a USD cost; "N/A" when the price is unknown. */
function formatCost(value: number | null): string {
  if (value === null) return 'N/A'
  // Show enough precision for tiny per-query costs without trailing noise.
  return `$${value.toFixed(value < 0.01 ? 5 : 4)}`
}

export function TokenWidget({
  provider,
  lastTokens,
  settingsVersion = 0,
  datasetsVersion = 0,
}: {
  provider?: string
  lastTokens: LastQueryTokens | null
  settingsVersion?: number
  /** Bump token from AnalyseTab; re-fetches the dataset aggregate on change. */
  datasetsVersion?: number
}) {
  const [stats, setStats] = useState<DailyStats | null>(null)
  const [statsError, setStatsError] = useState<string | null>(null)
  const [userPrice, setUserPrice] = useState<ModelPrice | null>(null)
  // Dataset aggregate (count + summed rows) for the sidebar line.
  const [datasets, setDatasets] = useState<DatasetSummary[] | null>(null)

  const loadStats = useCallback(async () => {
    setStatsError(null)
    try {
      setStats(await api.dailyStats())
    } catch (err) {
      setStatsError(err instanceof Error ? err.message : 'Failed to load usage stats.')
    }
  }, [])

  // Load on mount, on a new answer (lastTokens), AND when settings are saved
  // (settingsVersion) — so the model badge + context bar reflect a model change.
  useEffect(() => {
    void loadStats()
  }, [loadStats, lastTokens, settingsVersion])

  // Dataset aggregate (Datasets / Rows): load on mount and re-fetch whenever the
  // dataset universe changes (upload/delete bump `datasetsVersion`). Best-effort:
  // a failure simply leaves the aggregate hidden rather than blocking the widget.
  useEffect(() => {
    let cancelled = false
    api
      .listDatasets()
      .then(rows => {
        if (!cancelled) setDatasets(rows)
      })
      .catch(() => {
        if (!cancelled) setDatasets(null)
      })
    return () => {
      cancelled = true
    }
  }, [datasetsVersion])

  // Fetch user-configured pricing from Settings; re-fetch when settings change.
  useEffect(() => {
    api.getSettings().then(s => {
      const inp = parseFloat(s.price_input_per_million ?? '')
      const out = parseFloat(s.price_output_per_million ?? '')
      if (!isNaN(inp) && !isNaN(out)) {
        setUserPrice({ inputPerMillion: inp, outputPerMillion: out })
      } else {
        setUserPrice(null)
      }
    }).catch(() => {})
  }, [settingsVersion])

  const mode =
    provider === 'stub' ? 'Stub (offline)' : provider ? provider : '—'

  // Prominent active-model badge (uppercase). Graceful "—" when unknown.
  const modelBadge = stats?.model ? stats.model.toUpperCase() : '—'

  // Dataset aggregate: count of datasets + summed row_count across all of them.
  const datasetCount = datasets?.length ?? 0
  const totalRows = datasets
    ? datasets.reduce((sum, d) => sum + (d.row_count ?? 0), 0)
    : 0

  const lastQuery = lastTokens ? `${lastTokens.input} / ${lastTokens.output}` : '— / —'

  const today = stats
    ? `${stats.tokens_input} / ${stats.tokens_output} / ${stats.query_count}`
    : '— / — / —'

  // Context-window bar: the LAST query's prompt size vs the per-request window.
  // (The previous "today total vs window" was meaningless — cumulative daily
  // usage has nothing to do with the per-request context capacity.) Prefer the
  // live last-query input; fall back to the most recent persisted run on reload.
  const lastPromptTokens = lastTokens?.input ?? stats?.last_prompt_tokens ?? 0
  const budgetPct =
    stats && stats.context_limit > 0
      ? Math.min(100, (lastPromptTokens / stats.context_limit) * 100)
      : 0

  // Cost (C18): user-configured price takes precedence; fall back to hardcoded table.
  const price = userPrice ?? priceFor(stats?.model)
  const lastQueryCost = lastTokens
    ? formatCost(costUsd(price, lastTokens.input, lastTokens.output))
    : '—'
  const todayCost = stats
    ? formatCost(costUsd(price, stats.tokens_input, stats.tokens_output))
    : '—'

  return (
    <section
      aria-labelledby="tokens-heading"
      className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm"
    >
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 id="tokens-heading" className="text-sm font-semibold text-gray-800">
          Token usage
        </h2>
      </div>

      {/* Prominent active-model badge (uppercase). */}
      <div className="mb-3">
        <span
          aria-label="Active model"
          className="inline-block rounded bg-gray-100 px-2 py-1 text-xs font-bold uppercase tracking-wide text-gray-700"
        >
          {modelBadge}
        </span>
      </div>

      <dl className="space-y-1.5 text-xs">
        <Row label="Provider / mode" value={mode} live />
        <Row label="Last query (In / Out)" value={lastQuery} live={!!lastTokens} />
        <Row label="Today (In / Out / Queries)" value={today} live={!!stats} />
      </dl>

      {/* C29 context-window bar — last query's prompt size vs the model window. */}
      {stats && stats.context_limit > 0 && (
        <div className="mt-3">
          <div className="mb-1 flex items-center justify-between text-[11px] text-gray-500">
            <span>Context window (last query)</span>
            <span className="tabular-nums">
              {lastPromptTokens.toLocaleString()} / {stats.context_limit.toLocaleString()}
            </span>
          </div>
          <div
            role="progressbar"
            aria-label="Last query prompt size vs context window"
            aria-valuemin={0}
            aria-valuemax={stats.context_limit}
            aria-valuenow={lastPromptTokens}
            className="h-1.5 w-full overflow-hidden rounded-full bg-gray-100"
          >
            <div
              className={`h-full transition-all ${
                budgetPct > 90 ? 'bg-red-500' : budgetPct > 70 ? 'bg-amber-500' : 'bg-green-500'
              }`}
              style={{ width: `${budgetPct}%` }}
            />
          </div>
        </div>
      )}

      {statsError && (
        <p role="alert" className="mt-2 text-[11px] text-red-600">
          {statsError}
        </p>
      )}

      {/* Dataset aggregate: total datasets + summed rows across the universe. */}
      <div className="mt-3 rounded-md border border-gray-200 bg-gray-50/60 px-2.5 py-1.5 text-[11px] text-gray-600">
        <span className="font-medium text-gray-700">Datasets:</span>{' '}
        <span className="tabular-nums">{datasetCount}</span>
        <span aria-hidden="true" className="mx-1.5 text-gray-300">
          ·
        </span>
        <span className="font-medium text-gray-700">Rows:</span>{' '}
        <span className="tabular-nums">{totalRows.toLocaleString()}</span>
      </div>

      {/* Cost (C18) — user price > hardcoded table; "N/A" when price is unknown. */}
      <div className="mt-3 border-t border-gray-100 pt-3">
        <div className="mb-1.5 flex items-center justify-between gap-2">
          <span className="text-[11px] font-medium text-gray-500">Cost estimate</span>
        </div>
        <dl className="space-y-1.5 text-xs">
          <Row label="Last query cost" value={lastQueryCost} live={!!lastTokens} />
          <Row label="Today cost" value={todayCost} live={!!stats} />
        </dl>
        {price === null && stats && !userPrice && (
          <p className="mt-1 text-[11px] text-gray-400">
            Configure pricing in Settings to see cost estimates.
          </p>
        )}
      </div>
    </section>
  )
}

function Row({
  label,
  value,
  live = false,
}: {
  label: string
  value: string
  live?: boolean
}) {
  return (
    <div className="flex items-center justify-between gap-3">
      <dt className="text-gray-500">{label}</dt>
      <dd className={`font-medium tabular-nums ${live ? 'text-gray-800' : 'text-gray-400'}`}>
        {value}
      </dd>
    </div>
  )
}
