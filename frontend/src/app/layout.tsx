import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = {
  title: 'Data Analysis Agent',
  description: 'Upload data and ask questions in plain English — explainable answers, no code or SQL.',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-gray-50 text-gray-900 antialiased">
        {children}
        {/* Hidden live region for programmatic screen-reader announcements.
            Populated at runtime by announce() in src/lib/announce.ts. */}
        <div
          id="aria-live"
          aria-live="polite"
          aria-atomic="true"
          className="sr-only-live"
        />
      </body>
    </html>
  )
}
