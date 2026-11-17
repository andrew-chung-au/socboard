// Screenshots and query results of the observability stack for the reviewer evidence (#293).
//
//   node observability/screenshots/capture.mjs --out <dir> --trace-id <32 hex> --from <ms> --to <ms>
//        [--grafana http://127.0.0.1:3000] [--secrets observability.secrets]
//
// It signs in to Grafana with the admin password read from `observability.secrets` (never printed), and
// takes three screenshots in kiosk mode: the dashboard "SOCBoard: alert to queue", one trace in Tempo
// and the log lines of that trace in Loki. Each has a fixed viewport and the dark theme, refresh off, an
// absolute `from` and `to` (the run's probe times, in milliseconds since the epoch), and is taken after
// every panel has finished loading. It also saves what Tempo, Loki and Prometheus answer for that trace
// (trace.json, logs.json, exemplars.json), with the Grafana password and the canary strings of
// observability/CONTRACT.md section 8 replaced by "[redacted]". It writes nothing else, and CI never
// commits: a person commits the files, once, from the run's artifact.
//
// Playwright is the one `e2e/` already pins (no new package). This file is plain Node, so it takes the
// library from e2e/node_modules, which `make e2e-install` fills.
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "..", "..");
// e2e/package.json declares @playwright/test; the install hoists playwright-core beside it. Either has `chromium`.
async function playwright() {
  for (const name of ["playwright-core/index.mjs", "@playwright/test/index.mjs"]) {
    try {
      const library = await import(path.join(root, "e2e", "node_modules", name));
      if (library.chromium) return library;
    } catch {
      // try the next one
    }
  }
  refuse("Playwright is not installed: run `make e2e-install` first");
}
const { chromium } = await playwright();

const LOOPBACK = /^http:\/\/(127\.0\.0\.1|localhost|\[::1\])(:\d{1,5})?$/i;
const TRACE_ID = /^[0-9a-f]{32}$/;
const VIEWPORT = { width: 1600, height: 1000 };
const PANEL_TIMEOUT_MS = 60_000;

function option(name, fallback) {
  const at = process.argv.indexOf(`--${name}`);
  return at >= 0 ? process.argv[at + 1] : fallback;
}

function refuse(message) {
  console.error(`observability screenshots: refused: ${message}`);
  process.exit(2);
}

const out = option("out");
const traceId = option("trace-id");
const from = option("from");
const to = option("to");
const grafana = (option("grafana", "http://127.0.0.1:3000") ?? "").replace(/\/$/, "");
const secretsFile = option("secrets", path.join(root, "observability.secrets"));
if (!out) refuse("--out is required");
if (!traceId || !TRACE_ID.test(traceId) || /^0+$/.test(traceId)) refuse("--trace-id must be 32 lower-case hex characters");
if (!/^\d{13}$/.test(from ?? "") || !/^\d{13}$/.test(to ?? "") || Number(from) >= Number(to)) refuse("--from and --to must be milliseconds since the epoch, from before to");
if (!LOOPBACK.test(grafana)) refuse("--grafana must be plain http on 127.0.0.1, localhost or ::1");

// The password never goes on a command line or into the output.
const secrets = await readFile(secretsFile, "utf8");
const password = /^STACK_GRAFANA_ADMIN_PASSWORD=(.+)$/m.exec(secrets)?.[1];
if (!password) refuse("STACK_GRAFANA_ADMIN_PASSWORD is not in the secrets file");

// The canary strings, read from the contract (section 8), so a leak into a saved answer is caught here too.
const contract = await readFile(path.join(root, "observability", "CONTRACT.md"), "utf8");
const canaries = [...contract.matchAll(/^[a-z-]+=(CANARY-[A-Za-z0-9-]+)$/gm)].map((match) => match[1]);

function redact(text) {
  let clean = text.split(password).join("[redacted]");
  for (const canary of canaries) clean = clean.split(canary).join("[redacted]");
  return clean;
}

await mkdir(out, { recursive: true });
const browser = await chromium.launch();
const context = await browser.newContext({ viewport: VIEWPORT, colorScheme: "dark", deviceScaleFactor: 1 });

// Sign in once: Grafana sends a page request to its login form, not a Basic-auth challenge, so the browser's
// own credentials would never be used. The session cookie is shared by the page and by context.request.
const login = await context.request.post(`${grafana}/login`, { data: { user: "admin", password } });
if (!login.ok()) refuse(`the sign-in answered HTTP ${login.status()}`);
const whoami = await context.request.get(`${grafana}/api/user`);
if (!whoami.ok()) refuse(`the session was not accepted (HTTP ${whoami.status()})`);
const page = await context.newPage();

async function panelsLoaded(minimum) {
  // Grafana marks each panel's header and shows a loading bar while it queries.
  await page.waitForFunction(
    (count) => document.querySelectorAll('[data-testid^="data-testid Panel header"]').length >= count,
    minimum,
    { timeout: PANEL_TIMEOUT_MS },
  );
  await page.waitForFunction(
    () => document.querySelectorAll('[aria-label="Panel loading bar"], [data-testid="data-testid Panel loading bar"]').length === 0,
    undefined,
    { timeout: PANEL_TIMEOUT_MS },
  );
  await page.waitForLoadState("networkidle");
  // The loading-bar selectors can match nothing and pass at once; a short settle lets the last panel draw.
  await page.waitForTimeout(3_000);
}

// Explore shows a "Cancel" button while a query runs. On the Loki page it can stay for a long time after
// the log lines and the volume chart are drawn (the first run photographed it, with the content complete),
// so wait for it a short while and go on; the screenshot is of the drawn content either way.
async function queriesFinished() {
  await page.getByRole("button", { name: "Cancel" }).waitFor({ state: "hidden", timeout: 10_000 }).catch(() => {});
  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(2_000);
}

async function shoot(name, url, ready) {
  await page.goto(url, { waitUntil: "domcontentloaded" });
  await ready();
  await page.screenshot({ path: path.join(out, name), fullPage: false });
  console.log(`observability screenshots: wrote ${name}`);
}

const range = `from=${from}&to=${to}`;
const pane = (datasource, query) =>
  encodeURIComponent(
    JSON.stringify({
      a: {
        datasource: datasource.uid,
        queries: [{ refId: "A", datasource, ...query }],
        range: { from, to },
      },
    }),
  );

await shoot(
  "dashboard.png",
  `${grafana}/d/socboard-queue-latency/socboard-alert-to-queue?orgId=1&${range}&refresh=&theme=dark&kiosk`,
  () => panelsLoaded(5),
);
await shoot(
  "trace.png",
  `${grafana}/explore?schemaVersion=1&orgId=1&theme=dark&kiosk&panes=${pane({ type: "tempo", uid: "tempo" }, { queryType: "traceql", query: traceId })}`,
  async () => {
    await page.getByText("ingest.alert").first().waitFor({ timeout: PANEL_TIMEOUT_MS });
    await queriesFinished();
  },
);
await shoot(
  "logs.png",
  `${grafana}/explore?schemaVersion=1&orgId=1&theme=dark&kiosk&panes=${pane({ type: "loki", uid: "loki" }, { queryType: "range", expr: `{service_name="socboard"} | trace_id="${traceId}"` })}`,
  async () => {
    await page.getByText("alert.ingested").first().waitFor({ timeout: PANEL_TIMEOUT_MS });
    await queriesFinished();
  },
);

// What the three stores answer for the trace, through Grafana's datasource proxy (the same path a panel uses).
async function save(name, url, headers = {}) {
  const response = await context.request.get(url, { headers });
  if (!response.ok()) refuse(`${name}: HTTP ${response.status()}`);
  const body = redact(await response.text());
  await writeFile(path.join(out, name), `${JSON.stringify(JSON.parse(body), null, 2)}\n`);
  console.log(`observability screenshots: wrote ${name}`);
}
const nanos = (ms) => `${ms}000000`;
await save("trace.json", `${grafana}/api/datasources/proxy/uid/tempo/api/traces/${traceId}`, { Accept: "application/json" });
await save(
  "logs.json",
  `${grafana}/api/datasources/proxy/uid/loki/loki/api/v1/query_range?${new URLSearchParams({ query: `{service_name="socboard"} | trace_id="${traceId}"`, start: nanos(from), end: nanos(to), limit: "20", direction: "backward" })}`,
  { "X-Loki-Response-Encoding-Flags": "categorize-labels" },
);
await save(
  "exemplars.json",
  `${grafana}/api/datasources/proxy/uid/prometheus/api/v1/query_exemplars?${new URLSearchParams({ query: "socboard_probe_time_to_queue_seconds_bucket", start: String(Number(from) / 1000), end: String(Number(to) / 1000) })}`,
);

await browser.close();
