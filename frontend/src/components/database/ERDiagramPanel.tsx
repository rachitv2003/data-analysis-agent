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

/** Choose the canonical PK ("one") table for a shared join column. */
function pickPkTable(
  column: string,
  candidates: { id: string; filename: string }[],
): string {
  if (candidates.length === 0) return ''
  // The entity the column points at, e.g. `customer_id` -> `customer`.
  const clean = (s: string) => s.toLowerCase().replace(/[^a-z0-9]/g, '')
  const entity = clean(column.replace(/_id$/, ''))
  const singular = entity.replace(/s$/, '')
  const plural = `${singular}s`
  const stem = (fn: string) => clean(fn.replace(/\.[^.]+$/, ''))

  // The canonical table is the one whose NAME contains the entity — and crucially
  // we match by CONTAINMENT, not equality, so the common `olist_<entity>_dataset`
  // wrapping no longer defeats the match (the old exact check fell through to
  // "shortest filename", which wrongly made `orders` the PK for `customer_id`).
  // Prefer the plural/dimension table (`customers`, `orders`, `sellers`...), then
  // the singular, then the shortest filename as a last resort.
  const byPlural = candidates.filter(c => stem(c.filename).includes(plural))
  const bySingular = candidates.filter(c => stem(c.filename).includes(singular))
  const pool = byPlural.length ? byPlural : bySingular.length ? bySingular : candidates
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

// Hybrid-radial layout tuning.
const RING_GAP = 60 // extra radial gap (beyond a card height) between rings
const CARD_GAP = 70 // minimum gap between cards along a ring
const SEP = 24 // overlap-resolution safety gap
const STRETCH_MAX = 1.5 // cap on the mild x-stretch that fills the wide pane

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
 * Hybrid ER layout (deterministic — no Math.random/Date.now).
 *
 * If the schema has a dominant HUB (a table with FK-degree ≥ 3, e.g. an orders
 * fact table) it's laid out as a RADIAL TREE: hub at the centre, its neighbours
 * on ring 1, theirs on ring 2, … Each parent's children take an angular SECTOR
 * proportional to their subtree's leaf count, which keeps spokes clean and
 * avoids crossings; ring radii come from card size + count, so spacing is even
 * by construction. With no clear hub (flat/path-like schemas) it falls back to a
 * single CIRCLE, ordered by a BFS walk so connected tables sit adjacent.
 *
 * Centres are mildly x-stretched to use the wide Schema pane, an overlap pass
 * guarantees no two cards touch, then centres become top-left rects normalised
 * to a (20,20) origin. Returns the content bounds so the caller's fit() frames it.
 */
function layoutCards(
  datasets: ErDataset[],
  view?: { w: number; h: number },
  opts?: { stretchMax?: number; ringGap?: number },
): { cards: CardLayout[]; width: number; height: number } {
  const n = datasets.length
  if (n === 0) return { cards: [], width: 0, height: 0 }
  const stretchMax = opts?.stretchMax ?? STRETCH_MAX
  const ringGap = opts?.ringGap ?? RING_GAP

  const meta = datasets.map(ds => {
    const cols = datasetColumns(ds)
    return { ds, cols, h: cardHeight(cols) }
  })
  const hById = new Map(meta.map(m => [m.ds.id, m.h]))
  const heightOf = (id: string) => hById.get(id) ?? HEADER_H
  const maxH = Math.max(...meta.map(m => m.h))

  // Undirected adjacency from the inferred FK links.
  const adj = new Map<string, Set<string>>(datasets.map(d => [d.id, new Set<string>()]))
  for (const l of _erFkLinks(datasets)) {
    adj.get(l.fromId)?.add(l.toId)
    adj.get(l.toId)?.add(l.fromId)
  }
  const ids = datasets.map(d => d.id).sort() // deterministic

  // Hub = highest FK-degree table (id-tiebroken for stability).
  let hub = ids[0]
  let maxDeg = -1
  for (const id of ids) {
    const dg = adj.get(id)!.size
    if (dg > maxDeg) {
      maxDeg = dg
      hub = id
    }
  }

  const C: Record<string, { x: number; y: number }> = {}
  const ringStep = Math.max(maxH + ringGap, CARD_W * 0.95)

  if (maxDeg >= 3) {
    // ── Radial tree from the hub ─────────────────────────────────────────────
    const level = new Map<string, number>([[hub, 0]])
    const children = new Map<string, string[]>(datasets.map(d => [d.id, []]))
    const seen = new Set([hub])
    const queue = [hub]
    while (queue.length) {
      const u = queue.shift()!
      for (const v of [...adj.get(u)!].sort()) {
        if (seen.has(v)) continue
        seen.add(v)
        level.set(v, level.get(u)! + 1)
        children.get(u)!.push(v)
        queue.push(v)
      }
    }
    const leaves = new Map<string, number>()
    const countLeaves = (u: string): number => {
      const ch = children.get(u)!
      if (ch.length === 0) {
        leaves.set(u, 1)
        return 1
      }
      let s = 0
      for (const c of ch) s += countLeaves(c)
      leaves.set(u, s)
      return s
    }
    const totalLeaves = countLeaves(hub)
    const maxLevel = Math.max(0, ...level.values())

    // Outer radius must give each leaf enough arc for a card; inner rings are
    // spaced evenly out to it but never tighter than ringStep.
    const minLeafArc = (2 * Math.PI) / Math.max(totalLeaves, 1)
    const outerR = Math.max(maxLevel * ringStep, (CARD_W + CARD_GAP) / Math.max(minLeafArc, 1e-4))
    const radiusAt = (lv: number) => (maxLevel === 0 ? 0 : (outerR * lv) / maxLevel)

    const assign = (u: string, a0: number, a1: number) => {
      const ang = (a0 + a1) / 2
      const r = radiusAt(level.get(u)!)
      C[u] = { x: Math.cos(ang) * r, y: Math.sin(ang) * r }
      const ch = children.get(u)!
      if (!ch.length) return
      const total = leaves.get(u)!
      let a = a0
      for (const c of ch) {
        const frac = leaves.get(c)! / total
        assign(c, a, a + (a1 - a0) * frac)
        a += (a1 - a0) * frac
      }
    }
    assign(hub, -Math.PI / 2, -Math.PI / 2 + 2 * Math.PI)

    // Tables with no FK links → an extra outer ring so they stay visible.
    const loose = ids.filter(id => !seen.has(id))
    loose.forEach((id, i) => {
      const ang = -Math.PI / 2 + (i * 2 * Math.PI) / loose.length
      const r = outerR + ringStep
      C[id] = { x: Math.cos(ang) * r, y: Math.sin(ang) * r }
    })
  } else {
    // ── Circle fallback (no dominant hub) ────────────────────────────────────
    const order: string[] = []
    const seen = new Set<string>()
    for (const start of ids) {
      if (seen.has(start)) continue
      seen.add(start)
      const q = [start]
      while (q.length) {
        const u = q.shift()!
        order.push(u)
        for (const v of [...adj.get(u)!].sort())
          if (!seen.has(v)) {
            seen.add(v)
            q.push(v)
          }
      }
    }
    const r = Math.max((n * (CARD_W + CARD_GAP)) / (2 * Math.PI), ringStep)
    order.forEach((id, i) => {
      const ang = -Math.PI / 2 + (i * 2 * Math.PI) / n
      C[id] = { x: Math.cos(ang) * r, y: Math.sin(ang) * r }
    })
  }

  // Mild x-stretch to use the wide pane without distorting the shape much.
  const paneAspect = view && view.w > 40 && view.h > 40 ? view.w / view.h : 1.5
  const sx = Math.min(Math.max(paneAspect, 1), stretchMax)
  for (const id of ids) C[id].x *= sx

  // Safety: nudge apart any cards that still overlap (rare with radial sectors).
  for (let pass = 0; pass < 40; pass++) {
    let moved = false
    for (let i = 0; i < ids.length; i++) {
      for (let j = i + 1; j < ids.length; j++) {
        const pa = C[ids[i]]
        const pb = C[ids[j]]
        const ox = CARD_W + SEP - Math.abs(pa.x - pb.x)
        const oy = (heightOf(ids[i]) + heightOf(ids[j])) / 2 + SEP - Math.abs(pa.y - pb.y)
        if (ox <= 0 || oy <= 0) continue
        moved = true
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

  // Centres → top-left card rects, normalised to (20,20).
  const cards: CardLayout[] = meta.map(m => ({
    ds: m.ds,
    cols: m.cols,
    x: C[m.ds.id].x - CARD_W / 2,
    y: C[m.ds.id].y - m.h / 2,
    w: CARD_W,
    h: m.h,
  }))
  const minX = Math.min(...cards.map(c => c.x))
  const minY = Math.min(...cards.map(c => c.y))
  for (const c of cards) {
    c.x = Math.round(c.x - minX + 20)
    c.y = Math.round(c.y - minY + 20)
  }
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
  detailsOpen,
  onToggleDetails,
}: {
  datasets: ErDataset[]
  selectedId: string | null
  onSelect: (id: string) => void
  /** Whether the sibling Table-description panel is shown (for the toggle). */
  detailsOpen?: boolean
  onToggleDetails?: () => void
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
  // Live layout-tuning knobs (sliders in the header).
  const [stretchMax, setStretchMax] = useState(STRETCH_MAX)
  const [ringGap, setRingGap] = useState(RING_GAP)

  // Layout depends on the viewport (x-stretch matches the pane) and the tuning
  // knobs; recomputes on resize, dataset change, or a slider move.
  const { cards } = useMemo(
    () => layoutCards(datasets, viewSize, { stretchMax, ringGap }),
    [datasets, viewSize.w, viewSize.h, stretchMax, ringGap],
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

  // Route every edge once (per layout); hover state (highlight/dim/label) is
  // applied cheaply at render time below.
  const routedEdges = useMemo(() => {
    return links
      .map(l => {
        const a = cardById.get(l.fromId)
        const b = cardById.get(l.toId)
        if (!a || !b) return null
        return { link: l, geom: routeEdge(a, b, l) }
      })
      .filter((e): e is { link: ErLink; geom: EdgeGeometry } => e !== null)
  }, [links, cards, cardById])

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
      <div className="mb-3 flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
        <h2 id="schema-heading" className="text-sm font-semibold text-gray-800">
          Schema
        </h2>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          {/* Live layout-tuning sliders — fine-tune the radial layout in place. */}
          <label
            className="flex items-center gap-1.5 text-[11px] font-medium text-gray-500"
            title="Horizontal stretch — widens the layout to fill the pane"
          >
            <span className="whitespace-nowrap">Stretch</span>
            <input
              type="range"
              min={1}
              max={2.5}
              step={0.1}
              value={stretchMax}
              onChange={e => setStretchMax(Number(e.target.value))}
              disabled={isEmpty}
              aria-label="Horizontal stretch"
              className="h-1 w-16 cursor-pointer accent-blue-600 disabled:cursor-not-allowed"
            />
            <span className="w-5 text-right tabular-nums text-gray-400">{stretchMax.toFixed(1)}</span>
          </label>
          <label
            className="flex items-center gap-1.5 text-[11px] font-medium text-gray-500"
            title="Ring gap — spacing between the concentric rings of tables"
          >
            <span className="whitespace-nowrap">Ring gap</span>
            <input
              type="range"
              min={0}
              max={200}
              step={10}
              value={ringGap}
              onChange={e => setRingGap(Number(e.target.value))}
              disabled={isEmpty}
              aria-label="Ring gap"
              className="h-1 w-16 cursor-pointer accent-blue-600 disabled:cursor-not-allowed"
            />
            <span className="w-6 text-right tabular-nums text-gray-400">{ringGap}</span>
          </label>
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
            {onToggleDetails && (
              <button
                type="button"
                onClick={onToggleDetails}
                aria-pressed={detailsOpen ?? false}
                aria-label={detailsOpen ? 'Hide description panel' : 'Show description panel'}
                title={detailsOpen ? 'Hide description panel' : 'Show description panel'}
                className={`rounded-md border px-3 py-1 text-xs font-medium ${
                  detailsOpen
                    ? 'border-blue-200 bg-blue-50 text-blue-700 hover:bg-blue-100'
                    : 'border-gray-200 bg-white text-gray-600 hover:bg-gray-50'
                }`}
              >
                Details
              </button>
            )}
          </div>
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
// Edge routing — smooth cubic-bezier connectors. Each end anchors at its FK/PK
// column row and the curve leaves/arrives HORIZONTALLY, sweeping through open
// space (there is no right-angle channel that could drop through a card). Edges
// are painted BEHIND the cards, so a curve that overlaps a card passes behind it.
// ---------------------------------------------------------------------------

const FK_COLOR = '#94a3b8'
const FK_HI = '#2563eb'
const DRV_COLOR = '#22c55e'
const DRV_HI = '#16a34a'

type Pt = [number, number]

interface EdgeGeometry {
  d: string // rounded path
  crow: { apex: Pt; t1: Pt; t2: Pt } // crow's-foot lines at the FK ("many") end
  tick: { x: number; y1: number; y2: number } // single tick at the PK ("one") end
  label: { x: number; y: number; text: string }
}

/**
 * Build a smooth cubic-bezier connector from the PK ("one") card `a` to the FK
 * ("many") card `b`, anchored at the join column's row on each card. Each end is
 * anchored on the card edge that minimises the horizontal gap between the two
 * anchors — facing sides for side-by-side cards, the same near side for
 * stacked/overlapping cards. The curve leaves and arrives HORIZONTALLY, so it
 * sweeps through open space and (since edges paint behind the cards) any overlap
 * passes behind a card rather than through it. Crow's-foot at the FK end, tick
 * at the PK end, both flush to the horizontal tangent.
 */
function routeEdge(a: CardLayout, b: CardLayout, link: ErLink): EdgeGeometry {
  const ay = colRowY(a, link.column)
  const by = colRowY(b, link.column)
  const aEdges: Array<{ s: 1 | -1; x: number }> = [
    { s: -1, x: a.x },
    { s: 1, x: a.x + a.w },
  ]
  const bEdges: Array<{ s: 1 | -1; x: number }> = [
    { s: -1, x: b.x },
    { s: 1, x: b.x + b.w },
  ]
  let aSide: 1 | -1 = 1
  let bSide: 1 | -1 = -1
  let ax = a.x + a.w
  let bx = b.x
  let bestGap = Infinity
  for (const ae of aEdges) {
    for (const be of bEdges) {
      const gap = Math.abs(ae.x - be.x)
      if (gap < bestGap) {
        bestGap = gap
        aSide = ae.s
        bSide = be.s
        ax = ae.x
        bx = be.x
      }
    }
  }

  // Horizontal control handles. K is how far the curve pulls straight out before
  // sweeping; the floor keeps same-side loops visibly curved.
  const K = Math.max(48, Math.abs(bx - ax) * 0.5)
  const c1x = ax + aSide * K
  const c2x = bx + bSide * K
  const d =
    `M${ax.toFixed(1)},${ay.toFixed(1)} ` +
    `C${c1x.toFixed(1)},${ay.toFixed(1)} ${c2x.toFixed(1)},${by.toFixed(1)} ` +
    `${bx.toFixed(1)},${by.toFixed(1)}`

  const apexX = bx + bSide * 11
  const crow = {
    apex: [apexX, by] as Pt,
    t1: [bx, by - 5] as Pt,
    t2: [bx, by + 5] as Pt,
  }
  const tickX = ax + aSide * 7
  const tick = { x: tickX, y1: ay - 4, y2: ay + 4 }

  // Label at the curve midpoint (cubic bezier at t = 0.5).
  const lx = 0.125 * ax + 0.375 * c1x + 0.375 * c2x + 0.125 * bx
  return {
    d,
    crow,
    tick,
    label: { x: lx, y: (ay + by) / 2, text: link.column },
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
  selected,
  dimmed,
  onSelect,
  onHover,
  onLeave,
}: {
  card: CardLayout
  keys?: { pk: Set<string>; fk: Set<string> }
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

      {/* Columns. The PK/FK badge sits just after the dtype dot — a consistent
          position (like dbdiagram and other ER tools). Relationship lines attach
          at the column's ROW on the card edge, so the row is the connection point
          and the badge simply labels the column's key role. A single column can
          be referenced from both sides (e.g. a PK used by several tables), which
          is exactly why the badge is NOT tied to a connector side. */}
      {shown.map((c, i) => {
        const y = HEADER_H + i * ROW_H
        const nc = normColName(c.name)
        const isPk = keys?.pk.has(nc) ?? false
        const isFk = !isPk && (keys?.fk.has(nc) ?? false)
        const badge = isPk ? 'PK' : isFk ? 'FK' : null
        return (
          <g key={c.name} style={{ pointerEvents: 'none' }}>
            {i % 2 === 1 && (
              <rect x={1} y={y} width={card.w - 2} height={ROW_H} fill="#f8fafc" />
            )}
            <circle cx={12} cy={y + ROW_H / 2} r={3.5} fill={dtypeColor(c.dtype)} />
            {badge && (
              <>
                <rect
                  x={20}
                  y={y + (ROW_H - 12) / 2}
                  width={18}
                  height={12}
                  rx={3}
                  fill={isPk ? '#fde68a' : '#bfdbfe'}
                />
                <text
                  x={29}
                  y={y + ROW_H / 2 + 3}
                  textAnchor="middle"
                  fontSize={7}
                  fontWeight={700}
                  fill={isPk ? '#92400e' : '#1e40af'}
                >
                  {badge}
                </text>
              </>
            )}
            <text x={badge ? 44 : 24} y={y + ROW_H / 2 + 4} fontSize={11} fill="#334155">
              {truncate(c.name, badge ? 18 : 22)}
            </text>
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
