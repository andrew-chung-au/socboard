import { QueryClient } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory, createRouter } from "@tanstack/react-router";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { NEW_ALERT_INTERVAL_MS } from "@/services/mockService";

// The root route starts the new-alert timer when a page loads. The service-level behaviour
// (20 s, once, no case in mock-off mode) is tested in new-alert-timer.test.ts; this file only
// checks the wiring, with the real routes. The mock service remembers that the timer started, so
// each test resets the module registry and loads the routes afresh (loadRoutes): every test sees
// a first start, and the tests give the same result in any order.

const WAIT = { timeout: 8000 };
const realSetInterval = globalThis.setInterval as unknown as (...args: unknown[]) => unknown;

/** Watches setInterval. A 20 s interval is recorded but not created, so none outlives the test. */
function watchIntervals() {
  const spy = vi.spyOn(globalThis, "setInterval");
  spy.mockImplementation(((fn: () => void, ms?: number, ...rest: unknown[]) =>
    ms === NEW_ALERT_INTERVAL_MS
      ? 0
      : realSetInterval.call(globalThis, fn, ms, ...rest)) as unknown as typeof setInterval);
  return () => spy.mock.calls.filter(([, ms]) => ms === NEW_ALERT_INTERVAL_MS).length;
}

/** Loads the route tree (and so a fresh mock service with its "started" flag unset). */
async function loadRoutes() {
  vi.resetModules();
  return (await import("@/routeTree.gen")).routeTree;
}

function renderLogin(routeTree: Awaited<ReturnType<typeof loadRoutes>>) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createRouter({
    routeTree,
    context: { queryClient },
    history: createMemoryHistory({ initialEntries: ["/login"] }),
  });
  return render(<RouterProvider router={router} />);
}

/** Lets effects that run after the first paint finish. */
const settle = () => new Promise((resolve) => setTimeout(resolve, 100));

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
});

describe("root route and the new-alert timer", () => {
  it("does not start the timer when VITE_USE_MOCK is false", async () => {
    vi.stubEnv("VITE_USE_MOCK", "false");
    const startedCount = watchIntervals();
    const routeTree = await loadRoutes();

    renderLogin(routeTree);
    await screen.findByRole("heading", { name: "Sign in" }, WAIT);
    await settle();

    expect(startedCount()).toBe(0);
  }, 20_000);

  it("starts the timer once in mock mode, however many times the page loads", async () => {
    vi.stubEnv("VITE_USE_MOCK", "true");
    const startedCount = watchIntervals();
    const routeTree = await loadRoutes();

    const first = renderLogin(routeTree);
    await screen.findByRole("heading", { name: "Sign in" }, WAIT);
    await waitFor(() => expect(startedCount()).toBe(1), WAIT);
    first.unmount();

    renderLogin(routeTree);
    await screen.findByRole("heading", { name: "Sign in" }, WAIT);
    await settle();

    expect(startedCount()).toBe(1);
  }, 20_000);

  // Order check that runs on every test run (CI cannot pass Vitest a shuffle option): the mock-on
  // scenario runs first and starts the timer, then the mock-off scenario must still see none. If
  // the "started" flag leaked between page loads, the second half would pass for the wrong reason
  // or the first half would fail when run after another start.
  it("gives the same results with the mock-on scenario first and the mock-off scenario second", async () => {
    vi.stubEnv("VITE_USE_MOCK", "true");
    const onCount = watchIntervals();
    const onRoutes = await loadRoutes();
    const on = renderLogin(onRoutes);
    await screen.findByRole("heading", { name: "Sign in" }, WAIT);
    await waitFor(() => expect(onCount()).toBe(1), WAIT);
    on.unmount();
    vi.restoreAllMocks();

    vi.stubEnv("VITE_USE_MOCK", "false");
    const offCount = watchIntervals();
    const offRoutes = await loadRoutes();
    renderLogin(offRoutes);
    await screen.findByRole("heading", { name: "Sign in" }, WAIT);
    await settle();

    expect(offCount()).toBe(0);
  }, 30_000);
});
