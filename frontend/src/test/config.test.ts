import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Defaults from the guide's starting prompt: VITE_USE_MOCK (default true),
// VITE_API_BASE_URL (default "/api"), VITE_POLL_INTERVAL_MS (default 3000).

type Vars = { useMock?: string; apiBase?: string; poll?: string };

async function loadConfig(vars: Vars) {
  vi.resetModules();
  vi.stubEnv("VITE_USE_MOCK", vars.useMock);
  vi.stubEnv("VITE_API_BASE_URL", vars.apiBase);
  vi.stubEnv("VITE_POLL_INTERVAL_MS", vars.poll);
  const mod = await import("@/services/config");
  return mod.config;
}

beforeEach(() => {
  vi.resetModules();
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
});

describe("config: VITE_USE_MOCK", () => {
  it("selects the mock when unset", async () => {
    expect((await loadConfig({})).useMock).toBe(true);
  });

  it("selects the mock for any value except the string false", async () => {
    expect((await loadConfig({ useMock: "true" })).useMock).toBe(true);
    expect((await loadConfig({ useMock: "" })).useMock).toBe(true);
  });

  it("turns the mock off for the string false", async () => {
    expect((await loadConfig({ useMock: "false" })).useMock).toBe(false);
  });
});

describe("config: VITE_API_BASE_URL", () => {
  it("defaults to /api when unset", async () => {
    expect((await loadConfig({})).apiBaseUrl).toBe("/api");
  });

  it("defaults to /api when empty", async () => {
    expect((await loadConfig({ apiBase: "" })).apiBaseUrl).toBe("/api");
  });

  it("uses the value when set", async () => {
    expect((await loadConfig({ apiBase: "/backend/v1" })).apiBaseUrl).toBe("/backend/v1");
  });
});

describe("config: VITE_POLL_INTERVAL_MS", () => {
  it("defaults to 3000 when unset", async () => {
    expect((await loadConfig({})).pollIntervalMs).toBe(3000);
  });

  it("uses a positive number", async () => {
    expect((await loadConfig({ poll: "1500" })).pollIntervalMs).toBe(1500);
  });

  it.each(["0", "abc", "-5", "", "NaN"])("gives 3000 for %j", async (value) => {
    expect((await loadConfig({ poll: value })).pollIntervalMs).toBe(3000);
  });
});

describe("services index: which service is selected", () => {
  it("picks the mock service when VITE_USE_MOCK is unset", async () => {
    vi.resetModules();
    vi.stubEnv("VITE_USE_MOCK", undefined);
    const { api } = await import("@/services");
    const { mockService } = await import("@/services/mockService");

    expect(api).toBe(mockService);
  });

  it("picks the HTTP service when VITE_USE_MOCK is false", async () => {
    vi.resetModules();
    vi.stubEnv("VITE_USE_MOCK", "false");
    const { api } = await import("@/services");
    const { httpService } = await import("@/services/httpService");

    expect(api).toBe(httpService);
  });
});
