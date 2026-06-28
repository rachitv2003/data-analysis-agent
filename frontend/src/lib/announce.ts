/**
 * announce() — posts a message to the shared #aria-live region so screen
 * readers announce transient status changes (query complete, upload done, etc.)
 *
 * The region itself is rendered in the root layout (app/layout.tsx).
 * This helper is safe to call before the DOM is ready — it silently no-ops
 * when the element is not found (e.g. during SSR or unit tests).
 *
 * Usage:
 *   import { announce } from '@/lib/announce'
 *   announce('Query complete.')
 */
export function announce(message: string): void {
  if (typeof document === 'undefined') return

  const region = document.getElementById('aria-live')
  if (!region) return

  // Setting textContent triggers the screen reader; clearing after a delay
  // allows the same message to be announced again on subsequent calls.
  region.textContent = message
  setTimeout(() => {
    region.textContent = ''
  }, 1000)
}
