import { QueryClient } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory, createRouter } from "@tanstack/react-router";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { routeTree } from "@/routeTree.gen";
import { NEW_ALERT_INTERVAL_MS } from "@/services/mockService";

// The root route starts the new-alert timer when a page loads. The service-level behaviour
// (20 s, once, no case in mock-off mode) is tested in new-alert-timer.test.ts; this file only
// checks the wiring, with the real routes. The module remembers that the timer started, so the
// "mock off" test comes first: it must not start the timer, and the "mock on" test then sees the
// first start in this file.

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

function renderLogin() {
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

    renderLogin();
    await screen.findByRole("heading", { name: "Sign in" }, WAIT);
    await settle();

    expect(startedCount()).toBe(0);
  }, 20_000);

  it("starts the timer once in mock mode, however many times the page loads", async () => {
    vi.stubEnv("VITE_USE_MOCK", "true");
    const startedCount = watchIntervals();

    const first = renderLogin();
    await screen.findByRole("heading", { name: "Sign in" }, WAIT);
    await waitFor(() => expect(startedCount()).toBe(1), WAIT);
    first.unmount();

    renderLogin();
    await screen.findByRole("heading", { name: "Sign in" }, WAIT);
    await settle();

    expect(startedCount()).toBe(1);
  }, 20_000);
});
