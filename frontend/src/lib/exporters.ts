/**
 * Small client-side export/clipboard helpers shared by the answer toolbars
 * (copy/download a table as CSV; the chart PNG/JPEG/PDF paths live with the
 * Plotly graph in ChartRender). Browser-only — call from event handlers.
 */

/** Trigger a browser download of `content` as `filename`. */
export function downloadBlob(content: BlobPart, filename: string, mime: string): void {
  const blob = new Blob([content], { type: mime })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

/** Read a rendered <table> into rows of trimmed cell text (header + body). */
export function extractTableRows(table: HTMLTableElement): string[][] {
  const rows: string[][] = []
  for (const tr of Array.from(table.rows)) {
    const cells = Array.from(tr.cells).map(c => (c.textContent ?? '').trim())
    if (cells.length > 0) rows.push(cells)
  }
  return rows
}

/** RFC-4180-ish CSV: quote any field with a comma, quote, or newline. */
export function rowsToCsv(rows: string[][]): string {
  const esc = (v: string) => (/[",\n\r]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v)
  return rows.map(r => r.map(esc).join(',')).join('\r\n')
}

/** Tab-separated — pastes cleanly into Sheets/Excel; tabs in cells become spaces. */
export function rowsToTsv(rows: string[][]): string {
  return rows.map(r => r.map(v => v.replace(/\t/g, ' ')).join('\t')).join('\n')
}

/** Copy text to the clipboard. Returns whether it succeeded. */
export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    return false
  }
}

/** A short, filesystem-safe timestamp for export filenames (no Date at module top). */
export function fileStamp(): string {
  return new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')
}
