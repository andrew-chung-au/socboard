import { defineConfig, devices } from "@playwright/test";
import { baseUrl } from "./lib/stack";

// One worker, no retry: the journey adds cases to a shared stack and uses the ingest key's allowance.
// A failure keeps a screenshot and a trace in test-results; CI redacts the trace (e2e/redact_traces.py)
// before it uploads it, because a trace records what the test typed and what the page sent.
export default defineConfig({
  testDir: "./tests",
  outputDir: "./test-results",
  workers: 1,
  retries: 0,
  timeout: 120_000, // the first ingest may wait a minute for the key's allowance
  expect: { timeout: 10_000 },
  reporter: [["list"]],
  use: {
    baseURL: baseUrl(),
    headless: true,
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
