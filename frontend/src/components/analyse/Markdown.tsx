'use client'

import { useRef, useState, type ComponentPropsWithoutRef } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  copyText,
  downloadBlob,
  extractTableRows,
  fileStamp,
  rowsToCsv,
  rowsToTsv,
} from '@/lib/exporters'

/**
 * Markdown renderer for agent answers (C6).
 *
 * We render the answer's `answer_markdown` (NOT the server's pre-rendered HTML)
 * via react-markdown — it sanitises by construction (no raw HTML passthrough),
 * which is the safer default for model-generated content. `remark-gfm` adds
 * GitHub-flavoured tables, task lists, and strikethrough since agents commonly
 * answer with Markdown tables.
 *
 * Tailwind's preflight strips default element styling, so we restyle the small
 * set of elements an answer uses (headings, lists, tables, code) explicitly.
 */
export function Markdown({ children }: { children: string }) {
  return (
    <div className="text-sm leading-relaxed text-gray-800">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1: props => <h1 className="mt-3 mb-2 text-base font-bold" {...props} />,
          h2: props => <h2 className="mt-3 mb-2 text-sm font-bold" {...props} />,
          h3: props => <h3 className="mt-2 mb-1 text-sm font-semibold" {...props} />,
          p: props => <p className="my-2" {...props} />,
          ul: props => <ul className="my-2 list-disc space-y-1 pl-5" {...props} />,
          ol: props => <ol className="my-2 list-decimal space-y-1 pl-5" {...props} />,
          li: props => <li className="marker:text-gray-400" {...props} />,
          strong: props => <strong className="font-semibold text-gray-900" {...props} />,
          a: props => (
            <a
              className="text-blue-600 underline"
              target="_blank"
              rel="noopener noreferrer"
              {...props}
            />
          ),
          blockquote: props => (
            <blockquote
              className="my-2 border-l-2 border-gray-300 pl-3 text-gray-600"
              {...props}
            />
          ),
          code: ({ className, children, ...props }) => {
            const isBlock = /language-/.test(className ?? '')
            if (isBlock) {
              return (
                <code
                  className="block overflow-x-auto rounded bg-gray-900 p-3 font-mono text-xs text-gray-100"
                  {...props}
                >
                  {children}
                </code>
              )
            }
            return (
              <code
                className="rounded bg-gray-100 px-1 py-0.5 font-mono text-[0.85em] text-gray-800"
                {...props}
              >
                {children}
              </code>
            )
          },
          pre: props => <pre className="my-2" {...props} />,
          table: props => <MarkdownTable {...props} />,
          thead: props => <thead className="bg-gray-50" {...props} />,
          th: props => (
            <th
              className="border border-gray-200 px-2 py-1 text-left font-semibold"
              {...props}
            />
          ),
          td: props => <td className="border border-gray-200 px-2 py-1" {...props} />,
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  )
}

/**
 * A rendered Markdown table with a small Copy / CSV toolbar. "Copy" puts the
 * table on the clipboard as TSV (pastes straight into Sheets/Excel); "CSV"
 * downloads it. Cell text is read from the real rendered DOM, so it matches
 * exactly what the user sees.
 */
function MarkdownTable(props: ComponentPropsWithoutRef<'table'>) {
  const ref = useRef<HTMLTableElement>(null)
  const [copied, setCopied] = useState(false)

  const rows = () => (ref.current ? extractTableRows(ref.current) : [])

  const onCopy = async () => {
    const data = rows()
    if (data.length === 0) return
    if (await copyText(rowsToTsv(data))) {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    }
  }

  const onCsv = () => {
    const data = rows()
    if (data.length === 0) return
    downloadBlob(rowsToCsv(data), `table-${fileStamp()}.csv`, 'text/csv;charset=utf-8')
  }

  return (
    <div className="my-2">
      <div className="mb-1 flex justify-end gap-1.5">
        <button
          type="button"
          onClick={() => void onCopy()}
          className="rounded border border-gray-200 bg-white px-2 py-0.5 text-[11px] font-medium text-gray-500 hover:bg-gray-50 hover:text-gray-700"
        >
          {copied ? 'Copied' : 'Copy'}
        </button>
        <button
          type="button"
          onClick={onCsv}
          title="Download this table as CSV"
          className="rounded border border-gray-200 bg-white px-2 py-0.5 text-[11px] font-medium text-gray-500 hover:bg-gray-50 hover:text-gray-700"
        >
          CSV
        </button>
      </div>
      <div className="overflow-x-auto">
        <table ref={ref} className="w-full border-collapse text-xs" {...props} />
      </div>
    </div>
  )
}
