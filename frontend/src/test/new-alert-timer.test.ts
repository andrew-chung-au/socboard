import { afterEach, describe, expect, it, vi } from "vitest";

// The mock adds one pre-written alert every 20 s, in mock mode only. These tests call the
// service directly with fake timers (no screens). Each test loads a fresh copy of the mock, so
// the "started once" flag and the case list never carry over from another test.
//
// Order of work in each test: sign in and read the queue with real timers (the mock answers
// after a 150 ms delay), then switch to fake timers. Under fake timers a service call is
// answered by advancing the clock a little (see queue below).

type MockModule = typeof import("@/services/mockService");

const SEV_RANK = { Critical: 4, High: 3, Medium: 2, Low: 1 } as const;

async function load(): Promise<MockModule> {
  vi.resetModules();
  const mod = await import("@/services/mockService");
  const { session } = await import("@/services/session");
  const res = await mod.mockService.login("lead", "lead-pass");
  session.set({ token: res.token, user: res.user });
  return mod;
}

/** Reads the queue under fake timers: the mock answers after 150 ms, so move the clock 200 ms. */
async function queue(mod: MockModule) {
  const pending = mod.mockService.getQueue();
  await vi.advanceTimersByTimeAsync(200);
  return (await pending).cases;
}

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
});

describe("mock new-alert timer: mock mode", () => {
  it("adds nothing before 20 s and one case after 20 s, in service order", async () => {
    vi.stubEnv("VITE_USE_MOCK", "true");
    const mod = await load();
    vi.useFakeTimers();
    const before = await queue(mod); // clock at 200 ms

    mod.startNewAlertTimer(); // the timer starts counting at 200 ms
    const soon = await queue(mod); // clock at 400 ms
    expect(soon).toHaveLength(before.length);

    // Clock to 20 200 ms, 20 s after the start: exactly one tick has run.
    await vi.advanceTimersByTimeAsync(mod.NEW_ALERT_INTERVAL_MS - 200);
    const after = await queue(mod);

    expect(after).toHaveLength(before.length + 1);
    const added = after.filter((c) => c.title === mod.NEW_ALERT.title);
    expect(added).toHaveLength(1);
    expect(added[0]?.severity).toBe("Medium");
    expect(added[0]?.status).toBe("New");
    expect(added[0]?.owner).toBeNull();
    // The service orders the queue: severity never rises as you go down the list.
    const ranks = after.map((c) => SEV_RANK[c.severity]);
    expect(ranks).toEqual([...ranks].sort((a, b) => b - a));
  });

  it("starts at most once: a second start call schedules nothing", async () => {
    vi.stubEnv("VITE_USE_MOCK", "true");
    const mod = await load();
    vi.useFakeTimers();
    const before = await queue(mod);
    const interval = vi.spyOn(globalThis, "setInterval");

    mod.startNewAlertTimer();
    mod.startNewAlertTimer();

    expect(interval).toHaveBeenCalledTimes(1);
    expect(interval).toHaveBeenCalledWith(expect.any(Function), 20_000);
    // Two timers would add two cases per 20 s.
    await vi.advanceTimersByTimeAsync(20_000);
    expect(await queue(mod)).toHaveLength(before.length + 1);
    await vi.advanceTimersByTimeAsync(20_000);
    expect(await queue(mod)).toHaveLength(before.length + 2);
  });

  it("does not start a timer when the module is loaded", async () => {
    vi.stubEnv("VITE_USE_MOCK", "true");
    const interval = vi.spyOn(globalThis, "setInterval");
    vi.resetModules();

    await import("@/services/mockService");

    expect(interval).not.toHaveBeenCalled();
  });
});

describe("mock new-alert timer: VITE_USE_MOCK is false", () => {
  it("schedules no timer and adds no case in 60 s", async () => {
    vi.stubEnv("VITE_USE_MOCK", "false");
    const mod = await load();
    vi.useFakeTimers();
    const before = await queue(mod);
    const interval = vi.spyOn(globalThis, "setInterval");
    const timeout = vi.spyOn(globalThis, "setTimeout");

    mod.startNewAlertTimer();
    await vi.advanceTimersByTimeAsync(60_000);

    expect(interval).not.toHaveBeenCalled();
    expect(timeout).not.toHaveBeenCalled();
    expect(await queue(mod)).toHaveLength(before.length);
  });
});
