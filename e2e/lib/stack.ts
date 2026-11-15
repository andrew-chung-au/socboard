// The live stack the browser test drives (#234, D13).
//
// SOCBOARD_BASE_URL is the stack's address (`make e2e`; the proxy's, http://127.0.0.1:8080, for
// `make compose-up`). The test ingests alerts and claims cases, so it must be plain http on the
// loopback: the same rule as tests/stack_support.py, which refuses any other host. A refused value
// stops the run with fixed text that does not print it.

export const BASE_URL_VAR = "SOCBOARD_BASE_URL";
const LOOPBACK = /^http:\/\/(127\.0\.0\.1|localhost|\[::1\])(:(\d{1,5}))?\/?$/i;

export function checkedUrl(name: string, value: string): string {
  const refused = new Error(
    `${name} must be a plain http URL on 127.0.0.1, localhost or ::1, with no path, user, query or fragment.`,
  );
  const match = LOOPBACK.exec(value);
  if (!match) throw refused;
  if (match[3] !== undefined && !(Number(match[3]) >= 1 && Number(match[3]) <= 65535)) throw refused;
  return value.replace(/\/$/, "");
}

/** The checked address, or undefined when the variable is unset or empty. */
export function baseUrl(environ: Record<string, string | undefined> = process.env): string | undefined {
  const value = environ[BASE_URL_VAR];
  return value ? checkedUrl(BASE_URL_VAR, value) : undefined;
}

/** A variable the test needs; a missing or empty one fails naming it, never printing a value. */
export function required(name: string, environ: Record<string, string | undefined> = process.env): string {
  const value = environ[name];
  if (!value || !value.trim()) throw new Error(`${name} must be set for a browser run.`);
  return value;
}

export const USERS = {
  a: { username: "analyst-a", displayName: "Ana Alvarez", passwordVar: "SOCBOARD_SEED_PASSWORD_ANALYST_A" },
  b: { username: "analyst-b", displayName: "Ben Okafor", passwordVar: "SOCBOARD_SEED_PASSWORD_ANALYST_B" },
  lead: { username: "lead", displayName: "Lee Tanaka", passwordVar: "SOCBOARD_SEED_PASSWORD_LEAD" },
} as const;

/** Fail fast, naming the variable, when the stack is not up. */
export async function assertStackUp(base: string): Promise<void> {
  try {
    const answer = await fetch(`${base}/api/health`, { signal: AbortSignal.timeout(5000) });
    if (answer.ok) return;
  } catch {
    // fall through to the one message below
  }
  throw new Error(`The stack at ${BASE_URL_VAR} did not answer /api/health; start it with make compose-up.`);
}

export type AlertInput = {
  type: string;
  asset: string;
  timestamp: string;
  title: string;
  description: string;
};

/** Ingest one alert through the API with the ingest key (the API is the intake; the browser does not ingest). */
export async function ingest(base: string, alert: AlertInput): Promise<{ id: string; severity: string }> {
  const send = () =>
    fetch(`${base}/api/alerts`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-API-Key": required("SOCBOARD_INGEST_API_KEY") },
      body: JSON.stringify({ source: "e2e-browser", ...alert }),
      signal: AbortSignal.timeout(10000),
    });
  let answer = await send();
  if (answer.status === 429) {
    // The integration tests before this one use up the key's allowance (60 a minute) on purpose:
    // wait once for the window, then let a second 429 fail the test.
    const seconds = Math.min(Number(answer.headers.get("Retry-After")) || 60, 65);
    await new Promise((resolve) => setTimeout(resolve, seconds * 1000));
    answer = await send();
  }
  if (answer.status !== 201) throw new Error(`Ingesting an alert answered ${answer.status}, not 201.`);
  const created = (await answer.json()) as { id: string; severity: string };
  return { id: created.id, severity: created.severity };
}
