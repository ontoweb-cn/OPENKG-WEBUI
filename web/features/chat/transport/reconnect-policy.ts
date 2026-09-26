// The turn fixture closes connections to route the browser to a designated
// backend worker; the stock 250ms->8s backoff would burn the whole turn on
// routing. Under the fixture env the backoff collapses to ~50-400ms.
const FIXTURE_MODE = process.env.NEXT_PUBLIC_TURN_E2E_FIXTURE === "1";
const BASE_DELAY_MS = FIXTURE_MODE ? 50 : 250;
const MAX_DELAY_MS = FIXTURE_MODE ? 400 : 8_000;
const IDLE_ATTEMPT_LIMIT = 5;

export function reconnectDelay(
  attempt: number,
  random: () => number = Math.random,
): number {
  const exponential = Math.min(
    MAX_DELAY_MS,
    BASE_DELAY_MS * 2 ** Math.max(0, attempt),
  );
  const jitter = 0.8 + Math.min(1, Math.max(0, random())) * 0.4;
  return Math.round(exponential * jitter);
}

export function shouldReconnect(input: {
  attempt: number;
  activeTurnId: string | null;
  pageVisible: boolean;
}): boolean {
  if (!input.pageVisible && !input.activeTurnId) return false;
  return Boolean(input.activeTurnId) || input.attempt < IDLE_ATTEMPT_LIMIT;
}
