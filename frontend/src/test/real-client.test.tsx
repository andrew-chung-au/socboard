import { QueryClient } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory, createRouter } from "@tanstack/react-router";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { clearCacheOnLogout } from "@/router";
import { routeTree } from "@/routeTree.gen";
import { api, config, session } from "@/services";
import { BAD_RESPONSE_MESSAGE } from "@/services/http";
import { httpService } from "@/services/httpService";
import type { CaseDetail, CaseSummary, User } from "@/services";

// The screens driven by the real HTTP client (issue #21): `api` is the mock object, but every
// method is replaced by httpService's, and `fetch` is a stub that plays the server. Nothing here
// touches the mock's state. Timing: allow 8 s per wait and 20 s per test, as screen-behaviour does.

const WAIT = { timeout: 8000 };
const TEST_TIMEOUT = 20_000;

const ANA: User = { id: "1", username: "analyst-a", displayName: "Ana Alvarez", role: "analyst" };
const BEN: User = { id: "2", username: "analyst-b", displayName: "Ben Okafor", role: "analyst" };
const LEE: User = { id: "3", username: "lead", displayName: "Lee Tanaka", role: "lead" };

const ID = "9101";
const TITLE = "Real client test case";

function makeCase(over: Partial<CaseDetail> = {}): CaseDetail {
  return {
    id: ID,
    title: TITLE,
    alertType: "malware",
    source: "EDR",
    asset: "host-1",
    assetCriticality: "High",
    severity: "High",
    status: "New",
    owner: null,
    alertTimestamp: "2026-01-01T00:00:00Z",
    ingestedAt: "2026-01-01T00:00:01Z",
    closingReason: null,
    description: "Plain description.",
    severityRule: "High: malware x high asset",
    notes: [],
    ...over,
  };
}

function reply(status: number, body: unknown) {
  return { ok: status >= 200 && status < 300, status, text: async () => JSON.stringify(body) };
}

type Refusal = { method: string; suffix: string; status: number; code: string; message: string };

/** A fake server: GET case answers `current()`, one POST is refused, everything else is empty. */
function stubServer(current: () => CaseDetail, refusal?: Refusal) {
  const fetchMock = vi.fn(async (url: string, init: { method: string }) => {
    if (refusal && init.method === refusal.method && url.endsWith(refusal.suffix)) {
      return reply(refusal.status, { code: refusal.code, message: refusal.message });
    }
    if (init.method === "GET" && url.endsWith(`/cases/${ID}`)) return reply(200, current());
    return reply(200, []);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function caseReads(fetchMock: ReturnType<typeof stubServer>) {
  return fetchMock.mock.calls.filter(([u, i]) => i.method === "GET" && u.endsWith(`/cases/${ID}`))
    .length;
}

function useHttpService() {
  for (const key of Object.keys(httpService) as Array<keyof typeof httpService>) {
    vi.spyOn(api, key).mockImplementation(httpService[key] as never);
  }
}

function signIn(user: User) {
  session.set({ token: `tok-${user.id}`, user });
}

function renderAt(path: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createRouter({
    routeTree,
    context: { queryClient },
    history: createMemoryHistory({ initialEntries: [path] }),
  });
  return render(<RouterProvider router={router} />);
}

let savedPoll: number;

beforeEach(() => {
  savedPoll = config.pollIntervalMs;
  window.sessionStorage.clear();
  useHttpService();
});

afterEach(() => {
  config.pollIntervalMs = savedPoll;
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  session.set(null);
  window.sessionStorage.clear();
});

describe("a refusal from the server shows its message, then the case is read again", () => {
  it.each<[string, Refusal, User, string]>([
    [
      "403",
      {
        method: "POST",
        suffix: `/cases/${ID}/transition`,
        status: 403,
        code: "forbidden",
        message: "Not yours to move.",
      },
      ANA,
      "Start investigating",
    ],
    [
      "409",
      {
        method: "POST",
        suffix: `/cases/${ID}/claim`,
        status: 409,
        code: "already_claimed",
        message: "Someone else claimed it.",
      },
      ANA,
      "Claim case",
    ],
    [
      "422",
      {
        method: "POST",
        suffix: `/cases/${ID}/notes`,
        status: 422,
        code: "validation_error",
        message: "That note is not valid.",
      },
      ANA,
      "Add note",
    ],
  ])(
    "%s",
    async (_label, refusal, user, buttonName) => {
      signIn(user);
      // Ana owns the case for the 403 and 422 rows; for the 409 row Ben's claim lands first.
      const isClaim = refusal.suffix.endsWith("/claim");
      let owner: User | null = isClaim ? null : user;
      const fetchMock = stubServer(() => makeCase({ owner }), refusal);
      renderAt(`/cases/${ID}`);
      await screen.findByRole("heading", { name: TITLE }, WAIT);
      if (isClaim) owner = BEN;
      if (refusal.suffix.endsWith("/notes")) {
        fireEvent.change(await screen.findByPlaceholderText("Add a note…", undefined, WAIT), {
          target: { value: "x" },
        });
      }
      const before = caseReads(fetchMock);

      fireEvent.click(await screen.findByRole("button", { name: buttonName }, WAIT));

      // The message is in the page and in a toast, so allow more than one match.
      expect((await screen.findAllByText(refusal.message, undefined, WAIT)).length).toBeGreaterThan(
        0,
      );
      await waitFor(() => expect(caseReads(fetchMock)).toBeGreaterThan(before), WAIT);
      if (isClaim)
        expect(await screen.findByText("owner: Ben Okafor", undefined, WAIT)).toBeInTheDocument();
    },
    TEST_TIMEOUT,
  );
});

describe("401 and login", () => {
  it(
    "a 401 from the queue returns to the login screen and clears the token",
    async () => {
      signIn(ANA);
      vi.stubGlobal(
        "fetch",
        vi.fn(async () =>
          reply(401, { code: "unauthorized", message: "Your session has expired." }),
        ),
      );

      renderAt("/");

      expect(await screen.findByRole("heading", { name: "Sign in" }, WAIT)).toBeInTheDocument();
      expect(session.get()).toBeNull();
      expect(window.sessionStorage.getItem("socboard.session")).toBeNull();
    },
    TEST_TIMEOUT,
  );

  it(
    "a wrong password shows the server's message and stays on the login screen",
    async () => {
      const fetchMock = vi.fn(async () =>
        reply(401, { code: "invalid_credentials", message: "Wrong username or password." }),
      );
      vi.stubGlobal("fetch", fetchMock);
      renderAt("/login");

      fireEvent.change(await screen.findByLabelText("Username", undefined, WAIT), {
        target: { value: "analyst-a" },
      });
      fireEvent.change(screen.getByLabelText("Password"), { target: { value: "wrong" } });
      fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

      expect(
        await screen.findByText("Wrong username or password.", undefined, WAIT),
      ).toBeInTheDocument();
      expect(screen.getByRole("heading", { name: "Sign in" })).toBeInTheDocument();
      expect(session.get()).toBeNull();
      expect(fetchMock).toHaveBeenCalledTimes(1);
    },
    TEST_TIMEOUT,
  );

  it(
    "a 429 on sign-in shows the server's message and stays on the login screen (#178)",
    async () => {
      const fetchMock = vi.fn(async () =>
        reply(429, {
          code: "rate_limited",
          message: "Too many failed sign-in attempts. Try again later.",
        }),
      );
      vi.stubGlobal("fetch", fetchMock);
      renderAt("/login");

      fireEvent.change(await screen.findByLabelText("Username", undefined, WAIT), {
        target: { value: "analyst-a" },
      });
      fireEvent.change(screen.getByLabelText("Password"), { target: { value: "wrong" } });
      fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

      expect(
        await screen.findByText(
          "Too many failed sign-in attempts. Try again later.",
          undefined,
          WAIT,
        ),
      ).toBeInTheDocument();
      expect(screen.getByRole("heading", { name: "Sign in" })).toBeInTheDocument();
      expect(session.get()).toBeNull();
      expect(fetchMock).toHaveBeenCalledTimes(1);
    },
    TEST_TIMEOUT,
  );

  it(
    "a good login keeps the token in session storage and opens the queue",
    async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn(async (url: string) =>
          url.endsWith("/login")
            ? reply(200, { token: "tok-new", user: ANA })
            : reply(200, { cases: [], servedAt: "2026-01-01T00:00:00Z" }),
        ),
      );
      renderAt("/login");

      fireEvent.change(await screen.findByLabelText("Username", undefined, WAIT), {
        target: { value: "analyst-a" },
      });
      fireEvent.change(screen.getByLabelText("Password"), { target: { value: "right" } });
      fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

      expect(await screen.findByRole("heading", { name: "Open queue" }, WAIT)).toBeInTheDocument();
      expect(session.get()?.token).toBe("tok-new");
      expect(window.localStorage.getItem("socboard.session")).toBeNull();
    },
    TEST_TIMEOUT,
  );
});

describe("queue polling", () => {
  it(
    "asks again every poll interval, keeps the server's order and shows text literally",
    async () => {
      signIn(ANA);
      config.pollIntervalMs = 100;
      const row = (id: string, title: string, severity: CaseSummary["severity"]): CaseSummary => ({
        ...makeCase({ id, title, severity }),
      });
      // Out of severity order on purpose: Low, then Critical.
      const served = [
        row("9001", "<b>literal</b> low", "Low"),
        row("9002", "Order test critical", "Critical"),
      ];
      // Through the real client (#131): the stubbed fetch plays the server for GET /api/queue.
      const fetchMock = vi.fn(async (url: string) =>
        url.endsWith("/queue")
          ? reply(200, { cases: served, servedAt: "2026-01-04T00:00:00Z" })
          : reply(200, []),
      );
      vi.stubGlobal("fetch", fetchMock);
      const queueCalls = () =>
        fetchMock.mock.calls.filter(([u]) => u === `${config.apiBaseUrl}/queue`).length;

      renderAt("/");

      await screen.findByText("<b>literal</b> low", undefined, WAIT);
      const first = queueCalls();
      expect(first).toBeGreaterThanOrEqual(1);
      await waitFor(() => expect(queueCalls()).toBeGreaterThanOrEqual(first + 1), WAIT); // one interval
      await waitFor(() => expect(queueCalls()).toBeGreaterThanOrEqual(first + 2), WAIT); // two intervals
      expect(queueCalls()).toBeGreaterThanOrEqual(3);
      const shown = screen.getAllByTitle(/literal|Order test/).map((el) => el.textContent);
      expect(shown).toEqual(["<b>literal</b> low", "Order test critical"]);
    },
    TEST_TIMEOUT,
  );
});

describe("the query cache does not outlive the session", () => {
  // Log out and any 401 both end the session with session.set(null), so one call covers both.
  it("ending the session empties the cache; signing in does not", () => {
    const queryClient = new QueryClient();
    const unsubscribe = clearCacheOnLogout(queryClient);
    try {
      signIn(LEE);
      queryClient.setQueryData(["audit", ID], [{ id: "a1" }]);
      queryClient.setQueryData(["closed"], [makeCase({ status: "Closed" })]);
      signIn(BEN);
      expect(queryClient.getQueryCache().getAll()).toHaveLength(2);

      session.set(null);

      expect(queryClient.getQueryData(["audit", ID])).toBeUndefined();
      expect(queryClient.getQueryCache().getAll()).toHaveLength(0);
    } finally {
      unsubscribe();
    }
  });
});

describe("controls the role cannot use are hidden", () => {
  it(
    "an Analyst sees no Closed link and no audit trail, and the audit is never requested",
    async () => {
      signIn(ANA);
      const fetchMock = stubServer(() => makeCase({ owner: ANA, status: "Investigating" }));
      renderAt(`/cases/${ID}`);

      // Positive control: the page is drawn, with the Analyst's own action.
      expect(await screen.findByRole("button", { name: "Close case…" }, WAIT)).toBeInTheDocument();
      expect(screen.queryByRole("link", { name: "Closed" })).toBeNull();
      expect(screen.queryByText("Audit trail")).toBeNull();
      expect(fetchMock.mock.calls.some(([u]) => u.includes("/audit"))).toBe(false);
    },
    TEST_TIMEOUT,
  );

  it(
    "an Analyst who opens /closed gets a note and no request",
    async () => {
      signIn(ANA);
      const fetchMock = stubServer(() => makeCase());
      renderAt("/closed");

      expect(
        await screen.findByText("Only a Lead can see closed cases.", undefined, WAIT),
      ).toBeInTheDocument();
      expect(fetchMock).not.toHaveBeenCalled();
    },
    TEST_TIMEOUT,
  );

  it.each<[string, User, string, string]>([
    ["an Analyst", ANA, "Queue", "Closed cases"],
    ["a Lead", LEE, "Closed cases", "Queue"],
  ])(
    "%s viewing a Closed case gets the back link %s",
    async (_who, user, shown, notShown) => {
      signIn(user);
      stubServer(() => makeCase({ status: "Closed", closingReason: "false_positive" }));
      renderAt(`/cases/${ID}`);

      await screen.findByRole("heading", { name: TITLE }, WAIT);
      // Scoped to <main>: the header nav has its own "Queue" link.
      const page = within(screen.getByRole("main"));
      expect(page.getByRole("link", { name: shown })).toBeInTheDocument();
      expect(page.queryByRole("link", { name: notShown })).toBeNull();
    },
    TEST_TIMEOUT,
  );

  it(
    "a Lead who opens /closed sees the list and no note",
    async () => {
      signIn(LEE);
      const closedCase: CaseSummary = makeCase({ status: "Closed", closingReason: "duplicate" });
      const fetchMock = vi.fn(async (url: string, init: { method: string }) =>
        init.method === "GET" && url.endsWith("/cases?status=Closed")
          ? reply(200, [closedCase])
          : reply(200, []),
      );
      vi.stubGlobal("fetch", fetchMock);
      renderAt("/closed");

      expect(await screen.findByText(TITLE, undefined, WAIT)).toBeInTheDocument();
      expect(screen.queryByText("Only a Lead can see closed cases.")).toBeNull();
    },
    TEST_TIMEOUT,
  );

  it(
    "a Lead sees the Closed link and the audit trail",
    async () => {
      signIn(LEE);
      stubServer(() => makeCase({ owner: ANA, status: "Investigating" }));
      renderAt(`/cases/${ID}`);

      expect(await screen.findByText("Audit trail", undefined, WAIT)).toBeInTheDocument();
      expect(screen.getByRole("link", { name: "Closed" })).toBeInTheDocument();
    },
    TEST_TIMEOUT,
  );
});

// --- #131 -------------------------------------------------------------------------------------

describe("the user list is asked for only when Reassign can show (#131)", () => {
  it.each<[string, boolean, number]>([
    ["off", false, 0],
    ["on", true, 1],
  ])(
    "a Lead with featureReassign %s: %i calls to /api/users",
    async (_label, flag, calls) => {
      const saved = config.featureReassign;
      config.featureReassign = flag;
      try {
        signIn(LEE);
        const fetchMock = stubServer(() => makeCase({ owner: ANA, status: "Investigating" }));
        renderAt(`/cases/${ID}`);

        // Positive control: the page is drawn and the audit (also Lead-only) was asked for.
        expect(await screen.findByText("Audit trail", undefined, WAIT)).toBeInTheDocument();
        await waitFor(
          () => expect(fetchMock.mock.calls.some(([u]) => u.endsWith("/audit"))).toBe(true),
          WAIT,
        );
        expect(
          fetchMock.mock.calls.filter(([u]) => u === `${config.apiBaseUrl}/users`),
        ).toHaveLength(calls);
      } finally {
        config.featureReassign = saved;
      }
    },
    TEST_TIMEOUT,
  );
});

describe("a 2xx response that is not JSON shows a fixed message (#131)", () => {
  const notJson = { ok: true, status: 200, text: async () => "<html>captive portal zz-77</html>" };

  it(
    "on the case page: the message, and no error boundary",
    async () => {
      signIn(ANA);
      vi.stubGlobal(
        "fetch",
        vi.fn(async () => notJson),
      );
      renderAt(`/cases/${ID}`);

      expect(await screen.findByText(BAD_RESPONSE_MESSAGE, undefined, WAIT)).toBeInTheDocument();
      expect(screen.queryByText(/Something went wrong on our end/)).toBeNull();
      expect(screen.queryByText(/zz-77/)).toBeNull();
    },
    TEST_TIMEOUT,
  );

  it(
    "on sign-in: the message, and the page stays on Sign in",
    async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn(async () => notJson),
      );
      renderAt("/login");

      fireEvent.change(await screen.findByLabelText("Username", undefined, WAIT), {
        target: { value: "analyst-a" },
      });
      fireEvent.change(screen.getByLabelText("Password"), { target: { value: "right" } });
      fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

      expect(await screen.findByText(BAD_RESPONSE_MESSAGE, undefined, WAIT)).toBeInTheDocument();
      expect(screen.getByRole("heading", { name: "Sign in" })).toBeInTheDocument();
      expect(session.get()).toBeNull();
    },
    TEST_TIMEOUT,
  );
});

describe("Sign in says why the session ended (#131)", () => {
  const EXPIRED = "Your session has expired. Please sign in again.";

  it(
    "after a 401 it shows the server's message, and a good sign-in clears it",
    async () => {
      signIn(ANA);
      let expired = true;
      vi.stubGlobal(
        "fetch",
        vi.fn(async (url: string) => {
          if (url.endsWith("/login")) return reply(200, { token: "tok-new", user: ANA });
          return expired
            ? reply(401, { code: "unauthorized", message: EXPIRED })
            : reply(200, { cases: [], servedAt: "2026-01-01T00:00:00Z" });
        }),
      );
      renderAt("/");

      expect(await screen.findByRole("heading", { name: "Sign in" }, WAIT)).toBeInTheDocument();
      expect(await screen.findByRole("status", undefined, WAIT)).toHaveTextContent(EXPIRED);
      expect(window.sessionStorage.getItem("socboard.session")).toBeNull();
      expect(JSON.stringify({ ...window.localStorage })).not.toContain("expired");

      expired = false;
      fireEvent.change(screen.getByLabelText("Username"), { target: { value: "analyst-a" } });
      fireEvent.change(screen.getByLabelText("Password"), { target: { value: "right" } });
      fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

      expect(await screen.findByRole("heading", { name: "Open queue" }, WAIT)).toBeInTheDocument();
      expect(session.endedMessage()).toBeNull();
    },
    TEST_TIMEOUT,
  );

  it(
    "after a plain Log out it shows nothing",
    async () => {
      signIn(ANA);
      vi.stubGlobal(
        "fetch",
        vi.fn(async () => reply(200, { cases: [], servedAt: "2026-01-01T00:00:00Z" })),
      );
      renderAt("/");

      fireEvent.click(await screen.findByRole("button", { name: /Log out/ }, WAIT));

      expect(await screen.findByRole("heading", { name: "Sign in" }, WAIT)).toBeInTheDocument();
      expect(screen.queryByRole("status")).toBeNull();
      expect(session.endedMessage()).toBeNull();
    },
    TEST_TIMEOUT,
  );
});

describe("the case page shows every action, disabled when not allowed (#131)", () => {
  type Enabled = { claim: boolean; start: boolean; close: boolean };
  const BUTTONS: Array<[keyof Enabled, string]> = [
    ["claim", "Claim case"],
    ["start", "Start investigating"],
    ["close", "Close case…"],
  ];

  // Each case is owned by Ana; the viewer is Ana (owner), Ben (an Analyst who is not the owner) or Lee (Lead).
  it.each<[CaseDetail["status"], string, User, Enabled]>([
    ["New", "Analyst owner", ANA, { claim: false, start: true, close: false }],
    ["New", "Analyst non-owner", BEN, { claim: false, start: false, close: false }],
    ["New", "Lead", LEE, { claim: false, start: true, close: false }],
    ["Investigating", "Analyst owner", ANA, { claim: false, start: false, close: true }],
    ["Investigating", "Analyst non-owner", BEN, { claim: false, start: false, close: false }],
    ["Investigating", "Lead", LEE, { claim: false, start: false, close: true }],
    ["Closed", "Analyst owner", ANA, { claim: false, start: false, close: false }],
    ["Closed", "Analyst non-owner", BEN, { claim: false, start: false, close: false }],
    ["Closed", "Lead", LEE, { claim: false, start: false, close: false }],
  ])(
    "%s case, %s",
    async (status, _who, viewer, enabled) => {
      signIn(viewer);
      const closingReason = status === "Closed" ? "duplicate" : null;
      stubServer(() => makeCase({ status, owner: ANA, closingReason }));
      renderAt(`/cases/${ID}`);

      await screen.findByRole("heading", { name: TITLE }, WAIT);
      for (const [key, name] of BUTTONS) {
        const button = await screen.findByRole("button", { name }, WAIT);
        if (enabled[key]) {
          expect(button, name).toBeEnabled();
          expect(button.getAttribute("aria-describedby"), name).toBeNull();
        } else {
          expect(button, name).toBeDisabled();
          const reasonId = button.getAttribute("aria-describedby");
          expect(reasonId, name).toBeTruthy();
          expect(document.getElementById(reasonId as string)?.textContent, name).toBeTruthy();
        }
      }
    },
    TEST_TIMEOUT,
  );

  it(
    "an unowned New case: Claim is enabled with no reason; the others say why not",
    async () => {
      signIn(BEN);
      stubServer(() => makeCase({ status: "New", owner: null }));
      renderAt(`/cases/${ID}`);

      expect(await screen.findByRole("button", { name: "Claim case" }, WAIT)).toBeEnabled();
      expect(screen.getByRole("button", { name: "Close case…" })).toBeDisabled();
      expect(screen.getByText("Start investigating first")).toBeInTheDocument();
    },
    TEST_TIMEOUT,
  );
});
