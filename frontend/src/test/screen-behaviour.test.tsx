import { QueryClient } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory, createRouter } from "@tanstack/react-router";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { routeTree } from "@/routeTree.gen";
import { ApiError, api, config, session } from "@/services";
import type { CaseDetail, CaseSummary, Status, User } from "@/services";

// Screen behaviour through the real routes and components. The service is the real `api` object
// (the mock, VITE_USE_MOCK unset) with the methods a test cares about replaced by spies, so each
// test sets exactly the answers it needs and does not depend on the mock's state or on another
// test. A user is "signed in" by writing the session; the spies do not check the token.
//
// Timing: the mock answers after 150 ms and the first render waits for the session to load, so
// every wait below allows 8 s and every test 20 s (the default test timeout is 5 s).

const WAIT = { timeout: 8000 };
const TEST_TIMEOUT = 20_000;

const ANA: User = { id: "u1", username: "analyst-a", displayName: "Ana Alvarez", role: "analyst" };
const BEN: User = { id: "u2", username: "analyst-b", displayName: "Ben Okafor", role: "analyst" };
const LEE: User = { id: "u3", username: "lead", displayName: "Lee Tanaka", role: "lead" };

const CASE_ID = "C-9101";
const CASE_TITLE = "Screen behaviour test case";

function makeCase(over: Partial<CaseDetail> = {}): CaseDetail {
  return {
    id: CASE_ID,
    title: CASE_TITLE,
    alertType: "malware",
    source: "EDR",
    asset: "host-1",
    assetCriticality: "High",
    severity: "High",
    status: "Investigating",
    owner: ANA,
    alertTimestamp: "2026-01-01T00:00:00.000Z",
    ingestedAt: "2026-01-01T00:00:01.000Z",
    closingReason: null,
    description: "Plain description.",
    severityRule: "High: malware x high asset",
    notes: [],
    ...over,
  };
}

function signIn(user: User) {
  session.set({ token: `test-token-${user.id}`, user });
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

/** Makes the case page load `current()`; also answers the audit and user-list calls it makes. */
function serveCase(current: () => CaseDetail) {
  vi.spyOn(api, "getCase").mockImplementation(async () => current());
  vi.spyOn(api, "getAudit").mockResolvedValue([]);
  vi.spyOn(api, "listUsers").mockResolvedValue([ANA, BEN, LEE]);
}

async function openCasePage() {
  renderAt(`/cases/${CASE_ID}`);
  await screen.findByRole("heading", { name: CASE_TITLE }, WAIT);
}

let savedReassign: boolean;

beforeEach(() => {
  savedReassign = config.featureReassign;
  config.featureReassign = true;
  window.sessionStorage.clear();
});

afterEach(() => {
  config.featureReassign = savedReassign;
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
  session.set(null);
  window.sessionStorage.clear();
});

describe("queue", () => {
  it("shows cases in the order the service returns them, not by severity or age", async () => {
    signIn(ANA);
    const row = (id: string, title: string, severity: CaseSummary["severity"], alertTimestamp: string): CaseSummary => ({
      ...makeCase({ id, title, severity, alertTimestamp, status: "New", owner: null }),
    });
    // Deliberately out of severity order (Low, Critical, Medium) and out of age order.
    const served = [
      row("C-9001", "Order test low", "Low", "2026-01-01T00:00:00.000Z"),
      row("C-9002", "Order test critical", "Critical", "2026-01-03T00:00:00.000Z"),
      row("C-9003", "Order test medium", "Medium", "2026-01-02T00:00:00.000Z"),
    ];
    vi.spyOn(api, "getQueue").mockResolvedValue({ cases: served, servedAt: "2026-01-04T00:00:00.000Z" });

    renderAt("/");

    await screen.findByText("Order test low", undefined, WAIT);
    const shown = screen.getAllByTitle(/^Order test /).map((el) => el.textContent);
    expect(shown).toEqual(["Order test low", "Order test critical", "Order test medium"]);
  }, TEST_TIMEOUT);
});

describe("close dialog", () => {
  it("cannot submit without a reason, and submits once with the chosen reason", async () => {
    signIn(ANA);
    serveCase(() => makeCase({ status: "Investigating", owner: ANA }));
    const transition = vi
      .spyOn(api, "transitionCase")
      .mockResolvedValue(makeCase({ status: "Closed", closingReason: "false_positive" }));
    await openCasePage();

    fireEvent.click(await screen.findByRole("button", { name: "Close case…" }, WAIT));
    const dialog = await screen.findByRole("dialog", undefined, WAIT);
    const submit = within(dialog).getByRole("button", { name: "Close case" });

    // No reason chosen: the submit control is disabled and a click does nothing.
    expect(submit).toBeDisabled();
    fireEvent.click(submit);
    expect(transition).not.toHaveBeenCalled();

    fireEvent.click(within(dialog).getByRole("radio", { name: "False positive" }));
    expect(submit).toBeEnabled();
    fireEvent.click(submit);

    await waitFor(() => expect(transition).toHaveBeenCalledTimes(1), WAIT);
    expect(transition).toHaveBeenCalledWith(CASE_ID, "Closed", "false_positive");
  }, TEST_TIMEOUT);
});

describe("claim refused with 409", () => {
  it("shows the server's message and does not show the viewer as the owner", async () => {
    signIn(ANA);
    // The case is unowned when the page loads; Ben's claim lands first, so the server refuses
    // Ana's claim and a later read shows Ben as the owner.
    let owner: User | null = null;
    serveCase(() => makeCase({ status: "New", owner }));
    const claim = vi.spyOn(api, "claimCase").mockImplementation(async () => {
      owner = BEN;
      throw new ApiError(409, "already_claimed", "Case is already owned by Ben Okafor.");
    });
    await openCasePage();
    expect(await screen.findByText("owner: Unclaimed", undefined, WAIT)).toBeInTheDocument();

    fireEvent.click(await screen.findByRole("button", { name: "Claim case" }, WAIT));

    // The message can also appear in a toast, so allow more than one match.
    const messages = await screen.findAllByText(/Case is already owned by Ben Okafor\./, undefined, WAIT);
    expect(messages.length).toBeGreaterThan(0);
    expect(claim).toHaveBeenCalledTimes(1);
    expect(await screen.findByText("owner: Ben Okafor", undefined, WAIT)).toBeInTheDocument();
    expect(screen.queryByText("owner: Ana Alvarez")).toBeNull();
  }, TEST_TIMEOUT);
});

describe("Lead-only actions", () => {
  it.each<Status>(["New", "Investigating"])(
    "are hidden from an Analyst who does not own a %s case",
    async (status) => {
      signIn(ANA);
      serveCase(() => makeCase({ status, owner: BEN }));

      await openCasePage();

      // Positive control: the page has loaded and shows the actions panel for this case.
      expect(await screen.findByText("owner: Ben Okafor", undefined, WAIT)).toBeInTheDocument();
      expect(screen.getByText("Only the owner or a Lead can change status.")).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Start investigating" })).toBeNull();
      expect(screen.queryByRole("button", { name: "Close case…" })).toBeNull();
      expect(screen.queryByRole("button", { name: "Reassign" })).toBeNull();
      expect(screen.queryByText("Reassign to")).toBeNull();
    },
    TEST_TIMEOUT,
  );

  it.each<[Status, string]>([
    ["New", "Start investigating"],
    ["Investigating", "Close case…"],
  ])(
    "are shown to a Lead on a claimed %s case (flag on)",
    async (status, statusButton) => {
      signIn(LEE);
      serveCase(() => makeCase({ status, owner: ANA }));

      await openCasePage();

      expect(await screen.findByRole("button", { name: statusButton }, WAIT)).toBeInTheDocument();
      expect(await screen.findByRole("button", { name: "Reassign" }, WAIT)).toBeInTheDocument();
      expect(screen.getByText("Reassign to")).toBeInTheDocument();
    },
    TEST_TIMEOUT,
  );
});

describe("VITE_FEATURE_REASSIGN: the Reassign control for a Lead on a claimed open case", () => {
  type Vars = { flag?: string; mock?: string };

  /** The value config.ts gives for these variables, read from a fresh copy of the module. */
  async function flagFor(vars: Vars): Promise<boolean> {
    vi.resetModules();
    vi.stubEnv("VITE_FEATURE_REASSIGN", vars.flag);
    vi.stubEnv("VITE_USE_MOCK", vars.mock);
    const fresh = await import("@/services/config");
    return fresh.config.featureReassign;
  }

  it.each<[string, Vars, boolean]>([
    ["true shows it", { flag: "true", mock: "false" }, true],
    ["false hides it", { flag: "false", mock: undefined }, false],
    ["unset follows the mock: on in mock mode", { flag: undefined, mock: undefined }, true],
    ["unset follows the mock: off when the mock is off", { flag: undefined, mock: "false" }, false],
    ["a value that is not true or false follows the mock", { flag: "yes", mock: "false" }, false],
  ])(
    "%s",
    async (_label, vars, visible) => {
      expect(await flagFor(vars)).toBe(visible);
      config.featureReassign = await flagFor(vars);
      vi.unstubAllEnvs();
      signIn(LEE);
      serveCase(() => makeCase({ status: "Investigating", owner: ANA }));

      await openCasePage();

      // Positive control: the Lead's own status button is there, so the page is fully drawn.
      expect(await screen.findByRole("button", { name: "Close case…" }, WAIT)).toBeInTheDocument();
      if (visible) {
        expect(await screen.findByRole("button", { name: "Reassign" }, WAIT)).toBeInTheDocument();
      } else {
        expect(screen.queryByRole("button", { name: "Reassign" })).toBeNull();
        expect(screen.queryByText("Reassign to")).toBeNull();
      }
    },
    TEST_TIMEOUT,
  );
});

describe("401 after login", () => {
  // The token below is one the mock does not know, so its own check answers 401 and clears the
  // session, as the real client does. The page must then return to /login.
  it.each([["/"], ["/cases/C-1001"]])("from %s returns to /login and clears the session", async (path) => {
    session.set({ token: "mock-token-u99", user: ANA });
    expect(window.sessionStorage.getItem("socboard.session")).not.toBeNull();

    renderAt(path);

    expect(await screen.findByRole("heading", { name: "Sign in" }, WAIT)).toBeInTheDocument();
    expect(window.sessionStorage.getItem("socboard.session")).toBeNull();
    expect(session.get()).toBeNull();
  }, TEST_TIMEOUT);
});
