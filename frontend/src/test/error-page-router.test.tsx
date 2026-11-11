import { QueryClient } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory, createRouter } from "@tanstack/react-router";
import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { routeTree } from "@/routeTree.gen";
import { session } from "@/services";

// The error page, reached the way a visitor reaches it: the real routeTree in a RouterProvider,
// a route that throws while it renders, and the root route's errorComponent catching it.
// error-page.test.tsx renders ErrorPage directly, so it would still pass if the root route
// stopped using ErrorPage; this file would not (heading and message would be missing).

const { MESSAGE } = vi.hoisted(() => ({ MESSAGE: "<img src=x onerror=alert(1)>" }));

// The queue page calls useNow() when it renders. Making it throw is the render error.
vi.mock("@/lib/useNow", () => ({
  useNow: () => {
    throw new Error(MESSAGE);
  },
}));

const WAIT = { timeout: 8000 };

beforeEach(() => {
  // The queue page only renders for a signed-in user (AppShell redirects to /login otherwise).
  session.set({
    token: "test-token-u1",
    user: { id: "u1", username: "analyst-a", displayName: "Ana Alvarez", role: "analyst" },
  });
  // React and the error component log the error; keep the test output readable.
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
  session.set(null);
});

describe("error page through the router", () => {
  it("shows the heading and the message as literal text, with no img element", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const router = createRouter({
      routeTree,
      context: { queryClient },
      history: createMemoryHistory({ initialEntries: ["/"] }),
    });

    const { container } = render(<RouterProvider router={router} />);

    expect(await screen.findByRole("heading", { name: "This page didn't load" }, WAIT)).toBeInTheDocument();
    expect(screen.getByText(MESSAGE)).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
    expect(document.body.querySelector("img")).toBeNull();
  }, 20_000);
});
