'use client'

import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { api, ApiError, type UploadResponse } from '@/lib/api'

/**
 * Upload card (C1, C11, C13, C17) — Staged upload queue with folder drop.
 *
 * Files are NOT uploaded immediately on drop/select. They enter a pre-commit
 * staging queue where the user can edit per-file notes and remove files before
 * committing. "Upload all" submits all staged files ≤3 concurrent.
 *
 * C13 — Multi-file / folder drop:
 *   Drag-dropping a folder reads all files via FileSystemEntry API. If the
 *   folder contains a folder-level README/notes file (name matches _notes /
 *   context / readme / notes, case-insensitive, any extension), its text is
 *   routed to the GLOBAL Project notes (the agent's memory) — best-effort,
 *   never blocking the upload — and a dismissible green banner confirms it.
 *   It is NOT pre-filled into each file's per-file notes textarea (it now lives
 *   in Project notes, which the agent already consults on every question;
 *   duplicating it per-file would be noise). Per-file `<stem>.notes.txt` files
 *   still attach as that file's individual notes. Only data files are enqueued;
 *   notes files and hidden files (starting with `.`) are filtered out.
 *
 * C17 — Staged upload queue:
 *   Each staged entry shows: filename, inferred type badge, editable notes
 *   textarea, status badge, and a Remove button. "Upload all (N files)" submits
 *   with ≤3 concurrent. Per-file states: pending → uploading → done / error /
 *   duplicate. Done entries auto-remove from the queue. 409 duplicates surface
 *   an inline "Use existing / Upload anyway" resolver.
 */

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type StagedStatus = 'pending' | 'uploading' | 'done' | 'error' | 'duplicate'

interface StagedFile {
  id: string
  file: File
  notes: string
  status: StagedStatus
  errorMsg?: string
  uploadResult?: UploadResponse
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const ACCEPT = '.csv,.tsv,.txt,.json,.xlsx,.xls,.parquet'

/** File extensions considered data files (non-notes). */
const DATA_EXTENSIONS = new Set(['.csv', '.tsv', '.txt', '.json', '.xlsx', '.xls', '.parquet'])

/**
 * Name stems (without extension, lower-cased) treated as folder-level notes
 * files. The file is read as text and applied to all sibling data files.
 */
const NOTES_STEMS = new Set(['_notes', 'context', 'readme', 'notes'])

/**
 * Suffix that makes a file a per-data-file notes attachment:
 * `sales.notes.txt` attaches to `sales.csv`.
 */
const PER_FILE_NOTES_SUFFIX = '.notes.txt'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function stemOf(name: string): string {
  const dot = name.lastIndexOf('.')
  return dot > 0 ? name.slice(0, dot).toLowerCase() : name.toLowerCase()
}

function extOf(name: string): string {
  const dot = name.lastIndexOf('.')
  return dot >= 0 ? name.slice(dot).toLowerCase() : ''
}

function isDataFile(name: string): boolean {
  if (name.startsWith('.')) return false
  return DATA_EXTENSIONS.has(extOf(name))
}

function isFolderNotesFile(name: string): boolean {
  if (name.startsWith('.')) return false
  const stem = stemOf(name)
  return NOTES_STEMS.has(stem)
}

function inferType(name: string): string {
  const ext = extOf(name)
  const map: Record<string, string> = {
    '.csv': 'CSV',
    '.tsv': 'TSV',
    '.txt': 'TXT',
    '.json': 'JSON',
    '.xlsx': 'XLSX',
    '.xls': 'XLS',
    '.parquet': 'Parquet',
  }
  return map[ext] ?? 'File'
}

/**
 * Format a byte count as a compact human-readable size (1 decimal place):
 * ≥1 MB → "8.6 MB", ≥1 KB → "12.4 KB", else "843 B". Used for the staged-row
 * size hint shown next to the type badge.
 */
function formatFileSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${bytes} B`
}

function readFileAsText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result as string)
    reader.onerror = () => reject(new Error('Failed to read file'))
    reader.readAsText(file)
  })
}

/** Read a FileSystemFileEntry as a File object. */
function fileEntryToFile(entry: FileSystemFileEntry): Promise<File> {
  return new Promise((resolve, reject) => {
    entry.file(resolve, reject)
  })
}

/**
 * Recursively read all file entries from a FileSystemDirectoryEntry.
 * Returns a flat list of FileSystemFileEntry items.
 */
function readDirectoryEntries(dir: FileSystemDirectoryEntry): Promise<FileSystemFileEntry[]> {
  return new Promise((resolve, reject) => {
    const reader = dir.createReader()
    const allEntries: FileSystemFileEntry[] = []

    function readBatch() {
      reader.readEntries(entries => {
        if (entries.length === 0) {
          resolve(allEntries)
          return
        }
        for (const entry of entries) {
          if (entry.isFile) {
            allEntries.push(entry as FileSystemFileEntry)
          }
          // Nested sub-directories are not recursed — top-level folder only
          // per the spec (single-level folder read).
        }
        readBatch()
      }, reject)
    }

    readBatch()
  })
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function UploadCard({ onUploaded }: { onUploaded: () => void }) {
  const [queue, setQueue] = useState<StagedFile[]>([])
  const [uploading, setUploading] = useState(false)
  const [dragActive, setDragActive] = useState(false)
  // C13 — green confirmation shown after a dropped folder's README/notes file
  // is routed to the global Project notes. Null = hidden.
  const [readmeBanner, setReadmeBanner] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const headingId = useId()

  // ---------------------------------------------------------------------------
  // Queue helpers
  // ---------------------------------------------------------------------------

  const updateEntry = useCallback((id: string, patch: Partial<StagedFile>) => {
    setQueue(prev => prev.map(e => (e.id === id ? { ...e, ...patch } : e)))
  }, [])

  const removeEntry = useCallback((id: string) => {
    setQueue(prev => prev.filter(e => e.id !== id))
  }, [])

  /**
   * Route a dropped folder's README/notes text into the global Project notes
   * (the agent's memory). Appends under a labelled separator when notes already
   * exist, else sets them. Best-effort: any failure (network, API) is swallowed
   * so it never blocks the staging/upload of the data files. On success the
   * green confirmation banner is shown.
   */
  const saveFolderReadmeToMemory = useCallback(
    async (folderName: string, readmeText: string) => {
      const text = readmeText.trim()
      if (!text) return
      try {
        const current = (await api.getMemory()).global_memory?.trim() ?? ''
        const header = `--- From folder: ${folderName} ---`
        const next = current ? `${current}\n\n${header}\n${text}` : text
        await api.patchMemory(next)
        setReadmeBanner('📁 Folder README saved to Project notes')
      } catch {
        // Non-critical — never block the upload on a memory write.
      }
    },
    [],
  )

  // The folder-README confirmation belongs to the active staging session — once
  // the queue empties (all files uploaded/removed) it has served its purpose.
  useEffect(() => {
    if (queue.length === 0 && readmeBanner) setReadmeBanner(null)
  }, [queue.length, readmeBanner])

  // ---------------------------------------------------------------------------
  // Stage helpers — build StagedFile entries
  // ---------------------------------------------------------------------------

  function makeEntry(file: File, notes: string): StagedFile {
    return {
      id: crypto.randomUUID(),
      file,
      notes,
      status: 'pending',
    }
  }

  /**
   * Resolve a dropped DataTransfer item list into staged entries.
   * Handles folders (via FileSystemDirectoryEntry) and plain files.
   */
  const resolveDroppedItems = useCallback(async (items: DataTransferItemList) => {
    // A fresh drop clears any prior folder-README confirmation.
    setReadmeBanner(null)

    const newEntries: StagedFile[] = []
    // Folder README text to route to Project notes after staging (best-effort),
    // keyed to the folder it came from. Only the first README found wins.
    let folderReadme: { folderName: string; text: string } | null = null

    for (let i = 0; i < items.length; i++) {
      const item = items[i]
      const entry = item.webkitGetAsEntry?.()

      if (!entry) {
        // Fallback: no FileSystemEntry API — use the File directly
        const file = item.getAsFile()
        if (file && isDataFile(file.name)) {
          newEntries.push(makeEntry(file, ''))
        }
        continue
      }

      if (entry.isDirectory) {
        // --- Folder drop (C13) -------------------------------------------
        const dir = entry as FileSystemDirectoryEntry
        const fileEntries = await readDirectoryEntries(dir)

        // Separate notes files from data files
        const perFileNotesMap = new Map<string, string>() // stem → notes text
        const dataFileEntries: FileSystemFileEntry[] = []

        for (const fe of fileEntries) {
          const name = fe.name
          if (name.startsWith('.')) continue

          if (isFolderNotesFile(name)) {
            // Folder-level README/notes → routed to global Project notes below,
            // NOT pre-filled into each file's notes (only the first match wins).
            if (!folderReadme) {
              try {
                const f = await fileEntryToFile(fe)
                const text = await readFileAsText(f)
                folderReadme = { folderName: dir.name || name, text }
              } catch {
                // Non-critical — skip bad notes files
              }
            }
          } else if (name.toLowerCase().endsWith(PER_FILE_NOTES_SUFFIX)) {
            // Per-file notes: `sales.notes.txt` → key = `sales`
            const notesFileStem = name.slice(0, name.length - PER_FILE_NOTES_SUFFIX.length).toLowerCase()
            try {
              const f = await fileEntryToFile(fe)
              const text = await readFileAsText(f)
              perFileNotesMap.set(notesFileStem, text)
            } catch {
              // Non-critical
            }
          } else if (isDataFile(name)) {
            dataFileEntries.push(fe)
          }
        }

        // Build staged entries for each data file. Per-file `<stem>.notes.txt`
        // attaches as that file's notes; the folder README does NOT pre-fill
        // here (it goes to Project notes instead).
        for (const fe of dataFileEntries) {
          try {
            const file = await fileEntryToFile(fe)
            const fileStem = stemOf(file.name)
            const notes = perFileNotesMap.get(fileStem) ?? ''
            newEntries.push(makeEntry(file, notes))
          } catch {
            // Skip unreadable files
          }
        }
      } else if (entry.isFile) {
        // --- Single file drop --------------------------------------------
        const fe = entry as FileSystemFileEntry
        if (!fe.name.startsWith('.') && isDataFile(fe.name)) {
          try {
            const file = await fileEntryToFile(fe)
            newEntries.push(makeEntry(file, ''))
          } catch {
            // Skip
          }
        }
      }
    }

    if (newEntries.length > 0) {
      setQueue(prev => [...prev, ...newEntries])
    }

    // Route a folder README to global Project notes (best-effort, non-blocking).
    if (folderReadme) {
      void saveFolderReadmeToMemory(folderReadme.folderName, folderReadme.text)
    }
  }, [saveFolderReadmeToMemory])

  /**
   * Stage plain File objects (from <input type="file"> click path).
   * No folder-notes detection — just add each file with empty notes.
   */
  const stageFiles = useCallback((files: FileList | File[]) => {
    const list = Array.from(files).filter(f => isDataFile(f.name))
    if (list.length === 0) return
    const newEntries = list.map(f => makeEntry(f, ''))
    setQueue(prev => [...prev, ...newEntries])
  }, [])

  // ---------------------------------------------------------------------------
  // Event handlers
  // ---------------------------------------------------------------------------

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault()
      setDragActive(false)
      if (e.dataTransfer?.items) {
        void resolveDroppedItems(e.dataTransfer.items)
      }
    },
    [resolveDroppedItems],
  )

  const onInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      if (e.target.files) stageFiles(e.target.files)
      e.target.value = ''
    },
    [stageFiles],
  )

  // ---------------------------------------------------------------------------
  // Upload logic
  // ---------------------------------------------------------------------------

  /**
   * Upload a single staged entry.
   * Returns true on success, false on failure (status already set in queue).
   */
  const uploadOne = useCallback(
    async (entry: StagedFile, force = false): Promise<boolean> => {
      updateEntry(entry.id, { status: 'uploading', errorMsg: undefined })
      try {
        const result = await api.upload(entry.file, {
          context: entry.notes.trim() || undefined,
          force,
        })
        updateEntry(entry.id, { status: 'done', uploadResult: result })
        onUploaded()
        return true
      } catch (err) {
        if (err instanceof ApiError && err.code === 'duplicate_dataset') {
          updateEntry(entry.id, {
            status: 'duplicate',
            errorMsg: 'A dataset with the same content already exists.',
          })
        } else {
          const msg = err instanceof Error ? err.message : 'Upload failed.'
          updateEntry(entry.id, { status: 'error', errorMsg: msg })
        }
        return false
      }
    },
    [onUploaded, updateEntry],
  )

  /** Upload all pending entries ≤3 concurrent, then remove done entries. */
  const handleUploadAll = useCallback(async () => {
    const pending = queue.filter(e => e.status === 'pending')
    if (pending.length === 0 || uploading) return

    setUploading(true)

    const CHUNK = 3
    for (let i = 0; i < pending.length; i += CHUNK) {
      const batch = pending.slice(i, i + CHUNK)
      await Promise.allSettled(batch.map(e => uploadOne(e)))
    }

    // Remove done entries from the queue
    setQueue(prev => prev.filter(e => e.status !== 'done'))
    setUploading(false)
  }, [queue, uploading, uploadOne])

  /** Force-upload a duplicate entry (Upload anyway). */
  const handleForceUpload = useCallback(
    async (entry: StagedFile) => {
      const success = await uploadOne(entry, true)
      if (success) {
        setQueue(prev => prev.filter(e => e.id !== entry.id))
      }
    },
    [uploadOne],
  )

  /** Dismiss a duplicate — mark it gone (Use existing). */
  const handleDismissDuplicate = useCallback((id: string) => {
    removeEntry(id)
  }, [removeEntry])

  // ---------------------------------------------------------------------------
  // Derived values
  // ---------------------------------------------------------------------------

  const pendingCount = queue.filter(e => e.status === 'pending').length

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <section
      aria-labelledby={headingId}
      className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm"
    >
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 id={headingId} className="text-sm font-semibold text-gray-800">
          Upload data
        </h2>
      </div>

      {/* Folder-README → Project notes confirmation (C13). Dismissible; also
          auto-clears on a new drop or when the staged queue empties. */}
      {readmeBanner && (
        <div
          role="status"
          className="mb-3 flex items-start justify-between gap-2 rounded-md border border-green-200 bg-green-50 px-3 py-2 text-xs text-green-800"
        >
          <span className="flex items-start gap-1.5">
            <span aria-hidden="true">📁</span>
            <span>Folder README saved to Project notes</span>
          </span>
          <button
            type="button"
            onClick={() => setReadmeBanner(null)}
            aria-label="Dismiss folder README confirmation"
            className="shrink-0 rounded p-0.5 text-green-600 hover:bg-green-100 hover:text-green-800"
          >
            ✕
          </button>
        </div>
      )}

      {/* Drop zone — drag target only; keyboard path is the "Choose files" button below */}
      <div
        aria-label="File drop zone"
        onDragOver={e => {
          e.preventDefault()
          setDragActive(true)
        }}
        onDragLeave={() => setDragActive(false)}
        onDrop={onDrop}
        className={`flex flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed px-4 py-8 text-center transition-colors ${
          dragActive
            ? 'border-blue-400 bg-blue-50'
            : 'border-gray-300 bg-gray-50'
        }`}
      >
        <span aria-hidden="true" className="text-2xl text-gray-400">
          &#8679;
        </span>
        <p className="text-sm text-gray-600">
          Drag &amp; drop files or folders here
        </p>
        <p className="text-xs text-gray-400">CSV · TSV · JSON · Excel · Parquet</p>
        <input
          ref={inputRef}
          type="file"
          multiple
          accept={ACCEPT}
          onChange={onInputChange}
          className="sr-only"
          aria-label="Choose data files to upload"
        />
        <button
          type="button"
          onClick={() => inputRef.current?.click()}
          className="mt-1 rounded-md border border-gray-300 bg-white px-4 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50"
        >
          Choose files
        </button>
      </div>

      {/* Staging queue */}
      {queue.length > 0 && (
        <div className="mt-4">
          <div className="mb-2 flex items-center justify-between gap-2">
            <span className="text-xs font-medium text-gray-600">
              Staged files ({queue.length})
            </span>
            {pendingCount > 0 && (
              <button
                type="button"
                onClick={() => void handleUploadAll()}
                disabled={uploading}
                className="rounded-md bg-blue-600 px-3 py-1 text-xs font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {uploading ? (
                  <span className="inline-flex items-center gap-1.5">
                    <Spinner />
                    Uploading…
                  </span>
                ) : (
                  `Upload all (${pendingCount} file${pendingCount !== 1 ? 's' : ''})`
                )}
              </button>
            )}
          </div>

          <ul role="list" className="space-y-2" aria-label="Staged upload queue">
            {queue.map(entry => (
              <StagedRow
                key={entry.id}
                entry={entry}
                uploading={uploading}
                onNotesChange={notes => updateEntry(entry.id, { notes })}
                onRemove={() => removeEntry(entry.id)}
                onForceUpload={() => void handleForceUpload(entry)}
                onDismissDuplicate={() => handleDismissDuplicate(entry.id)}
              />
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}

// ---------------------------------------------------------------------------
// StagedRow sub-component
// ---------------------------------------------------------------------------

interface StagedRowProps {
  entry: StagedFile
  uploading: boolean
  onNotesChange: (notes: string) => void
  onRemove: () => void
  onForceUpload: () => void
  onDismissDuplicate: () => void
}

function StagedRow({
  entry,
  uploading,
  onNotesChange,
  onRemove,
  onForceUpload,
  onDismissDuplicate,
}: StagedRowProps) {
  const notesId = useId()
  const notesFileRef = useRef<HTMLInputElement>(null)

  // Attach a separate .txt/.md file as this row's notes (replaces the textarea
  // content). Best-effort read; reuses the existing onNotesChange callback.
  const onNotesFileChange = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0]
      e.target.value = '' // allow re-selecting the same file later
      if (!file) return
      try {
        const text = await readFileAsText(file)
        onNotesChange(text)
      } catch {
        // Non-critical — leave the existing notes untouched on read failure.
      }
    },
    [onNotesChange],
  )

  return (
    <li
      className="rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-xs"
      aria-label={`Staged file: ${entry.file.name}`}
    >
      {/* Top row: filename + type badge + status + remove */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 flex-1 items-center gap-2">
          <span className="min-w-0 truncate font-medium text-gray-700">
            {entry.file.name}
          </span>
          <span className="shrink-0 rounded bg-blue-100 px-1.5 py-0.5 font-mono text-blue-700">
            {inferType(entry.file.name)}
          </span>
          {/* File size hint (C17) — muted, accessible. */}
          <span
            className="shrink-0 text-gray-400"
            aria-label={`Size: ${formatFileSize(entry.file.size)}`}
          >
            {formatFileSize(entry.file.size)}
          </span>
        </div>

        <div className="flex items-center gap-2">
          {/* Status badge */}
          {entry.status === 'pending' && (
            <span className="text-gray-400">Pending</span>
          )}
          {entry.status === 'uploading' && (
            <span className="inline-flex items-center gap-1.5 text-blue-600">
              <Spinner /> Uploading…
            </span>
          )}
          {entry.status === 'done' && (
            <span className="text-green-700">
              &#10003;{' '}
              {entry.uploadResult
                ? `${entry.uploadResult.row_count} rows × ${entry.uploadResult.col_count} cols`
                : 'Done'}
            </span>
          )}
          {entry.status === 'error' && (
            <span className="text-red-600" role="alert">
              &#10007; {entry.errorMsg ?? 'Upload failed'}
            </span>
          )}
          {entry.status === 'duplicate' && (
            <span className="flex flex-wrap items-center gap-2">
              <span className="text-amber-700">Duplicate</span>
              <button
                type="button"
                onClick={onDismissDuplicate}
                className="rounded border border-gray-300 bg-white px-2 py-0.5 font-medium text-gray-700 hover:bg-gray-50"
              >
                Use existing
              </button>
              <button
                type="button"
                onClick={onForceUpload}
                className="rounded border border-amber-300 bg-amber-50 px-2 py-0.5 font-medium text-amber-800 hover:bg-amber-100"
              >
                Upload anyway
              </button>
            </span>
          )}

          {/* Remove button — shown when not actively uploading this file */}
          {entry.status !== 'uploading' && (
            <button
              type="button"
              onClick={onRemove}
              disabled={uploading}
              aria-label={`Remove ${entry.file.name} from queue`}
              className="ml-1 rounded border border-gray-200 bg-white px-2 py-0.5 text-gray-500 hover:bg-red-50 hover:text-red-600 disabled:cursor-not-allowed disabled:opacity-50"
            >
              Remove
            </button>
          )}
        </div>
      </div>

      {/* Notes textarea — editable while pending or after error/duplicate */}
      {(entry.status === 'pending' ||
        entry.status === 'error' ||
        entry.status === 'duplicate') && (
        <div className="mt-2">
          <div className="mb-1 flex items-center justify-between gap-2">
            <label htmlFor={notesId} className="block text-gray-500">
              Notes (optional) — describe this file for the agent
            </label>
            {/* Attach a separate .txt/.md as this file's notes. */}
            <input
              ref={notesFileRef}
              type="file"
              accept=".txt,.md"
              onChange={e => void onNotesFileChange(e)}
              className="sr-only"
              aria-label={`Attach a notes file for ${entry.file.name}`}
            />
            <button
              type="button"
              onClick={() => notesFileRef.current?.click()}
              className="shrink-0 rounded border border-gray-200 bg-white px-2 py-0.5 font-medium text-gray-600 hover:bg-gray-50 hover:text-gray-800"
            >
              Notes file
            </button>
          </div>
          <textarea
            id={notesId}
            rows={2}
            value={entry.notes}
            onChange={e => onNotesChange(e.target.value)}
            placeholder={`e.g. Monthly sales export for ${entry.file.name}`}
            className="w-full resize-none rounded border border-gray-200 bg-white p-1.5 text-gray-800 placeholder:text-gray-400 focus:border-blue-400 focus:outline-none focus:ring-1 focus:ring-blue-400"
          />
        </div>
      )}
    </li>
  )
}

// ---------------------------------------------------------------------------
// Spinner
// ---------------------------------------------------------------------------

function Spinner() {
  return (
    <span
      aria-hidden="true"
      className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-current border-t-transparent"
    />
  )
}
