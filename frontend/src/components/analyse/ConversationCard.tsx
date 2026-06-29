'use client'

import {
  useCallback,
  useEffect,
  useId,
  useImperativeHandle,
  useRef,
  useState,
  type Ref,
} from 'react'
import { api, type AskResponse, type AskStep, type SessionDetail, type TurnView } from '@/lib/api'
import type { LastQueryTokens } from '@/components/analyse/AnalyseTab'
import { Markdown } from '@/components/analyse/Markdown'
import { ChartRender } from '@/components/analyse/ChartRender'
import { StepsInspector } from '@/components/analyse/StepsInspector'
import { PromptBreakdown } from '@/components/analyse/PromptBreakdown'
import { ProgressBar } from '@/components/analyse/ProgressBar'
import { SuggestionChips } from '@/components/analyse/SuggestionChips'
import { ClarificationTurn } from '@/components/analyse/ClarificationTurn'

/**
 * Conversation card — REAL multi-turn in Phase 3.
 *
 * Renders the whole session as a list of TURNS (not just the latest):
 *  - On Ask → POST /ask. The body's datasets come from the explicit selection
 *    (if any) or are omitted so the server's C19 selector picks. The returned
 *    `session_id` is adopted (via `onSessionStarted`) so follow-ups continue the
 *    same session.
 *  - A `type:"clarification"` response (C26) renders an AMBER clarification turn
 *    with a re-submit that re-sends the SAME question with `skip_clarification`.
 *  - The latest answer's `suggested_questions` render as chips that submit the
 *    next question in the same session.
 *  - Older turns COLLAPSE to question + a one-line answer preview (C32); the
 *    latest is expanded. Each turn keeps its Steps inspector + token counts.
 *  - While an ask runs, GET /runs/current is polled ~1/s to drive a progress bar.
 *
 * The parent (AnalyseTab) hydrates the thread on resume via the imperative
 * `hydrate` / `reset` handle, and owns the session id.
 */

export interface ConversationHandle {
  /** Replace the thread with a resumed session's turns. */
  hydrate: (detail: SessionDetail) => void
  /** Clear the thread for a new session. */
  reset: () => void
}

type TurnStatus = 'pending' | 'answer' | 'clarification' | 'error'

interface Turn {
  id: string
  question: string
  status: TurnStatus
  answer?: AskResponse
  clarificationQuestion?: string
  /** The original question to re-send on a clarification "answer anyway". */
  originalQuestion?: string
  error?: string
  /**
   * Epoch ms the answer landed, for the "Asked at {time}" meta line. Live
   * answers capture `Date.now()` when the response arrives; hydrated history
   * turns reuse the persisted `created_at` when present, else stay undefined
   * (the "Asked at" prefix is then omitted gracefully).
   */
  answeredAt?: number
  /** Response time in ms (server wall-clock live; row lifespan for history). */
  durationMs?: number
}

let turnSeq = 0
function nextTurnId(): string {
  turnSeq += 1
  return `t${turnSeq}-${Date.now()}`
}

/**
 * C32 collapse state persists per browser session via sessionStorage, keyed by
 * turn id. We only persist explicit user overrides (the default — latest turn
 * expanded, older collapsed — is recomputed on render), so a resumed thread
 * restores exactly the expand/collapse choices the user last made this session.
 */
const COLLAPSE_STORAGE_KEY = 'conversation:collapse-overrides'

function loadCollapseOverrides(): Record<string, boolean> {
  if (typeof window === 'undefined') return {}
  try {
    const raw = window.sessionStorage.getItem(COLLAPSE_STORAGE_KEY)
    if (!raw) return {}
    const parsed: unknown = JSON.parse(raw)
    if (parsed && typeof parsed === 'object') {
      return parsed as Record<string, boolean>
    }
  } catch {
    /* corrupt/unavailable storage — fall back to defaults */
  }
  return {}
}

function saveCollapseOverrides(overrides: Record<string, boolean>): void {
  if (typeof window === 'undefined') return
  try {
    window.sessionStorage.setItem(COLLAPSE_STORAGE_KEY, JSON.stringify(overrides))
  } catch {
    /* storage unavailable / quota — collapse simply won't persist */
  }
}

/** Map a persisted TurnView (from GET /sessions/{id}) into a thread Turn. */
function turnFromView(v: TurnView): Turn {
  if (v.type === 'clarification' && v.clarification_question) {
    return {
      id: v.run_id || nextTurnId(),
      question: v.question,
      status: 'clarification',
      clarificationQuestion: v.clarification_question,
      originalQuestion: v.question,
    }
  }
  // Treat any non-clarification persisted turn as a resolved answer.
  const answer: AskResponse = {
    type: 'answer',
    run_id: v.run_id,
    session_id: undefined,
    dataset_ids: v.dataset_ids,
    datasets_used: v.datasets_used,
    selector_reasoning: v.selector_reasoning,
    answer_markdown: v.answer_markdown ?? '',
    answer_html: v.answer_html ?? undefined,
    iteration_count: v.iteration_count,
    tokens_input: v.tokens_input,
    tokens_output: v.tokens_output,
    status: v.status,
    is_best_effort: v.is_best_effort,
    steps: v.steps ?? [],
    suggested_questions: v.suggested_questions ?? [],
    prompt_breakdown: v.prompt_breakdown,
    charts: v.charts ?? [],
  }
  // Persisted turns may carry a `created_at` ISO string; use it for the
  // "Asked at" meta when present (parse to epoch ms), else leave it undefined so
  // the prefix is omitted gracefully for history that predates per-turn times.
  const createdMs = v.created_at ? Date.parse(v.created_at) : NaN
  return {
    id: v.run_id || nextTurnId(),
    question: v.question,
    status: 'answer',
    answer,
    answeredAt: Number.isNaN(createdMs) ? undefined : createdMs,
    durationMs: typeof v.duration_ms === 'number' ? v.duration_ms : undefined,
  }
}

export function ConversationCard({
  handleRef,
  selectedDatasetIds,
  sessionId,
  onSessionStarted,
  onAnswered,
  model,
}: {
  handleRef?: Ref<ConversationHandle>
  /** Explicit dataset selection; empty → let the server's selector pick. */
  selectedDatasetIds: string[]
  sessionId: string | null
  onSessionStarted: (id: string) => void
  onAnswered: (tokens: LastQueryTokens) => void
  /** The active model id (for the session-export header). */
  model?: string
}) {
  const [question, setQuestion] = useState('')
  const composerRef = useRef<HTMLTextAreaElement>(null)
  const [turns, setTurns] = useState<Turn[]>([])
  const [running, setRunning] = useState(false)
  const [progress, setProgress] = useState<{ iteration: number; max: number } | null>(null)
  const [progressLabel, setProgressLabel] = useState('Thinking…')
  // Turn ids the user has manually expanded/collapsed (overrides the default).
  // Restored from sessionStorage on mount so collapse choices survive this
  // browser session (C32).
  const [expandedOverride, setExpandedOverride] = useState<Record<string, boolean>>(() =>
    loadCollapseOverrides(),
  )
  const questionId = useId()

  // Persist collapse overrides to sessionStorage whenever they change (C32).
  useEffect(() => {
    saveCollapseOverrides(expandedOverride)
  }, [expandedOverride])
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const canAsk = question.trim().length > 0 && !running

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
    setProgress(null)
  }, [])

  useEffect(() => stopPolling, [stopPolling])

  useImperativeHandle(
    handleRef,
    () => ({
      hydrate: (detail: SessionDetail) => {
        const next = (detail.turns ?? []).map(turnFromView)
        setTurns(next)
        // Restore the user's per-turn collapse choices for this session (C32).
        setExpandedOverride(loadCollapseOverrides())
      },
      reset: () => {
        setTurns([])
        setExpandedOverride({})
      },
    }),
    [],
  )

  const updateTurn = useCallback((id: string, patch: Partial<Turn>) => {
    setTurns(prev => prev.map(t => (t.id === id ? { ...t, ...patch } : t)))
  }, [])

  /**
   * Run one ask. `q` is the question; `skipClarification` is set when the user
   * re-submits after a clarification turn. The returned session_id is adopted so
   * the next ask continues this session.
   */
  const runAsk = useCallback(
    async (q: string, skipClarification: boolean) => {
      const turnId = nextTurnId()
      setTurns(prev => [...prev, { id: turnId, question: q, status: 'pending' }])
      setRunning(true)
      setProgressLabel(skipClarification ? 'Thinking…' : 'Checking…')

      pollRef.current = setInterval(() => {
        api
          .currentRun()
          .then(run => {
            if (run && run.status === 'running') {
              setProgressLabel('Thinking…')
              setProgress({ iteration: run.iteration_count, max: run.max_iterations })
            }
          })
          .catch(() => {
            /* polling is best-effort */
          })
      }, 1000)

      try {
        const res = await api.ask({
          question: q,
          datasetIds: selectedDatasetIds.length > 0 ? selectedDatasetIds : undefined,
          sessionId,
          skipClarification,
        })

        if (res.session_id) onSessionStarted(res.session_id)

        if (res.type === 'clarification' && res.clarification_question) {
          updateTurn(turnId, {
            status: 'clarification',
            clarificationQuestion: res.clarification_question,
            originalQuestion: q,
          })
        } else {
          // Capture the moment the answer landed for the "Asked at" meta (the
          // /ask payload carries no timestamp) plus the server-measured response
          // time. Not read at module top-level.
          updateTurn(turnId, {
            status: 'answer',
            answer: res,
            answeredAt: Date.now(),
            durationMs: typeof res.duration_ms === 'number' ? res.duration_ms : undefined,
          })
          onAnswered({ input: res.tokens_input ?? 0, output: res.tokens_output ?? 0 })
        }
      } catch (err) {
        updateTurn(turnId, {
          status: 'error',
          error: err instanceof Error ? err.message : 'The question failed to run.',
        })
      } finally {
        stopPolling()
        setRunning(false)
      }
    },
    [selectedDatasetIds, sessionId, onSessionStarted, onAnswered, updateTurn, stopPolling],
  )

  const submit = useCallback(() => {
    const q = question.trim()
    if (!q || running) return
    setQuestion('')
    void runAsk(q, false)
  }, [question, running, runAsk])

  // Re-submit a clarification's original question without touching the composer.
  const submitQuestion = useCallback(
    (q: string, skipClarification = false) => {
      if (running) return
      void runAsk(q.trim(), skipClarification)
    },
    [running, runAsk],
  )

  // Picking a follow-up suggestion only DROPS it into the composer (it does not
  // auto-submit) so the user can edit or confirm before sending. Focus + caret
  // to end so they can tweak it or just press Enter.
  const pickSuggestion = useCallback((q: string) => {
    setQuestion(q)
    requestAnimationFrame(() => {
      const el = composerRef.current
      if (el) {
        el.focus()
        el.setSelectionRange(el.value.length, el.value.length)
      }
    })
  }, [])

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault()
        if (canAsk) submit()
      }
    },
    [canAsk, submit],
  )

  const setExpanded = useCallback((id: string, value: boolean) => {
    setExpandedOverride(prev => ({ ...prev, [id]: value }))
  }, [])

  // C32 bulk controls: set an explicit override for every collapsible (answer)
  // turn so the whole thread expands/collapses at once. Pending/error/
  // clarification turns aren't collapsible and are left untouched.
  const setAllExpanded = useCallback(
    (value: boolean) => {
      setExpandedOverride(prev => {
        const next = { ...prev }
        for (const t of turns) {
          if (t.status === 'answer') next[t.id] = value
        }
        return next
      })
    },
    [turns],
  )

  const collapsibleCount = turns.filter(t => t.status === 'answer').length

  // Export the WHOLE session as a Markdown log (prompts, clarifications, times,
  // answers, tokens, steps/backend code, prompt breakdown).
  const exportSession = useCallback(() => {
    if (turns.length === 0) return
    const md = buildSessionMarkdown(turns, model)
    const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')
    downloadFile(md, `session-${stamp}.md`)
  }, [turns, model])

  const lastTurn = turns[turns.length - 1]
  const latestSuggestions =
    lastTurn?.status === 'answer' ? (lastTurn.answer?.suggested_questions ?? []) : []

  return (
    <section
      aria-labelledby="conversation-heading"
      className="flex min-h-[24rem] flex-col rounded-lg border border-gray-200 bg-white p-4 shadow-sm"
    >
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 id="conversation-heading" className="text-sm font-semibold text-gray-800">
          Conversation
        </h2>
        <div className="flex items-center gap-2">
          {/* Session-level export — a full Markdown log of the conversation. */}
          {turns.length > 0 && (
            <button
              type="button"
              onClick={exportSession}
              title="Download the whole conversation as a Markdown log"
              className="rounded border border-gray-200 bg-white px-2 py-0.5 text-[11px] font-medium text-gray-500 hover:bg-gray-50 hover:text-gray-700"
            >
              Export session
            </button>
          )}
          {/* C32 bulk collapse/expand controls. */}
          {collapsibleCount > 1 && (
            <div className="flex items-center gap-1.5 text-[11px]">
              <button
                type="button"
                onClick={() => setAllExpanded(false)}
                aria-label="Collapse all answers"
                className="rounded px-1.5 py-0.5 font-medium text-gray-500 hover:bg-gray-100 hover:text-gray-700"
              >
                Collapse all
              </button>
              <span aria-hidden="true" className="text-gray-300">
                ·
              </span>
              <button
                type="button"
                onClick={() => setAllExpanded(true)}
                aria-label="Expand all answers"
                className="rounded px-1.5 py-0.5 font-medium text-gray-500 hover:bg-gray-100 hover:text-gray-700"
              >
                Expand all
              </button>
            </div>
          )}
          {sessionId && (
            <span className="text-[11px] text-gray-400">Session active</span>
          )}
        </div>
      </div>

      {/* Thread */}
      <div
        role="log"
        aria-live="polite"
        aria-label="Conversation thread"
        className="flex flex-1 flex-col gap-3 overflow-y-auto rounded-md border border-gray-100 bg-gray-50/50 p-3"
      >
        {turns.length === 0 ? (
          <div className="flex flex-1 items-center justify-center text-center">
            <p className="text-xs text-gray-400">
              Ask a question about your data to get started. You can pick datasets
              above or let the agent choose.
            </p>
          </div>
        ) : (
          turns.map((turn, i) => {
            const isLast = i === turns.length - 1
            const override = expandedOverride[turn.id]
            const expanded = override ?? isLast
            return (
              <TurnView
                key={turn.id}
                turn={turn}
                expanded={expanded}
                collapsible={turn.status === 'answer'}
                onToggle={() => setExpanded(turn.id, !expanded)}
                onResubmitClarification={() =>
                  turn.originalQuestion && submitQuestion(turn.originalQuestion, true)
                }
                busy={running}
              />
            )
          })
        )}
      </div>

      {/* Live progress row */}
      {running && (
        <ProgressBar
          label={progressLabel}
          iteration={progress?.iteration ?? null}
          max={progress?.max ?? null}
        />
      )}

      {/* Suggestion chips from the latest answer — click drops into the composer */}
      <SuggestionChips
        suggestions={latestSuggestions}
        onPick={pickSuggestion}
        disabled={running}
      />

      {/* Composer */}
      <div className="mt-3">
        <label htmlFor={questionId} className="sr-only">
          Ask a question about your data
        </label>
        <textarea
          id={questionId}
          ref={composerRef}
          rows={3}
          value={question}
          onChange={e => setQuestion(e.target.value)}
          onKeyDown={onKeyDown}
          disabled={running}
          placeholder="Ask a question about your data…"
          className="w-full resize-none rounded-md border border-gray-200 p-3 text-sm text-gray-800 placeholder:text-gray-400 focus:border-blue-400 focus:outline-none focus:ring-1 focus:ring-blue-400 disabled:bg-gray-50 disabled:text-gray-400"
        />
        <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
          <span className="text-xs text-gray-400">
            Enter to send · Shift+Enter for a new line
            {selectedDatasetIds.length === 0
              ? ' · the agent will pick datasets'
              : ` · ${selectedDatasetIds.length} dataset${selectedDatasetIds.length === 1 ? '' : 's'} selected`}
          </span>
          <button
            type="button"
            onClick={submit}
            disabled={!canAsk}
            title={question.trim().length === 0 ? 'Type a question first' : undefined}
            className="inline-flex items-center gap-2 rounded-md bg-blue-600 px-5 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-gray-300 disabled:text-gray-500"
          >
            {running && <Spinner />}
            {running ? 'Asking…' : 'Ask'}
          </button>
        </div>
      </div>
    </section>
  )
}

/** A single conversation turn: question, then pending/answer/clarification/error. */
function TurnView({
  turn,
  expanded,
  collapsible,
  onToggle,
  onResubmitClarification,
  busy,
}: {
  turn: Turn
  expanded: boolean
  collapsible: boolean
  onToggle: () => void
  onResubmitClarification: () => void
  busy: boolean
}) {
  return (
    <div className="space-y-2">
      {/* Question (blue) */}
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-lg rounded-br-sm bg-blue-600 px-3 py-2 text-sm text-white">
          {turn.question}
        </div>
      </div>

      {/* Body */}
      <div className="rounded-lg rounded-bl-sm border border-gray-200 bg-white px-3 py-2">
        {turn.status === 'pending' ? (
          <p className="inline-flex items-center gap-2 text-xs text-gray-500">
            <Spinner /> The agent is working on it…
          </p>
        ) : turn.status === 'error' ? (
          <p role="alert" className="text-sm text-red-600">
            {turn.error}
          </p>
        ) : turn.status === 'clarification' && turn.clarificationQuestion ? (
          <ClarificationTurn
            clarificationQuestion={turn.clarificationQuestion}
            onResubmit={onResubmitClarification}
            busy={busy}
          />
        ) : turn.answer ? (
          collapsible && !expanded ? (
            <CollapsedAnswer answer={turn.answer} onExpand={onToggle} />
          ) : (
            <AnswerView
              answer={turn.answer}
              collapsible={collapsible}
              onCollapse={collapsible ? onToggle : undefined}
              answeredAt={turn.answeredAt}
              durationMs={turn.durationMs}
            />
          )
        ) : null}
      </div>
    </div>
  )
}

/** Collapsed view (C32): a one-line answer preview with an expand affordance. */
function CollapsedAnswer({ answer, onExpand }: { answer: AskResponse; onExpand: () => void }) {
  const preview = previewText(answer.answer_markdown ?? '')
  return (
    <button
      type="button"
      onClick={onExpand}
      aria-expanded="false"
      className="flex w-full items-center gap-2 text-left"
    >
      <span aria-hidden="true" className="text-gray-400">
        ▸
      </span>
      <span className="min-w-0 flex-1 truncate text-xs text-gray-500">
        {preview || '(answer)'}
      </span>
      <span className="shrink-0 text-[10px] text-blue-600">expand</span>
    </button>
  )
}

function AnswerView({
  answer,
  collapsible,
  onCollapse,
  answeredAt,
  durationMs,
}: {
  answer: AskResponse
  collapsible: boolean
  onCollapse?: () => void
  /** Epoch ms the answer landed (live) or its persisted time (history). */
  answeredAt?: number
  /** Response time in ms, shown next to the timestamp when known. */
  durationMs?: number
}) {
  const markdown = answer.answer_markdown ?? ''
  const steps: AskStep[] = answer.steps ?? []
  // Local clock time for the "Asked at" prefix; omitted when no timestamp.
  const askedAt =
    answeredAt !== undefined ? new Date(answeredAt).toLocaleTimeString() : null
  const stepCount = steps.length
  return (
    <div>
      <div className="mb-2 flex items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          {answer.is_best_effort && (
            <span className="rounded bg-amber-100 px-2 py-0.5 text-[11px] font-semibold text-amber-800">
              Best effort
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {collapsible && onCollapse && (
            <button
              type="button"
              onClick={onCollapse}
              aria-expanded="true"
              className="shrink-0 text-[11px] font-medium text-gray-400 hover:text-gray-700"
            >
              collapse
            </button>
          )}
        </div>
      </div>

      <Markdown>{markdown}</Markdown>

      {/* Inline Plotly charts captured during analysis (C4). */}
      {answer.charts && answer.charts.length > 0 && (
        <ChartRender charts={answer.charts} />
      )}

      {/* Datasets used disclosure */}
      {answer.datasets_used && answer.datasets_used.length > 0 && (
        <p className="mt-2 text-[11px] text-gray-400">
          Datasets used: {answer.datasets_used.join(', ')}
        </p>
      )}

      {/* Meta: asked-at time + iterations + token counts */}
      <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-gray-100 pt-2 text-[11px] text-gray-500">
        {askedAt && (
          <>
            <span>Asked at {askedAt}</span>
            <span aria-hidden="true">·</span>
          </>
        )}
        {durationMs !== undefined && (
          <>
            <span className="tabular-nums">took {formatDuration(durationMs)}</span>
            <span aria-hidden="true">·</span>
          </>
        )}
        <span className="tabular-nums">
          {answer.iteration_count ?? 0} iteration
          {(answer.iteration_count ?? 0) === 1 ? '' : 's'}
        </span>
        <span aria-hidden="true">·</span>
        <span className="tabular-nums">
          Tokens: {answer.tokens_input ?? 0} in / {answer.tokens_output ?? 0} out
        </span>
        {answer.status && (
          <>
            <span aria-hidden="true">·</span>
            <span>status: {answer.status}</span>
          </>
        )}
      </div>

      <StepsInspector steps={steps} />

      {/* Provenance: how many pandas execution steps backed this answer (C4).
          Zero steps ⇒ the agent answered from schema/metadata without running
          code; ≥1 ⇒ that many code steps ran. */}
      <p className="mt-2 text-[11px] text-gray-400">
        {stepCount === 0
          ? '0 code steps — answered from schema'
          : `${stepCount} code step${stepCount === 1 ? '' : 's'}`}
      </p>

      {answer.prompt_breakdown && Object.keys(answer.prompt_breakdown).length > 0 && (
        <PromptBreakdown
          breakdown={
            Object.fromEntries(
              Object.entries(answer.prompt_breakdown).map(([k, v]) => [
                k,
                typeof v === 'number' ? v : Number(v) || 0,
              ]),
            )
          }
        />
      )}
    </div>
  )
}

/** Human-readable response time: "850ms", "3.4s", or "1m 5s". */
function formatDuration(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`
  const m = Math.floor(ms / 60_000)
  const s = Math.round((ms % 60_000) / 1000)
  return `${m}m ${s}s`
}

/** Trigger a client-side download of `content` as a file. */
function downloadFile(content: string, filename: string) {
  const blob = new Blob([content], { type: 'text/markdown' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

/**
 * Build a full Markdown log of the whole session — every prompt, any
 * clarification, submit time + response time, the answer, tokens, status,
 * iterations, datasets used, the backend code steps (action + output), and the
 * prompt breakdown — so the user can keep a complete record of the conversation.
 */
function buildSessionMarkdown(turns: Turn[], model?: string): string {
  const answered = turns.filter(t => t.status === 'answer')
  const totalIn = answered.reduce((s, t) => s + (t.answer?.tokens_input ?? 0), 0)
  const totalOut = answered.reduce((s, t) => s + (t.answer?.tokens_output ?? 0), 0)
  const lines: string[] = []

  lines.push('# Conversation session', '')
  lines.push(`- **Exported:** ${new Date().toLocaleString()}`)
  lines.push(`- **Model:** ${model || 'unknown'}`)
  lines.push(`- **Turns:** ${turns.length}`)
  lines.push(`- **Total tokens:** ${totalIn} in / ${totalOut} out`)
  lines.push('', '---', '')

  turns.forEach((turn, i) => {
    const a = turn.answer
    lines.push(`## Turn ${i + 1} — ${turn.status}`, '')
    lines.push('**Prompt**', '', `> ${turn.question.replace(/\n/g, '\n> ')}`, '')

    if (turn.status === 'clarification' && turn.clarificationQuestion) {
      lines.push('**Clarification requested**', '', `> ${turn.clarificationQuestion}`, '')
      lines.push('_Re-submitted with clarification skipped._', '')
    } else if (turn.status === 'error') {
      lines.push(`**Error:** ${turn.error ?? 'The question failed to run.'}`, '')
    } else if (a) {
      const meta: string[] = []
      if (turn.answeredAt !== undefined) meta.push(`Asked at ${new Date(turn.answeredAt).toLocaleTimeString()}`)
      if (turn.durationMs !== undefined) meta.push(`Response time ${formatDuration(turn.durationMs)}`)
      meta.push(`Status ${a.status ?? 'completed'}`)
      meta.push(`Iterations ${a.iteration_count ?? 0}`)
      meta.push(`Tokens ${a.tokens_input ?? 0} in / ${a.tokens_output ?? 0} out`)
      lines.push(`- ${meta.join('  ·  ')}`)
      if (a.datasets_used && a.datasets_used.length > 0) {
        lines.push(`- Datasets used: ${a.datasets_used.join(', ')}`)
      }
      lines.push('', '**Answer**', '', a.answer_markdown ?? '', '')

      const steps = a.steps ?? []
      if (steps.length > 0) {
        lines.push(`**Backend code (${steps.length} step${steps.length === 1 ? '' : 's'})**`, '')
        steps.forEach((step, si) => {
          lines.push(`Step ${si + 1} — ${step.is_error ? 'error' : 'ok'}`, '')
          lines.push('```python', step.action ?? '', '```', '')
          const out = (step.result ?? '').slice(0, 1500)
          if (out.trim()) lines.push('Output:', '', '```', out, '```', '')
        })
      }

      if (a.prompt_breakdown && Object.keys(a.prompt_breakdown).length > 0) {
        const parts = Object.entries(a.prompt_breakdown).map(([k, v]) => `${k}: ${v}`)
        lines.push(`**Prompt breakdown (tokens):** ${parts.join(', ')}`, '')
      }
    }
    lines.push('---', '')
  })

  return lines.join('\n')
}

/** First line / sentence of the answer, trimmed for a collapsed preview. */
function previewText(markdown: string): string {
  const flat = markdown
    .replace(/[#*`>_~|-]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
  return flat.length > 120 ? `${flat.slice(0, 120)}…` : flat
}

function Spinner() {
  return (
    <span
      aria-hidden="true"
      className="inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent"
    />
  )
}
