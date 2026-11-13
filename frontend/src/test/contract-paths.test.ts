import { readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { config } from "@/services/config";
import { httpService } from "@/services/httpService";

// Every method and path the HTTP service sends must exist in openapi.yaml (issue #21, criterion 1).
// No YAML parser is installed, so the paths are read from the text: under `paths:`, a path is a
// line indented 2 spaces and its methods are lines indented 4 spaces.

function contractOperations(): Set<string> {
  const text = readFileSync(path.resolve(__dirname, "../../../openapi.yaml"), "utf8");
  const ops = new Set<string>();
  let inPaths = false;
  let current = "";
  for (const line of text.split("\n")) {
    if (/^\S/.test(line)) inPaths = line.startsWith("paths:");
    if (!inPaths) continue;
    const p = /^ {2}(\/\S*):\s*$/.exec(line);
    if (p) current = p[1];
    const m = /^ {4}(get|post|put|patch|delete):\s*$/.exec(line);
    if (m && current) ops.add(`${m[1].toUpperCase()} ${current}`);
  }
  return ops;
}

describe("service-layer paths exist in openapi.yaml", () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  beforeEach(() => {
    fetchMock = vi.fn(async () => ({ ok: true, status: 200, text: async () => "{}" }));
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => vi.unstubAllGlobals());

  it("reads the contract's operations (positive control)", () => {
    const ops = contractOperations();
    expect(ops.has("POST /login")).toBe(true);
    expect(ops.has("GET /cases/{id}/audit")).toBe(true);
  });

  it("finds every method and path the HTTP service sends in the contract", async () => {
    const calls: Array<() => Promise<unknown>> = [
      () => httpService.login("u", "p"),
      () => httpService.getQueue(),
      () => httpService.getClosedCases(),
      () => httpService.getCase("1"),
      () => httpService.claimCase("1"),
      () => httpService.transitionCase("1", "Investigating"),
      () => httpService.reassignCase("1", "2"),
      () => httpService.addNote("1", "n"),
      () => httpService.getAudit("1"),
      () => httpService.listUsers(),
    ];
    // A new function on the service must be added above.
    expect(calls).toHaveLength(Object.keys(httpService).length);

    const sent: string[] = [];
    for (const call of calls) {
      fetchMock.mockClear();
      await call();
      const [url, init] = fetchMock.mock.calls[0] as [string, { method: string }];
      const route = url.slice(config.apiBaseUrl.length).split("?")[0].replace(/^\/cases\/[^/]+/, "/cases/{id}");
      sent.push(`${init.method} ${route}`);
    }

    const ops = contractOperations();
    expect(sent.filter((s) => !ops.has(s))).toEqual([]);
  });
});
