'use client'

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import type { DatasetSummary } from '@/lib/api'

/**
 * Schema / ER-diagram panel — REAL (Phase 4).
 *
 * Renders each dataset as a table card in an SVG canvas and draws inferred
 * foreign-key edges between them. `_erFkLinks(datasets)` is the SINGLE SOURCE OF
 * TRUTH for both the edges drawn here AND the PK/FK badges in the table
 * description panel (which imports it), so the diagram and the badges never
 * disagree.
 *
 * Interactions: Fit / + / − zoom controls (0.5×–3×), drag-pan, wheel-zoom, click
 * a card to select that dataset (lifted to DatabaseTab), and hover a card to
 * highlight its relationships while dimming the rest.
 */

// ---------------------------------------------------------------------------
// Dataset-with-columns helpers
// ---------------------------------------------------------------------------

/** A dataset carrying its column metadata for the diagram. */
export interface ErDataset extends DatasetSummary {
  /** Column names — `GET /datasets` may carry these directly. */
  columns?: string[]
  /** Or a {name,dtype} schema (GET /datasets/{id}). */
  columns_schema?: { name: string; dtype: string }[]
}

interface ColInfo {
  name: string
  dtype: string
}

/** Extract column {name,dtype} from whatever column shape a dataset carries. */
function datasetColumns(ds: ErDataset): ColInfo[] {
  if (Array.isArray(ds.columns_schema) && ds.columns_schema.length > 0) {
    return ds.columns_schema.map(c => ({ name: String(c.name), dtype: String(c.dtype) }))
  }
  if (Array.isArray(ds.columns)) {
    return ds.columns.map(name => ({ name: String(name), dtype: 'text' }))
  }
  // Some payloads carry `columns_json` (raw {name:dtype} or string[]).
  const raw = (ds as Record<string, unknown>).columns_json
  if (Array.isArray(raw)) {
    return raw.map(name => ({ name: String(name), dtype: 'text' }))
  }
  if (raw && typeof raw === 'object') {
    return Object.entries(raw as Record<string, unknown>).map(([name, dt]) => ({
      name,
      dtype: String(dt),
    }))
  }
  return []
}

// ---------------------------------------------------------------------------
// FK inference — _erFkLinks (the single source of truth)
// ---------------------------------------------------------------------------

/** Generic columns that are too common to be a meaningful join key. */
const GENERIC_DENYLIST = new Set([
  'id',
  'name',
  'date',
  'value',
  'count',
  'type',
  'status',
  'description',
  'title',
  'created_at',
  'updated_at',
  'timestamp',
  'index',
])

/** The `zip_code_prefix` family normalises to a single geolocation hub. */
const ZIP_FAMILY = new Set([
  'zip_code_prefix',
  'geolocation_zip_code_prefix',
  'customer_zip_code_prefix',
  'seller_zip_code_prefix',
])

export interface ErLink {
  /** The PK ("one") side dataset id. */
  fromId: string
  /** The FK ("many") side dataset id. */
  toId: string
  /** The join column (normalised name). */
  column: string
  /** Whether either endpoint is a derived dataset (drawn dashed green). */
  derived: boolean
}

/** A per-dataset key summary derived from `_erFkLinks`, for the badge panel. */
export interface ErKeys {
  /** Columns where this dataset is the canonical PK ("one") table. */
  pk: string[]
  /** Columns where this dataset references another (the "many"/FK side). */
  fk: { column: string; references: string }[]
}

function norm(col: string): string {
  return col.trim().toLowerCase()
}

/** Choose the canonical PK table for a shared column by filename heuristics. */
function pickPkTable(
  column: string,
  candidates: { id: string; filename: string }[],
): string {
  if (candidates.length === 0) return ''
  // A column like `customer_id` points at the table whose stem matches the
  // column's entity (`customer` / `customers`). Prefer the shortest matching
  // filename (the dimension table), else the shortest filename overall.
  const entity = column.replace(/_id$/, '')
  const singular = entity.replace(/s$/, '')
  const stem = (fn: string) =>
    fn.replace(/\.[^.]+$/, '').toLowerCase().replace(/[^a-z0-9]/g, '')

  const matches = candidates.filter(c => {
    const s = stem(c.filename)
    return s === entity || s === `${entity}s` || s === singular || s === `${singular}s`
  })
  const pool = matches.length > 0 ? matches : candidates
  return [...pool].sort((a, b) => a.filename.length - b.filename.length)[0].id
}

/**
 * Infer foreign-key links across datasets. The SINGLE SOURCE OF TRUTH for both
 * the diagram edges and the description panel's PK/FK badges.
 *
 * Rules (spec/ui.md):
 *  - columns ending `_id` shared by ≥2 tables → an FK link (PK table chosen by
 *    filename; the other tables reference it);
 *  - the `zip_code_prefix` family normalises to a geolocation hub;
 *  - other exact-name shared specific columns link (denylist of generic columns
 *    excluded);
 *  - generic columns (`id`, `name`, `date`, `value`, `count`, `type`, `status`,…)
 *    never link.
 */
export function _erFkLinks(datasets: ErDataset[]): ErLink[] {
  const tables = datasets.map(ds => ({
    id: ds.id,
    filename: ds.filename,
    derived: ds.origin === 'derived',
    cols: new Set(datasetColumns(ds).map(c => norm(c.name))),
  }))

  // Map a (normalised) join column → the tables that contain it.
  const colToTables = new Map<string, { id: string; filename: string; derived: boolean }[]>()

  for (const t of tables) {
    for (const raw of t.cols) {
      // Normalise the zip family to one logical column.
      const col = ZIP_FAMILY.has(raw) ? 'zip_code_prefix' : raw
      const isId = col.endsWith('_id')
      const isZip = col === 'zip_code_prefix'
      const isSpecific = !GENERIC_DENYLIST.has(col)
      // Only `_id` columns, the zip hub, or specific (non-generic) shared names
      // are eligible to be join keys.
      if (!isId && !isZip && !isSpecific) continue
      if (GENERIC_DENYLIST.has(col)) continue
      const arr = colToTables.get(col) ?? []
      arr.push({ id: t.id, filename: t.filename, derived: t.derived })
      colToTables.set(col, arr)
    }
  }

  const links: ErLink[] = []
  const seen = new Set<string>()

  for (const [col, members] of colToTables) {
    if (members.length < 2) continue // shared by ≥2 tables only

    const pkId =
      col === 'zip_code_prefix'
        ? // The geolocation hub: prefer a table whose name mentions geolocation.
          (members.find(m => /geo/i.test(m.filename)) ?? members[0]).id
        : pickPkTable(col, members)

    for (const m of members) {
      if (m.id === pkId) continue
      const key = `${pkId}->${m.id}:${col}`
      if (seen.has(key)) continue
      seen.add(key)
      const pkMember = members.find(x => x.id === pkId)
      links.push({
        fromId: pkId,
        toId: m.id,
        column: col,
        derived: m.derived || (pkMember?.derived ?? false),
      })
    }
  }

  return links
}

/** Per-dataset PK/FK key summary for the description panel, from `_erFkLinks`. */
export function _erKeysFor(datasetId: string, datasets: ErDataset[]): ErKeys {
  const links = _erFkLinks(datasets)
  const byId = new Map(datasets.map(d => [d.id, d.filename] as const))
  const pk = new Set<string>()
  const fk: { column: string; references: string }[] = []
  for (const l of links) {
    if (l.fromId === datasetId) pk.add(l.column)
    if (l.toId === datasetId) {
      fk.push({ column: l.column, references: byId.get(l.fromId) ?? l.fromId })
    }
  }
  return { pk: [...pk], fk }
}

// ---------------------------------------------------------------------------
// Layout
// ---------------------------------------------------------------------------

const CARD_W = 220
const HEADER_H = 34
const ROW_H = 20
const MAX_ROWS = 8

// Force-directed layout constants (ported from the reference vanilla-JS build).
const REPEL = 9000 // repulsion strength (1/dist² law) — compact for a narrow pane
const SPRING = 0.06 // pull-only spring stiffness
const IDEAL = CARD_W + 100 // edge rest length — only attract beyond this
const ITERS = 200 // simulation iterations
const SEP = 25 // minimum gap enforced by overlap resolution
const STRETCH_MAX = 2.4 // cap on the horizontal-fill stretch factor (fills wide panes)

interface CardLayout {
  ds: ErDataset
  cols: ColInfo[]
  x: number
  y: number
  w: number
  h: number
}

/** Each card's height depends only on its own columns. */
function cardHeight(cols: ColInfo[]): number {
  const shown = Math.min(cols.length, MAX_ROWS)
  const extra = cols.length > MAX_ROWS ? 1 : 0
  return HEADER_H + (shown + extra) * ROW_H + 8
}

/**
 * Force-directed ER layout (ported from the reference build).
 *
 * 1. Deterministic circular init — datasets are sorted by `ds.id` to break
 *    symmetry (NO Math.random / Date.now, so the layout is stable across
 *    renders).
 * 2. Repulsion (1/dist²) keeps distant nodes spread without blowing apart;
 *    pull-only springs along edges attract only past the IDEAL rest length, so
 *    related tables drift together without compressing onto each other.
 * 3. An AABB overlap-resolution pass then guarantees no two cards intersect.
 * 4. Positions are normalised to a (20,20) origin.
 * 5. Horizontal-fill stretch: the Schema pane is wide, so a too-tall layout is
 *    widened (x-positions only — cards keep fixed size, so this can only open
 *    horizontal gaps, never create overlaps) to fill the space. This is THE fix
 *    for "Fit zooms out too far / leaves whitespace".
 *
 * Returns the post-stretch content bounds as {width,height} so the caller's
 * fit() fills the pane.
 */
function layoutCards(
  datasets: ErDataset[],
  view?: { w: number; h: number },
): { cards: CardLayout[]; width: number; height: number } {
  const n = datasets.length
  if (n === 0) return { cards: [], width: 0, height: 0 }

  // The Schema pane is wide; capture its aspect ratio so the layout can fill it.
  const paneAspect =
    view && view.w > 40 && view.h > 40 ? view.w / view.h : 1.7

  // Per-card metadata; each card's height depends only on its own columns.
  const meta = datasets.map(ds => {
    const cols = datasetColumns(ds)
    return { ds, cols, h: cardHeight(cols) }
  })
  const metaById = new Map(meta.map(m => [m.ds.id, m]))
  const heightOf = (id: string) => metaById.get(id)?.h ?? HEADER_H

  // All edges drive the spring simulation. We only need FK links here (derived
  // lineage shares the same endpoints in this build), inferred once.
  const links = _erFkLinks(datasets)
  const edges = links.map(l => ({ a: l.fromId, b: l.toId }))

  // ── 1. Deterministic circular init (sorted by id to break symmetry) ───────
  const pos: Record<string, { x: number; y: number }> = {}
  const sorted = [...datasets].sort((x, y) => x.id.localeCompare(y.id))
  const R = Math.max(160, n * 32)
  sorted.forEach((d, i) => {
    const angle = (2 * Math.PI * i) / n - Math.PI / 2
    pos[d.id] = { x: R + Math.cos(angle) * R, y: R + Math.sin(angle) * R }
  })

  // ── 2. Force simulation ───────────────────────────────────────────────────
  for (let iter = 0; iter < ITERS; iter++) {
    const cool = 1 - iter / ITERS
    const fx: Record<string, number> = {}
    const fy: Record<string, number> = {}
    for (const d of datasets) {
      fx[d.id] = 0
      fy[d.id] = 0
    }
    // Repulsion 1/dist² — keeps distant nodes spread without blowing them apart.
    for (let i = 0; i < datasets.length; i++) {
      for (let j = i + 1; j < datasets.length; j++) {
        const a = datasets[i].id
        const b = datasets[j].id
        const dx = pos[b].x - pos[a].x
        const dy = pos[b].y - pos[a].y
        const dist2 = Math.max(dx * dx + dy * dy, 1)
        const dist = Math.sqrt(dist2)
        const f = REPEL / dist2
        fx[a] -= (f * dx) / dist
        fy[a] -= (f * dy) / dist
        fx[b] += (f * dx) / dist
        fy[b] += (f * dy) / dist
      }
    }
    // Spring — pull-only: attract when farther than IDEAL, never compress closer.
    for (const { a, b } of edges) {
      if (!pos[a] || !pos[b]) continue
      const dx = pos[b].x - pos[a].x
      const dy = pos[b].y - pos[a].y
      const dist = Math.max(Math.sqrt(dx * dx + dy * dy), 1)
      if (dist <= IDEAL) continue
      const f = SPRING * (dist - IDEAL)
      fx[a] += (f * dx) / dist
      fy[a] += (f * dy) / dist
      fx[b] -= (f * dx) / dist
      fy[b] -= (f * dy) / dist
    }
    // Apply with cooling.
    for (const d of datasets) {
      pos[d.id].x += fx[d.id] * cool * 0.5
      pos[d.id].y += fy[d.id] * cool * 0.5
    }
  }

  // ── 3. Overlap resolution: guarantee no two cards intersect ───────────────
  for (let pass = 0; pass < 30; pass++) {
    let moved = false
    for (let i = 0; i < datasets.length; i++) {
      for (let j = i + 1; j < datasets.length; j++) {
        const a = datasets[i].id
        const b = datasets[j].id
        const pa = pos[a]
        const pb = pos[b]
        const ha = heightOf(a)
        const hb = heightOf(b)
        // AABB overlap amounts.
        const ox = Math.min(pa.x + CARD_W + SEP - pb.x, pb.x + CARD_W + SEP - pa.x)
        const oy = Math.min(pa.y + ha + SEP - pb.y, pb.y + hb + SEP - pa.y)
        if (ox <= 0 || oy <= 0) continue
        moved = true
        // Push apart along the axis of smaller penetration.
        if (ox < oy) {
          const dir = pb.x >= pa.x ? 1 : -1
          pa.x -= (dir * ox) / 2
          pb.x += (dir * ox) / 2
        } else {
          const dir = pb.y >= pa.y ? 1 : -1
          pa.y -= (dir * oy) / 2
          pb.y += (dir * oy) / 2
        }
      }
    }
    if (!moved) break
  }

  // ── 4. Normalise to a (20,20) origin ──────────────────────────────────────
  const xs = datasets.map(d => pos[d.id].x)
  const ys = datasets.map(d => pos[d.id].y)
  const minX = Math.min(...xs)
  const minY = Math.min(...ys)
  for (const d of datasets) {
    pos[d.id].x = Math.round(pos[d.id].x - minX + 20)
    pos[d.id].y = Math.round(pos[d.id].y - minY + 20)
  }

  // ── 5. Horizontal fill (THE whitespace fix) ───────────────────────────────
  // Stretching x-positions (cards keep fixed size) only widens horizontal gaps,
  // so it can never introduce overlaps.
  {
    const cW = Math.max(...datasets.map(d => pos[d.id].x + CARD_W))
    const cH = Math.max(...datasets.map(d => pos[d.id].y + heightOf(d.id)))
    const contentAspect = cW / Math.max(cH, 1)
    if (paneAspect > contentAspect * 1.05) {
      const sx = Math.min(paneAspect / contentAspect, STRETCH_MAX)
      for (const d of datasets) {
        pos[d.id].x = Math.round(20 + (pos[d.id].x - 20) * sx)
      }
    }
  }

  const cards: CardLayout[] = meta.map(m => ({
    ds: m.ds,
    cols: m.cols,
    x: pos[m.ds.id].x,
    y: pos[m.ds.id].y,
    w: CARD_W,
    h: m.h,
  }))

  // Post-stretch content bounds (+20 margin) so the caller's fit() fills the pane.
  const width = Math.max(...cards.map(c => c.x + c.w)) + 20
  const height = Math.max(...cards.map(c => c.y + c.h)) + 20
  return { cards, width, height }
}

// ---------------------------------------------------------------------------
// dtype color dot
// ---------------------------------------------------------------------------

function dtypeColor(dtype: string): string {
  const d = dtype.toLowerCase()
  if (/(int|float|number|numeric|double)/.test(d)) return '#3b82f6' // blue — number
  if (/(date|time)/.test(d)) return '#f59e0b' // amber — date
  if (/bool/.test(d)) return '#22c55e' // green — boolean
  if (/(text|object|string|str|category)/.test(d)) return '#a855f7' // purple — text
  return '#9ca3af' // grey — other
}

/** Clamp a zoom factor. The low floor (0.15) lets Fit zoom OUT far enough to
 * fit a large/wide diagram into a narrow pane. */
function clampZoom(z: number): number {
  return Math.min(3, Math.max(0.15, z))
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function ERDiagramPanel({
  datasets,
  selectedId,
  onSelect,
}: {
  datasets: ErDataset[]
  selectedId: string | null
  onSelect: (id: string) => void
}) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [zoom, setZoom] = useState(1)
  const [pan, setPan] = useState({ x: 0, y: 0 })
  const [hoverId, setHoverId] = useState<string | null>(null)
  const dragRef = useRef<{ startX: number; startY: number; panX: number; panY: number } | null>(
    null,
  )
  const [viewSize, setViewSize] = useState({ w: 600, h: 360 })
  // The pan/zoom transform group — we measure its REAL rendered bbox for Fit.
  const gRef = useRef<SVGGElement>(null)

  // Layout depends on the viewport so the horizontal-fill stretch matches the
  // pane's aspect; recomputes on resize and when the dataset set changes.
  const { cards } = useMemo(
    () => layoutCards(datasets, viewSize),
    [datasets, viewSize.w, viewSize.h],
  )
  const links = useMemo(() => _erFkLinks(datasets), [datasets])

  // Per-dataset PK/FK column sets (normalised) for in-card key badges — derived
  // from the same _erFkLinks that drives the edges and the description panel, so
  // the three always agree.
  const keysById = useMemo(() => {
    const map = new Map<string, { pk: Set<string>; fk: Set<string> }>()
    for (const d of datasets) map.set(d.id, { pk: new Set(), fk: new Set() })
    for (const l of links) {
      map.get(l.fromId)?.pk.add(l.column)
      map.get(l.toId)?.fk.add(l.column)
    }
    return map
  }, [datasets, links])

  // Fit reads the ACTUAL rendered geometry (getBBox on the transform group) and
  // the LIVE pane size. Because it measures what's truly on screen, it can never
  // disagree with the render (no React-state staleness) and always centres the
  // real content with no clipping. Stable identity (no deps) so it never fights
  // the user's pan/zoom.
  const fit = useCallback(() => {
    const el = containerRef.current
    const g = gRef.current
    if (!el || !g) return
    let bb: { x: number; y: number; width: number; height: number }
    try {
      bb = g.getBBox()
    } catch {
      return // not laid out yet
    }
    if (bb.width === 0 || bb.height === 0) {
      setZoom(1)
      setPan({ x: 0, y: 0 })
      return
    }
    const PAD = 24
    const vw = el.clientWidth
    const vh = el.clientHeight
    const z = clampZoom(Math.min((vw - PAD * 2) / bb.width, (vh - PAD * 2) / bb.height, 1.6))
    setZoom(z)
    // The `- bb.x*z` / `- bb.y*z` terms map the content's true top-left to the
    // centred margin, so nothing clips regardless of the layout's origin.
    setPan({
      x: (vw - bb.width * z) / 2 - bb.x * z,
      y: (vh - bb.height * z) / 2 - bb.y * z,
    })
  }, [])

  // Measure the container (the layout uses it) and refit on resize.
  useLayoutEffect(() => {
    const el = containerRef.current
    if (!el) return
    const update = () => setViewSize({ w: el.clientWidth, h: el.clientHeight })
    update()
    const ro = new ResizeObserver(() => {
      update()
      requestAnimationFrame(fit)
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [fit])

  // Auto-fit one frame after each relayout, so the new cards are painted before
  // getBBox measures them.
  useEffect(() => {
    const id = requestAnimationFrame(fit)
    return () => cancelAnimationFrame(id)
  }, [cards, fit])

  const zoomBy = useCallback((factor: number) => {
    setZoom(z => clampZoom(z * factor))
  }, [])

  const onWheel = useCallback((e: React.WheelEvent) => {
    e.preventDefault()
    const factor = e.deltaY < 0 ? 1.1 : 1 / 1.1
    setZoom(z => clampZoom(z * factor))
  }, [])

  const onPointerDown = useCallback(
    (e: React.PointerEvent) => {
      // Only start a pan from empty canvas (cards stop propagation for clicks).
      dragRef.current = { startX: e.clientX, startY: e.clientY, panX: pan.x, panY: pan.y }
      ;(e.target as Element).setPointerCapture?.(e.pointerId)
    },
    [pan.x, pan.y],
  )

  const onPointerMove = useCallback((e: React.PointerEvent) => {
    const d = dragRef.current
    if (!d) return
    setPan({ x: d.panX + (e.clientX - d.startX), y: d.panY + (e.clientY - d.startY) })
  }, [])

  const onPointerUp = useCallback((e: React.PointerEvent) => {
    dragRef.current = null
    ;(e.target as Element).releasePointerCapture?.(e.pointerId)
  }, [])

  const cardById = useMemo(() => new Map(cards.map(c => [c.ds.id, c])), [cards])

  // Route every edge once (per layout) — routing calls dodgeX over the full card
  // list, so this is the expensive part; keep it out of the hover path. Hover
  // state (highlight/dim/label) is applied cheaply at render time below.
  const routedEdges = useMemo(() => {
    return links
      .map(l => {
        const a = cardById.get(l.fromId)
        const b = cardById.get(l.toId)
        if (!a || !b) return null
        return { link: l, geom: routeEdge(a, b, l, cards) }
      })
      .filter((e): e is { link: ErLink; geom: EdgeGeometry } => e !== null)
  }, [links, cards, cardById])

  // Which side each PK/FK badge sits on, so a badge lands next to the card edge
  // where its connector actually attaches (instead of always on the right, which
  // looks "disassociated" when the related table is to the left). datasetId →
  // column → side. First edge for a column wins.
  const badgeSideById = useMemo(() => {
    const map = new Map<string, Map<string, 'left' | 'right'>>()
    const set = (id: string, col: string, side: 'left' | 'right') => {
      let m = map.get(id)
      if (!m) {
        m = new Map()
        map.set(id, m)
      }
      if (!m.has(col)) m.set(col, side)
    }
    for (const { link, geom } of routedEdges) {
      set(link.fromId, link.column, geom.pkSide)
      set(link.toId, link.column, geom.fkSide)
    }
    return map
  }, [routedEdges])

  // The set of dataset ids related to the hovered card (for dim/highlight).
  const relatedIds = useMemo(() => {
    if (!hoverId) return null
    const ids = new Set<string>([hoverId])
    for (const l of links) {
      if (l.fromId === hoverId) ids.add(l.toId)
      if (l.toId === hoverId) ids.add(l.fromId)
    }
    return ids
  }, [hoverId, links])

  const isEmpty = datasets.length === 0

  return (
    <section
      aria-labelledby="schema-heading"
      className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm"
    >
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 id="schema-heading" className="text-sm font-semibold text-gray-800">
          Schema
        </h2>
        <div className="flex gap-1.5">
          <button
            type="button"
            onClick={fit}
            disabled={isEmpty}
            aria-label="Fit diagram to view"
            className="rounded-md border border-gray-200 bg-white px-3 py-1 text-xs font-medium text-gray-600 hover:bg-gray-50 disabled:cursor-not-allowed disabled:text-gray-300"
          >
            Fit
          </button>
          <button
            type="button"
            onClick={() => zoomBy(1.2)}
            disabled={isEmpty}
            aria-label="Zoom in"
            className="rounded-md border border-gray-200 bg-white px-3 py-1 text-xs font-medium text-gray-600 hover:bg-gray-50 disabled:cursor-not-allowed disabled:text-gray-300"
          >
            +
          </button>
          <button
            type="button"
            onClick={() => zoomBy(1 / 1.2)}
            disabled={isEmpty}
            aria-label="Zoom out"
            className="rounded-md border border-gray-200 bg-white px-3 py-1 text-xs font-medium text-gray-600 hover:bg-gray-50 disabled:cursor-not-allowed disabled:text-gray-300"
          >
            −
          </button>
        </div>
      </div>

      {isEmpty ? (
        <div className="flex min-h-[18rem] flex-col items-center justify-center rounded-md border-2 border-dashed border-gray-200 bg-gray-50 px-4 py-12 text-center">
          <span aria-hidden="true" className="mb-2 text-3xl text-gray-300">
            ⬚
          </span>
          <p className="text-sm font-medium text-gray-500">No datasets yet</p>
          <p className="mt-1 text-xs text-gray-400">
            Upload a CSV on the Analyse tab to see the schema diagram.
          </p>
        </div>
      ) : (
        <div
          ref={containerRef}
          onWheel={onWheel}
          className="relative h-[34rem] w-full cursor-grab touch-none overflow-hidden rounded-md border border-gray-100 bg-gray-50 active:cursor-grabbing"
        >
          <svg
            width="100%"
            height="100%"
            role="img"
            aria-label="Entity-relationship diagram"
            onPointerDown={onPointerDown}
            onPointerMove={onPointerMove}
            onPointerUp={onPointerUp}
            onPointerLeave={onPointerUp}
          >
            <g ref={gRef} transform={`translate(${pan.x},${pan.y}) scale(${zoom})`}>
              {/* Edges first (under the cards). Hovering either endpoint card —
                  or the edge itself — highlights it (thicker, blue) and reveals
                  its join-column label; unrelated edges dim. */}
              {routedEdges.map(({ link: l, geom }, i) => {
                const related =
                  relatedIds !== null && relatedIds.has(l.fromId) && relatedIds.has(l.toId)
                const highlighted = related
                const dimmed = relatedIds !== null && !related
                return (
                  <g
                    key={`${l.fromId}-${l.toId}-${l.column}-${i}`}
                    onMouseEnter={() => setHoverId(l.fromId)}
                    onMouseLeave={() => setHoverId(null)}
                  >
                    <Edge geom={geom} derived={l.derived} highlighted={highlighted} dimmed={dimmed} />
                  </g>
                )
              })}

              {/* Cards on top. */}
              {cards.map(card => {
                const dimmed = relatedIds !== null && !relatedIds.has(card.ds.id)
                return (
                  <TableCard
                    key={card.ds.id}
                    card={card}
                    keys={keysById.get(card.ds.id)}
                    badgeSides={badgeSideById.get(card.ds.id)}
                    selected={selectedId === card.ds.id}
                    dimmed={dimmed}
                    onSelect={() => onSelect(card.ds.id)}
                    onHover={() => setHoverId(card.ds.id)}
                    onLeave={() => setHoverId(null)}
                  />
                )
              })}
            </g>
          </svg>

          {/* Legend — table origin (squares), column dtype (dots), FK edge. */}
          <div className="pointer-events-none absolute bottom-2 left-2 flex flex-wrap items-center gap-x-2.5 gap-y-1 rounded bg-white/85 px-2 py-1 text-[10px] text-gray-500 shadow-sm">
            <span className="inline-flex items-center gap-1">
              <span className="inline-block h-2.5 w-2.5 rounded-sm bg-blue-500" /> uploaded
            </span>
            <span className="inline-flex items-center gap-1">
              <span className="inline-block h-2.5 w-2.5 rounded-sm bg-green-600" /> derived
            </span>
            <span aria-hidden className="text-gray-300">|</span>
            <span className="inline-flex items-center gap-1">
              <span className="inline-block h-2 w-2 rounded-full bg-[#3b82f6]" /> number
            </span>
            <span className="inline-flex items-center gap-1">
              <span className="inline-block h-2 w-2 rounded-full bg-[#a855f7]" /> text
            </span>
            <span className="inline-flex items-center gap-1">
              <span className="inline-block h-2 w-2 rounded-full bg-[#f59e0b]" /> date
            </span>
            <span className="inline-flex items-center gap-1">
              <span className="inline-block h-2 w-2 rounded-full bg-[#9ca3af]" /> other
            </span>
            <span aria-hidden className="text-gray-300">|</span>
            <span className="inline-flex items-center gap-1">
              <span className="inline-block h-0.5 w-4 bg-slate-400" /> FK edge
            </span>
          </div>
        </div>
      )}
    </section>
  )
}

// ---------------------------------------------------------------------------
// SVG sub-components
// ---------------------------------------------------------------------------

/**
 * Vertical centre (diagram coords) of the row for `column` within a card.
 * Anchoring edges at the actual FK/PK column row — rather than all at the header
 * centre — fans the edges out so multiple links to one table no longer collapse
 * onto a single point. Falls back to the header centre when the column isn't
 * found, and clamps to the "+N more" row when it's past the visible rows.
 */
function colRowY(card: CardLayout, column: string): number {
  const idx = card.cols.findIndex(c => {
    const n = c.name.trim().toLowerCase()
    const normed = ZIP_FAMILY.has(n) ? 'zip_code_prefix' : n
    return normed === column
  })
  if (idx < 0) return card.y + HEADER_H / 2
  const clamped = Math.min(idx, MAX_ROWS) // past MAX_ROWS → the "+N more" row
  return card.y + HEADER_H + clamped * ROW_H + ROW_H / 2
}

// ---------------------------------------------------------------------------
// Edge routing — ported from the reference build (orthogonal, obstacle-aware,
// rounded). Computed in the parent (where the full card list is in scope) so
// the vertical run can dodge around any card it would otherwise cross.
// ---------------------------------------------------------------------------

const STUB = 16 // length of the horizontal stub leaving each card edge
const CORNER = 6 // corner radius for the rounded path
const FK_COLOR = '#94a3b8'
const FK_HI = '#2563eb'
const DRV_COLOR = '#22c55e'
const DRV_HI = '#16a34a'

type Pt = [number, number]

/** A vertical run at `x` is nudged out of any card it would cross (endpoints
 * excluded), snapping to whichever side of the obstacle is nearer. */
function dodgeX(
  x: number,
  yTop: number,
  yBot: number,
  exclude: string[],
  cards: CardLayout[],
): number {
  for (let pass = 0; pass < 5; pass++) {
    let hit: { x: number; w: number } | null = null
    for (const c of cards) {
      if (exclude.includes(c.ds.id)) continue
      if (x > c.x - 8 && x < c.x + c.w + 8 && yBot > c.y - 8 && yTop < c.y + c.h + 8) {
        hit = { x: c.x, w: c.w }
        break
      }
    }
    if (!hit) break
    const leftX = hit.x - STUB
    const rightX = hit.x + hit.w + STUB
    x = Math.abs(x - leftX) <= Math.abs(x - rightX) ? leftX : rightX
  }
  return x
}

/** Build an SVG path string with quadratic-smoothed corners through `pts`. */
function roundedPath(pts: Pt[]): string {
  const P: Pt[] = []
  for (const p of pts) {
    const last = P[P.length - 1]
    if (!last || Math.abs(last[0] - p[0]) > 0.5 || Math.abs(last[1] - p[1]) > 0.5) P.push(p)
  }
  if (P.length < 3) {
    return `M${P[0][0]},${P[0][1]} L${P[P.length - 1][0]},${P[P.length - 1][1]}`
  }
  let s = `M${P[0][0].toFixed(1)},${P[0][1].toFixed(1)}`
  for (let i = 1; i < P.length - 1; i++) {
    const a = P[i - 1]
    const b = P[i]
    const c = P[i + 1]
    const v1x = b[0] - a[0]
    const v1y = b[1] - a[1]
    const l1 = Math.hypot(v1x, v1y) || 1
    const v2x = c[0] - b[0]
    const v2y = c[1] - b[1]
    const l2 = Math.hypot(v2x, v2y) || 1
    const r1 = Math.min(CORNER, l1 / 2)
    const r2 = Math.min(CORNER, l2 / 2)
    s +=
      ` L${(b[0] - (v1x / l1) * r1).toFixed(1)},${(b[1] - (v1y / l1) * r1).toFixed(1)}` +
      ` Q${b[0].toFixed(1)},${b[1].toFixed(1)} ${(b[0] + (v2x / l2) * r2).toFixed(1)},${(b[1] + (v2y / l2) * r2).toFixed(1)}`
  }
  const L = P[P.length - 1]
  s += ` L${L[0].toFixed(1)},${L[1].toFixed(1)}`
  return s
}

interface EdgeGeometry {
  d: string // rounded path
  crow: { apex: Pt; t1: Pt; t2: Pt } // crow's-foot lines at the FK ("many") end
  tick: { x: number; y1: number; y2: number } // single tick at the PK ("one") end
  label: { x: number; y: number; text: string }
  pkSide: 'left' | 'right' // card edge the connector uses on the PK (`a`) card
  fkSide: 'left' | 'right' // card edge the connector uses on the FK (`b`) card
}

/**
 * Route a column-anchored orthogonal connector from the PK ("one") card `a` to
 * the FK ("many") card `b`. The vertical channel sits at the midpoint of the two
 * stubs and is dodged out of any intervening card. Returns the rounded path plus
 * the crow's-foot/tick geometry and the join-column label position.
 */
function routeEdge(
  a: CardLayout,
  b: CardLayout,
  link: ErLink,
  cards: CardLayout[],
): EdgeGeometry {
  const aCenter = a.x + a.w / 2
  const bCenter = b.x + b.w / 2
  // Anchor on whichever side faces the other card, at the join column's row.
  const aSide: 1 | -1 = bCenter >= aCenter ? 1 : -1 // 1 = right edge, -1 = left
  const bSide: 1 | -1 = aCenter >= bCenter ? 1 : -1
  const ax = aSide === 1 ? a.x + a.w : a.x
  const ay = colRowY(a, link.column)
  const bx = bSide === 1 ? b.x + b.w : b.x
  const by = colRowY(b, link.column)

  // Stub out of each card, meet on a dodged vertical channel midway between.
  const m1x = ax + aSide * STUB
  const m2x = bx + bSide * STUB
  let midX = (m1x + m2x) / 2
  midX = dodgeX(midX, Math.min(ay, by), Math.max(ay, by), [a.ds.id, b.ds.id], cards)

  const pts: Pt[] = [
    [ax, ay],
    [m1x, ay],
    [midX, ay],
    [midX, by],
    [m2x, by],
    [bx, by],
  ]

  // Crow's-foot ("many") at the child/FK end b.
  const apexX = bx + bSide * 11
  const crow = {
    apex: [apexX, by] as Pt,
    t1: [bx, by - 5] as Pt,
    t2: [bx, by + 5] as Pt,
  }
  // Single tick ("one") at the parent/PK end a.
  const tickX = ax + aSide * 7
  const tick = { x: tickX, y1: ay - 4, y2: ay + 4 }

  return {
    d: roundedPath(pts),
    crow,
    tick,
    label: { x: midX, y: (ay + by) / 2, text: link.column },
    pkSide: aSide === 1 ? 'right' : 'left',
    fkSide: bSide === 1 ? 'right' : 'left',
  }
}

/**
 * A column-anchored elbow edge: rounded orthogonal connector with obstacle
 * avoidance, a crow's-foot at the FK end and a tick at the PK end. The geometry
 * is pre-computed by the parent (`routeEdge`) so the router can see every card.
 *
 * `highlighted` recolours + thickens the edge and reveals its join-column label
 * (a dark pill, hidden by default), `dimmed` fades unrelated edges.
 */
function Edge({
  geom,
  derived,
  highlighted,
  dimmed,
}: {
  geom: EdgeGeometry
  derived: boolean
  highlighted: boolean
  dimmed: boolean
}) {
  const base = derived ? DRV_COLOR : FK_COLOR
  const stroke = highlighted ? (derived ? DRV_HI : FK_HI) : base
  const strokeWidth = highlighted ? 2.2 : 1.5
  const opacity = dimmed ? 0.1 : 1
  const lw = geom.label.text.length * 5.2 + 10

  return (
    <g opacity={opacity}>
      <path
        d={geom.d}
        fill="none"
        stroke={stroke}
        strokeWidth={strokeWidth}
        strokeLinejoin="round"
        strokeLinecap="round"
        strokeDasharray={derived ? '5 4' : undefined}
      />
      {/* Crow's-foot ("many") at the FK end. */}
      <line
        x1={geom.crow.apex[0]}
        y1={geom.crow.apex[1]}
        x2={geom.crow.t1[0]}
        y2={geom.crow.t1[1]}
        stroke={stroke}
        strokeWidth={strokeWidth}
        strokeLinecap="round"
      />
      <line
        x1={geom.crow.apex[0]}
        y1={geom.crow.apex[1]}
        x2={geom.crow.t2[0]}
        y2={geom.crow.t2[1]}
        stroke={stroke}
        strokeWidth={strokeWidth}
        strokeLinecap="round"
      />
      {/* Single tick ("one") at the PK end. */}
      <line
        x1={geom.tick.x}
        y1={geom.tick.y1}
        x2={geom.tick.x}
        y2={geom.tick.y2}
        stroke={stroke}
        strokeWidth={strokeWidth}
        strokeLinecap="round"
      />
      {/* Hover-only join-column pill, centred on the vertical channel. */}
      <g opacity={highlighted ? 1 : 0} style={{ pointerEvents: 'none' }}>
        <rect
          x={geom.label.x - lw / 2}
          y={geom.label.y - 7.5}
          width={lw}
          height={15}
          rx={3}
          fill="#1e293b"
        />
        <text
          x={geom.label.x}
          y={geom.label.y + 3}
          textAnchor="middle"
          fontSize={8.5}
          fill="#ffffff"
        >
          {geom.label.text}
        </text>
      </g>
      {/* Native tooltip as a fallback. */}
      <title>{`${geom.label.text} (FK)`}</title>
    </g>
  )
}

/** Normalise a column name the same way _erFkLinks does (lowercase + zip hub). */
function normColName(name: string): string {
  const n = name.trim().toLowerCase()
  return ZIP_FAMILY.has(n) ? 'zip_code_prefix' : n
}

/** A single table card with a colored header and a zebra-striped column list. */
function TableCard({
  card,
  keys,
  badgeSides,
  selected,
  dimmed,
  onSelect,
  onHover,
  onLeave,
}: {
  card: CardLayout
  keys?: { pk: Set<string>; fk: Set<string> }
  badgeSides?: Map<string, 'left' | 'right'>
  selected: boolean
  dimmed: boolean
  onSelect: () => void
  onHover: () => void
  onLeave: () => void
}) {
  const derived = card.ds.origin === 'derived'
  const headerFill = derived ? '#16a34a' : '#2563eb'
  const shown = card.cols.slice(0, MAX_ROWS)
  const extra = card.cols.length - shown.length

  return (
    <g
      transform={`translate(${card.x},${card.y})`}
      opacity={dimmed ? 0.3 : 1}
      style={{ cursor: 'pointer' }}
      onPointerDown={e => e.stopPropagation()}
      onClick={onSelect}
      onMouseEnter={onHover}
      onMouseLeave={onLeave}
    >
      {/* Card body */}
      <rect
        x={0}
        y={0}
        width={card.w}
        height={card.h}
        rx={6}
        fill="#ffffff"
        stroke={selected ? '#f59e0b' : '#cbd5e1'}
        strokeWidth={selected ? 2.5 : 1}
      />
      {/* Header */}
      <rect x={0} y={0} width={card.w} height={HEADER_H} rx={6} fill={headerFill} />
      <rect x={0} y={HEADER_H - 6} width={card.w} height={6} fill={headerFill} />
      <text
        x={10}
        y={HEADER_H / 2 + 4}
        fontSize={12}
        fontWeight={600}
        fill="#ffffff"
        style={{ pointerEvents: 'none' }}
      >
        {truncate(card.ds.filename, 22)}
      </text>
      {derived && (
        <>
          <rect
            x={card.w - 56}
            y={8}
            width={48}
            height={16}
            rx={8}
            fill="rgba(255,255,255,0.25)"
          />
          <text
            x={card.w - 32}
            y={20}
            fontSize={9}
            fontWeight={600}
            fill="#ffffff"
            textAnchor="middle"
            style={{ pointerEvents: 'none' }}
          >
            derived
          </text>
        </>
      )}

      {/* Columns. A PK/FK badge sits on the side its connector attaches to (so it
          reads as connected to the arrow); the column name shifts to make room. */}
      {shown.map((c, i) => {
        const y = HEADER_H + i * ROW_H
        const nc = normColName(c.name)
        const isPk = keys?.pk.has(nc) ?? false
        const isFk = !isPk && (keys?.fk.has(nc) ?? false)
        const badge = isPk ? 'PK' : isFk ? 'FK' : null
        const onLeft = badge !== null && (badgeSides?.get(nc) ?? 'right') === 'left'
        const nameX = onLeft ? 46 : 24
        const nameTrunc = onLeft ? 12 : badge ? 15 : 22
        const badgeX = onLeft ? 20 : card.w - 26
        const badgeTextX = onLeft ? 30 : card.w - 16
        return (
          <g key={c.name} style={{ pointerEvents: 'none' }}>
            {i % 2 === 1 && (
              <rect x={1} y={y} width={card.w - 2} height={ROW_H} fill="#f8fafc" />
            )}
            <circle cx={12} cy={y + ROW_H / 2} r={3.5} fill={dtypeColor(c.dtype)} />
            <text x={nameX} y={y + ROW_H / 2 + 4} fontSize={11} fill="#334155">
              {truncate(c.name, nameTrunc)}
            </text>
            {badge && (
              <>
                <rect
                  x={badgeX}
                  y={y + (ROW_H - 12) / 2}
                  width={20}
                  height={12}
                  rx={3}
                  fill={isPk ? '#fde68a' : '#bfdbfe'}
                />
                <text
                  x={badgeTextX}
                  y={y + ROW_H / 2 + 3}
                  textAnchor="middle"
                  fontSize={7.5}
                  fontWeight={700}
                  fill={isPk ? '#92400e' : '#1e40af'}
                >
                  {badge}
                </text>
              </>
            )}
          </g>
        )
      })}
      {extra > 0 && (
        <text
          x={24}
          y={HEADER_H + shown.length * ROW_H + ROW_H / 2 + 4}
          fontSize={10}
          fontStyle="italic"
          fill="#94a3b8"
          style={{ pointerEvents: 'none' }}
        >
          +{extra} more
        </text>
      )}
    </g>
  )
}

function truncate(s: string, n: number): string {
  return s.length > n ? `${s.slice(0, n - 1)}…` : s
}
