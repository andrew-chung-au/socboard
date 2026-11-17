// Screenshots of the firing alert for the drill's evidence (#299).
//
//   node observability/drill/alert_shot.mjs --out <dir> [--grafana http://127.0.0.1:3000]
//        [--alertmanager http://127.0.0.1:9093] [--secrets observability.secrets]
//
// Takes two screenshots, each with a fixed viewport and the dark theme:
//   alert.png      Alertmanager's alert list, once it shows QueueLatencyOverSLO;
//   dashboard.png  Grafana's dashboard "SOCBoard: alert to queue" for the last 20 minutes, where the
//                  95th percentile is over the 5-second line.
// Both addresses must be plain http on the loopback. Grafana's admin password is read from the secrets
// file (the way observability/screenshots/capture.mjs does) and is never printed. Playwright is the one
// e2e/ pins, taken from e2e/node_modules (`make e2e-install`). CI never commits the files: a person
// commits them once, from the run's artifact.
import { mkdir, readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "..", "..");
const LOOPBACK = /^http:\/\/(127\.0\.0\.1|localhost|\[::1\])(:\d{1,5})?$/i;
const VIEWPORT = { width: 1600, height: 1000 };
const WAIT_MS = 60_000;

function option(name, fallback) {
  const at = process.argv.indexOf(`--${name}`);
  return at >= 0 ? process.argv[at + 1] : fallback;
}

function refuse(message) {
  console.error(`drill alert screenshot: refused: ${message}`);
  process.exit(2);
}

async function playwright() {
  for (const name of ["playwright-core/index.mjs", "@playwright/test/index.mjs"]) {
    try {
      const library = await import(path.join(root, "e2e", "node_modules", name));
      if (library.chromium) return library;
    } catch {
      // try the next one
    }
  }
  return refuse("Playwright is not installed: run `make e2e-install` first");
}

const out = option("out");
const grafana = (option("grafana", "http://127.0.0.1:3000") ?? "").replace(/\/$/, "");
const alertmanager = (option("alertmanager", "http://127.0.0.1:9093") ?? "").replace(/\/$/, "");
const secretsFile = option("secrets", path.join(root, "observability.secrets"));
if (!out) refuse("--out is required");
if (!LOOPBACK.test(grafana) || !LOOPBACK.test(alertmanager)) refuse("--grafana and --alertmanager must be plain http on the loopback");

const secrets = await readFile(secretsFile, "utf8");
const password = /^STACK_GRAFANA_ADMIN_PASSWORD=(.+)$/m.exec(secrets)?.[1];
if (!password) refuse("STACK_GRAFANA_ADMIN_PASSWORD is not in the secrets file");

const { chromium } = await playwright();
await mkdir(out, { recursive: true });
const browser = await chromium.launch();
const context = await browser.newContext({ viewport: VIEWPORT, colorScheme: "dark", deviceScaleFactor: 1 });
const page = await context.newPage();

// Alertmanager: the alert list (no sign-in).
await page.goto(`${alertmanager}/#/alerts`, { waitUntil: "domcontentloaded" });
await page.getByText("QueueLatencyOverSLO").first().waitFor({ timeout: WAIT_MS });
await page.waitForLoadState("networkidle");
await page.screenshot({ path: path.join(out, "alert.png"), fullPage: false });
console.log("drill alert screenshot: wrote alert.png");

// Grafana: sign in once (the form, not Basic auth), then the dashboard.
const login = await context.request.post(`${grafana}/login`, { data: { user: "admin", password } });
if (!login.ok()) refuse(`the Grafana sign-in answered HTTP ${login.status()}`);
const now = Date.now();
const range = `from=${now - 20 * 60_000}&to=${now + 60_000}`;
await page.goto(`${grafana}/d/socboard-queue-latency/socboard-alert-to-queue?orgId=1&${range}&refresh=&theme=dark&kiosk`, { waitUntil: "domcontentloaded" });
await page.waitForFunction(() => document.querySelectorAll('[data-testid^="data-testid Panel header"]').length >= 5, undefined, { timeout: WAIT_MS });
await page.waitForLoadState("networkidle");
await page.waitForTimeout(3_000);
await page.screenshot({ path: path.join(out, "dashboard.png"), fullPage: false });
console.log("drill alert screenshot: wrote dashboard.png");

await browser.close();
