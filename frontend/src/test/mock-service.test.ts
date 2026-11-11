import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AuditEntry, ClosingReason, User } from "@/services/types";

/** A deep copy that does not depend on the test environment providing structuredClone. */
const snap = <T>(v: T): T => JSON.parse(JSON.stringify(v)) as T;

// The mock plays the server: these tests call it directly (no screens), so the rules hold
// whether or not a control is shown. Seed cases used (from mockService.ts):
//   C-1001 Investigating, owner analyst-a    C-1002 New, unowned    C-1003 New, owner analyst-b
//   C-1004 Investigating, owner lead         C-1005 New, unowned    C-1009 New, owner analyst-a
//   C-1011 Investigating, owner analyst-a    C-1013 Closed, owner analyst-b
// Each test starts from fresh module state, so no test depends on another.

type Mock = (typeof import("@/services/mockService"))["mockService"];
type SessionApi = (typeof import("@/services/session"))["session"];

interface Env {
  mock: Mock;
  session: SessionApi;
  /** Sign in through the mock's own login(), as the screens do. */
  loginAs: (username: "analyst-a" | "analyst-b" | "lead") => Promise<User>;
  /** Read a case's audit trail as the Lead, then restore whoever was signed in. */
  peekAudit: (id: string) => Promise<AuditEntry[]>;
}

const PASSWORDS = { "analyst-a": "analyst-a-pass", "analyst-b": "analyst-b-pass", lead: "lead-pass" } as const;

let env: Env;

async function freshEnv(): Promise<Env> {
  vi.resetModules();
  const { mockService: mock } = await import("@/services/mockService");
  const { session } = await import("@/services/session");
  session.set(null);

  const loginAs: Env["loginAs"] = async (username) => {
    const res = await mock.login(username, PASSWORDS[username]);
    session.set({ token: res.token, user: res.user });
    return res.user;
  };
  const peekAudit: Env["peekAudit"] = async (id) => {
    const before = session.get();
    await loginAs("lead");
    const entries = await mock.getAudit(id);
    session.set(before);
    return entries;
  };
  return { mock, session, loginAs, peekAudit };
}

beforeEach(async () => {
  env = await freshEnv();
});

describe("mock service: refused actions return ApiError with status and code", () => {
  type Actor = "analyst-a" | "analyst-b" | "lead" | "no token" | "unknown token";
  interface Row {
    name: string;
    as: Actor;
    caseId: string;
    run: (m: Mock) => Promise<unknown>;
    status: number;
    code: string;
  }

  const rows: Row[] = [
    // 409: claims
    { name: "second claim by another analyst", as: "analyst-b", caseId: "C-1003", run: (m) => m.claimCase("C-1003"), status: 409, code: "already_claimed" },
    { name: "claim by the case's own owner", as: "analyst-a", caseId: "C-1001", run: (m) => m.claimCase("C-1001"), status: 409, code: "already_claimed" },
    { name: "claim of a Closed case", as: "analyst-a", caseId: "C-1013", run: (m) => m.claimCase("C-1013"), status: 409, code: "case_closed" },
    // 409: transitions
    { name: "New to Closed", as: "analyst-a", caseId: "C-1009", run: (m) => m.transitionCase("C-1009", "Closed", "duplicate"), status: 409, code: "invalid_transition" },
    { name: "Investigating to New", as: "analyst-a", caseId: "C-1001", run: (m) => m.transitionCase("C-1001", "New"), status: 409, code: "invalid_transition" },
    { name: "Closed to Investigating (Lead)", as: "lead", caseId: "C-1013", run: (m) => m.transitionCase("C-1013", "Investigating"), status: 409, code: "invalid_transition" },
    { name: "Closed to New (Lead)", as: "lead", caseId: "C-1013", run: (m) => m.transitionCase("C-1013", "New"), status: 409, code: "invalid_transition" },
    { name: "Closed to Closed (Lead)", as: "lead", caseId: "C-1013", run: (m) => m.transitionCase("C-1013", "Closed", "duplicate"), status: 409, code: "invalid_transition" },
    // 409: note on a Closed case
    { name: "note on a Closed case", as: "analyst-a", caseId: "C-1013", run: (m) => m.addNote("C-1013", "late note"), status: 409, code: "case_closed" },
    // 422
    { name: "closing without a reason", as: "analyst-a", caseId: "C-1011", run: (m) => m.transitionCase("C-1011", "Closed"), status: 422, code: "reason_required" },
    { name: "empty note", as: "analyst-a", caseId: "C-1001", run: (m) => m.addNote("C-1001", ""), status: 422, code: "empty_note" },
    { name: "whitespace-only note", as: "analyst-a", caseId: "C-1001", run: (m) => m.addNote("C-1001", " \n\t  "), status: 422, code: "empty_note" },
    // 403
    { name: "status change by a non-owner analyst", as: "analyst-b", caseId: "C-1001", run: (m) => m.transitionCase("C-1001", "Closed", "duplicate"), status: 403, code: "forbidden" },
    { name: "status change on an unowned case by an analyst", as: "analyst-b", caseId: "C-1002", run: (m) => m.transitionCase("C-1002", "Investigating"), status: 403, code: "forbidden" },
    { name: "reassign by a non-Lead who does not own the case", as: "analyst-b", caseId: "C-1001", run: (m) => m.reassignCase("C-1001", "u2"), status: 403, code: "forbidden" },
    { name: "reassign by a non-Lead who owns the case", as: "analyst-a", caseId: "C-1001", run: (m) => m.reassignCase("C-1001", "u2"), status: 403, code: "forbidden" },
    // 401
    { name: "claim with no token", as: "no token", caseId: "C-1002", run: (m) => m.claimCase("C-1002"), status: 401, code: "unauthorized" },
    { name: "transition with no token", as: "no token", caseId: "C-1001", run: (m) => m.transitionCase("C-1001", "Closed", "duplicate"), status: 401, code: "unauthorized" },
    { name: "note with no token", as: "no token", caseId: "C-1001", run: (m) => m.addNote("C-1001", "hi"), status: 401, code: "unauthorized" },
    { name: "reassign with no token", as: "no token", caseId: "C-1001", run: (m) => m.reassignCase("C-1001", "u2"), status: 401, code: "unauthorized" },
    { name: "claim with the unknown token mock-token-zz", as: "unknown token", caseId: "C-1002", run: (m) => m.claimCase("C-1002"), status: 401, code: "unauthorized" },
    { name: "note with the unknown token mock-token-zz", as: "unknown token", caseId: "C-1001", run: (m) => m.addNote("C-1001", "hi"), status: 401, code: "unauthorized" },
    // 404 and the reassign refusals beyond the table
    { name: "unknown case", as: "analyst-a", caseId: "C-1001", run: (m) => m.claimCase("C-9999"), status: 404, code: "not_found" },
    { name: "reassign of an unclaimed case (Lead)", as: "lead", caseId: "C-1002", run: (m) => m.reassignCase("C-1002", "u1"), status: 409, code: "not_claimed" },
    { name: "reassign of a Closed case (Lead)", as: "lead", caseId: "C-1013", run: (m) => m.reassignCase("C-1013", "u1"), status: 409, code: "case_closed" },
    { name: "reassign to the current owner (Lead)", as: "lead", caseId: "C-1001", run: (m) => m.reassignCase("C-1001", "u1"), status: 409, code: "same_owner" },
    { name: "reassign to an unknown user (Lead)", as: "lead", caseId: "C-1001", run: (m) => m.reassignCase("C-1001", "u99"), status: 404, code: "user_not_found" },
  ];

  async function signIn(as: Actor) {
    if (as === "no token") env.session.set(null);
    else if (as === "unknown token")
      env.session.set({
        token: "mock-token-zz",
        user: { id: "zz", username: "zz", displayName: "Nobody", role: "lead" },
      });
    else await env.loginAs(as);
  }

  it.each(rows)("$name -> $status $code, and appends no audit entry", async (row) => {
    const before = await env.peekAudit(row.caseId);
    await signIn(row.as);

    const err = await row.run(env.mock).then(
      () => null,
      (e: unknown) => e,
    );

    expect(err).toMatchObject({ name: "ApiError", status: row.status, code: row.code });
    expect(await env.peekAudit(row.caseId)).toEqual(before);
  });

  it("a refused action on one case changes no other case's audit trail either", async () => {
    const others = await Promise.all(["C-1002", "C-1003", "C-1009"].map((id) => env.peekAudit(id)));
    await env.loginAs("analyst-b");

    await expect(env.mock.transitionCase("C-1001", "Closed", "duplicate")).rejects.toMatchObject({ status: 403 });

    expect(await Promise.all(["C-1002", "C-1003", "C-1009"].map((id) => env.peekAudit(id)))).toEqual(others);
  });

  it("an unknown token also clears the stored session (the screens go to login)", async () => {
    await signIn("unknown token");

    await expect(env.mock.getQueue()).rejects.toMatchObject({ status: 401 });

    expect(env.session.get()).toBeNull();
  });

  it.each([
    ["getQueue", (m: Mock) => m.getQueue()],
    ["getClosedCases", (m: Mock) => m.getClosedCases()],
    ["getCase", (m: Mock) => m.getCase("C-1001")],
    ["claimCase", (m: Mock) => m.claimCase("C-1002")],
    ["transitionCase", (m: Mock) => m.transitionCase("C-1001", "Closed", "duplicate")],
    ["reassignCase", (m: Mock) => m.reassignCase("C-1001", "u2")],
    ["addNote", (m: Mock) => m.addNote("C-1001", "hi")],
    ["getAudit", (m: Mock) => m.getAudit("C-1001")],
    ["listUsers", (m: Mock) => m.listUsers()],
  ])("%s with no token is 401 unauthorized", async (_name, run) => {
    env.session.set(null);

    await expect(run(env.mock)).rejects.toMatchObject({ name: "ApiError", status: 401, code: "unauthorized" });
  });

  it("login with a wrong password is 401 invalid_credentials", async () => {
    await expect(env.mock.login("analyst-a", "wrong")).rejects.toMatchObject({
      status: 401,
      code: "invalid_credentials",
    });
  });
});

describe("mock service: the order in which rules are checked", () => {
  const status = (n: number, code?: string) => (code ? { status: n, code } : { status: n });

  it("401 beats 404: no token on a case that does not exist", async () => {
    env.session.set(null);

    await expect(env.mock.getCase("C-9999")).rejects.toMatchObject(status(401, "unauthorized"));
    await expect(env.mock.transitionCase("C-9999", "Closed")).rejects.toMatchObject(status(401, "unauthorized"));
  });

  it("404 beats 403: a non-Lead on a case that does not exist", async () => {
    await env.loginAs("analyst-a");

    await expect(env.mock.reassignCase("C-9999", "u2")).rejects.toMatchObject(status(404, "not_found"));
    await expect(env.mock.transitionCase("C-9999", "Closed")).rejects.toMatchObject(status(404, "not_found"));
  });

  it("403 beats 409: an analyst who does not own a New case tries to close it", async () => {
    await env.loginAs("analyst-b");

    // New to Closed is also an illegal move (409), but permission is checked first.
    await expect(env.mock.transitionCase("C-1002", "Closed", "duplicate")).rejects.toMatchObject(
      status(403, "forbidden"),
    );
  });

  it("403 beats 409: a non-Lead reassigns an unclaimed case", async () => {
    await env.loginAs("analyst-a");

    await expect(env.mock.reassignCase("C-1002", "u2")).rejects.toMatchObject(status(403, "forbidden"));
  });

  it("403 beats 409: a non-owner changes a Closed case", async () => {
    await env.loginAs("analyst-a");

    await expect(env.mock.transitionCase("C-1013", "Investigating")).rejects.toMatchObject(status(403, "forbidden"));
  });

  it("409 beats 422: the owner closes a New case with no reason", async () => {
    await env.loginAs("analyst-a");

    await expect(env.mock.transitionCase("C-1009", "Closed")).rejects.toMatchObject(
      status(409, "invalid_transition"),
    );
  });

  it("409 beats 422: an empty note on a Closed case", async () => {
    await env.loginAs("analyst-a");

    await expect(env.mock.addNote("C-1013", "  ")).rejects.toMatchObject(status(409, "case_closed"));
  });

  it("422: the owner closes an Investigating case with no reason", async () => {
    await env.loginAs("analyst-a");

    await expect(env.mock.transitionCase("C-1011", "Closed")).rejects.toMatchObject(status(422, "reason_required"));
  });

  it("reassign refusals run 403, then not_claimed, then case_closed, then user_not_found, then same_owner", async () => {
    await env.loginAs("analyst-a");
    await expect(env.mock.reassignCase("C-1002", "u99")).rejects.toMatchObject(status(403, "forbidden"));

    await env.loginAs("lead");
    await expect(env.mock.reassignCase("C-1002", "u99")).rejects.toMatchObject(status(409, "not_claimed"));
    await expect(env.mock.reassignCase("C-1013", "u99")).rejects.toMatchObject(status(409, "case_closed"));
    await expect(env.mock.reassignCase("C-1001", "u99")).rejects.toMatchObject(status(404, "user_not_found"));
    await expect(env.mock.reassignCase("C-1001", "u1")).rejects.toMatchObject(status(409, "same_owner"));
  });
});

describe("mock service: every successful action appends exactly one matching audit entry", () => {
  /** Run an action and return the entries it added (the trail must only grow at the end). */
  async function added(id: string, act: () => Promise<unknown>) {
    const before = await env.peekAudit(id);
    await act();
    const after = await env.peekAudit(id);
    expect(after).toHaveLength(before.length + 1);
    expect(after.slice(0, before.length)).toEqual(before);
    return after[after.length - 1] as AuditEntry;
  }

  it("claim -> one `claimed` entry by the claimer", async () => {
    const me = await env.loginAs("analyst-b");

    const entry = await added("C-1005", () => env.mock.claimCase("C-1005"));

    expect(entry.action).toBe("claimed");
    expect(entry.actor).toEqual(me);
  });

  it("start investigating -> one `status_changed` entry with from and to", async () => {
    const me = await env.loginAs("analyst-a");

    const entry = await added("C-1009", () => env.mock.transitionCase("C-1009", "Investigating"));

    expect(entry.action).toBe("status_changed");
    expect(entry.actor).toEqual(me);
    expect(entry.detail).toContain("New");
    expect(entry.detail).toContain("Investigating");
  });

  it.each<[ClosingReason, string]>([
    ["true_positive", "True positive"],
    ["false_positive", "False positive"],
    ["duplicate", "Duplicate"],
  ])("close with %s -> one `status_changed` entry with from, to and the reason", async (reason, label) => {
    await env.loginAs("analyst-a");

    const entry = await added("C-1011", () => env.mock.transitionCase("C-1011", "Closed", reason));

    expect(entry.action).toBe("status_changed");
    expect(entry.detail).toContain("Investigating");
    expect(entry.detail).toContain("Closed");
    expect(entry.detail).toContain(label);
  });

  it("note -> one `note_added` entry by the author", async () => {
    const me = await env.loginAs("analyst-b");

    const entry = await added("C-1001", () => env.mock.addNote("C-1001", "Checked the logs."));

    expect(entry.action).toBe("note_added");
    expect(entry.actor).toEqual(me);
  });

  it("reassign -> one `reassigned` entry by the Lead naming the old and new owner", async () => {
    const lead = await env.loginAs("lead");

    const entry = await added("C-1001", () => env.mock.reassignCase("C-1001", "u2"));

    expect(entry.action).toBe("reassigned");
    expect(entry.actor).toEqual(lead);
    expect(entry.detail).toContain("Ana Alvarez");
    expect(entry.detail).toContain("Ben Okafor");
  });

  it("a successful action writes to its own case's trail only", async () => {
    const other = await env.peekAudit("C-1003");
    await env.loginAs("analyst-a");

    await env.mock.addNote("C-1001", "Only for C-1001.");

    expect(await env.peekAudit("C-1003")).toEqual(other);
  });
});

describe("mock service: the audit trail is append-only", () => {
  it("changing an array returned by getAudit does not change the next call", async () => {
    await env.loginAs("lead");
    const first = await env.mock.getAudit("C-1001");
    const snapshot = snap(first);

    first.push({ id: "x", at: "2000-01-01T00:00:00Z", actor: "system", action: "ingested", detail: "forged" });
    first[0]!.detail = "edited";
    first[0]!.action = "reassigned";
    first.shift();
    first.length = 0;

    expect(await env.mock.getAudit("C-1001")).toEqual(snapshot);
  });

  it("changing an actor object returned by getAudit does not change the next call", async () => {
    await env.loginAs("lead");
    const snapshot = snap(await env.mock.getAudit("C-1001"));
    const first = await env.mock.getAudit("C-1001");
    const claimed = first.find((e) => e.action === "claimed");
    if (!claimed || claimed.actor === "system") throw new Error("seed case C-1001 should have a claimed entry");

    claimed.actor.displayName = "Mallory";

    expect(await env.mock.getAudit("C-1001")).toEqual(snapshot);
  });

  it("entries already read are unchanged after further actions", async () => {
    await env.loginAs("lead");
    const early = await env.mock.getAudit("C-1001");
    const snapshot = snap(early);

    await env.mock.addNote("C-1001", "One.");
    await env.mock.reassignCase("C-1001", "u2");
    await env.loginAs("analyst-b");
    await env.mock.transitionCase("C-1001", "Closed", "duplicate");

    expect(early).toEqual(snapshot);
    const later = await env.peekAudit("C-1001");
    expect(later.slice(0, snapshot.length)).toEqual(snapshot);
    expect(later).toHaveLength(snapshot.length + 3);
  });

  it("entries are returned oldest first and each action's entry comes last", async () => {
    await env.loginAs("analyst-a");
    await env.mock.addNote("C-1001", "First.");
    await env.mock.addNote("C-1001", "Second.");

    const trail = await env.peekAudit("C-1001");

    const times = trail.map((e) => e.at);
    expect(times).toEqual([...times].sort());
    expect(trail.slice(-2).map((e) => e.action)).toEqual(["note_added", "note_added"]);
  });
});

describe("mock service: other rules the screens rely on", () => {
  it("claiming sets the owner only and leaves the status alone", async () => {
    await env.loginAs("analyst-b");

    const c = await env.mock.claimCase("C-1005");

    expect(c.owner?.username).toBe("analyst-b");
    expect(c.status).toBe("New");
  });

  it("closing records the reason on the case", async () => {
    await env.loginAs("analyst-a");

    const c = await env.mock.transitionCase("C-1011", "Closed", "false_positive");

    expect(c.status).toBe("Closed");
    expect(c.closingReason).toBe("false_positive");
  });

  it("a Lead who does not own a case can change its status", async () => {
    await env.loginAs("lead");

    const c = await env.mock.transitionCase("C-1001", "Closed", "duplicate");

    expect(c.status).toBe("Closed");
  });

  it("reassign moves ownership to the chosen user", async () => {
    await env.loginAs("lead");

    const c = await env.mock.reassignCase("C-1001", "u2");

    expect(c.owner?.id).toBe("u2");
  });

  it("a note keeps its text exactly as sent and shows up on the case", async () => {
    await env.loginAs("analyst-a");
    const text = "  spaced <b>text</b>\nline two  ";

    const note = await env.mock.addNote("C-1001", text);
    const c = await env.mock.getCase("C-1001");

    expect(note.text).toBe(text);
    expect(c.notes.map((n) => n.text)).toContain(text);
  });

  it("the queue holds no Closed case and the closed list holds only Closed cases", async () => {
    await env.loginAs("analyst-a");

    const queue = await env.mock.getQueue();
    const closed = await env.mock.getClosedCases();

    expect(queue.cases.every((c) => c.status !== "Closed")).toBe(true);
    expect(closed.length).toBeGreaterThan(0);
    expect(closed.every((c) => c.status === "Closed")).toBe(true);
  });

  it("a closingReason sent with a move that is not a close is accepted and ignored (left as is; see deviations)", async () => {
    await env.loginAs("analyst-a");

    const c = await env.mock.transitionCase("C-1009", "Investigating", "duplicate");

    expect(c.status).toBe("Investigating");
    expect(c.closingReason).toBeNull();
  });
});
