"use client";

/**
 * Fixture-only browser controls for the critical-turns audit. Compiled in
 * only when NEXT_PUBLIC_TURN_E2E_FIXTURE=1: the "Drop connection" button
 * dispatches an event the chat runner listens for, closing its socket
 * transiently so the client reconnects with its resume cursor intact.
 * Renders nothing in production builds.
 */
export function E2ETurnFixtureControls() {
  if (process.env.NEXT_PUBLIC_TURN_E2E_FIXTURE !== "1") return null;
  return (
    <div className="fixed top-4 right-4 z-50">
      <button
        type="button"
        onClick={() =>
          window.dispatchEvent(new CustomEvent("openkg-e2e-drop-connection"))
        }
        className="rounded-md border border-[var(--border)] bg-[var(--card)] px-3 py-1.5 text-[12px] font-medium text-[var(--foreground)] shadow-sm hover:border-[var(--foreground)]/30"
      >
        Drop connection
      </button>
    </div>
  );
}
