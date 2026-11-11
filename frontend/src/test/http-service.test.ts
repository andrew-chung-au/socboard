import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { config } from "@/services/config";
import { httpService } from "@/services/httpService";
import { session } from "@/services/session";
import { ApiError, type User } from "@/services/types";

// The ten functions of httpService against the guide's starting prompt
// (_docs/planning/lovable-guidance.md, "Functions -> endpoints"), with fetch stubbed.

const user: User = { id: "u1", username: "analyst-a", displayName: "Ana Alvarez", role: "analyst" };

type FetchInit = { method: string; headers: Record<string, string>; body: string | null };
let fetchMock: ReturnType<typeof vi.fn>;

function reply(status: number, body: unknown) {
  const text = body === undefined ? "" : JSON.stringify(body);
  return { ok: status >= 200 && status < 300, status, text: async () => text };
}

function lastCall(): { url: string; init: FetchInit } {
  const call = fetchMock.mock.calls.at(-1);
  if (!call) throw new Error("fetch was not called");
  return { url: call[0] as string, init: call[1] as FetchInit };
}

beforeEach(() => {
  fetchMock = vi.fn(async () => reply(200, {}));
  vi.stubGlobal("fetch", fetchMock);
  session.set({ token: "tok-1", user });
});

afterEach(() => {
  vi.unstubAllGlobals();
  session.set(null);
});

describe("httpService: method and path of each function", () => {
  const cases: Array<[string, string, string, () => Promise<unknown>]> = [
    ["login", "POST", "/login", () => httpService.login("analyst-a", "pw")],
    ["getQueue", "GET", "/queue", () => httpService.getQueue()],
    ["getClosedCases", "GET", "/cases?status=Closed", () => httpService.getClosedCases()],
    ["getCase", "GET", "/cases/C-1001", () => httpService.getCase("C-1001")],
    ["claimCase", "POST", "/cases/C-1001/claim", () => httpService.claimCase("C-1001")],
    ["transitionCase", "POST", "/cases/C-1001/transition", () => httpService.transitionCase("C-1001", "Investigating")],
    ["reassignCase", "POST", "/cases/C-1001/reassign", () => httpService.reassignCase("C-1001", "u2")],
    ["addNote", "POST", "/cases/C-1001/notes", () => httpService.addNote("C-1001", "hello")],
    ["getAudit", "GET", "/cases/C-1001/audit", () => httpService.getAudit("C-1001")],
    ["listUsers", "GET", "/users", () => httpService.listUsers()],
  ];

  it.each(cases)("%s sends %s %s under the API base", async (_name, method, path, call) => {
    await call();

    const { url, init } = lastCall();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(init.method).toBe(method);
    expect(url).toBe(`${config.apiBaseUrl}${path}`);
  });

  it("covers all ten functions of the service contract", () => {
    expect(cases.map((c) => c[0]).sort()).toEqual(Object.keys(httpService).sort());
    expect(cases).toHaveLength(10);
  });

  it("escapes a case id so it cannot add a path segment or query", async () => {
    await httpService.getCase("C-1/../x?y=1");

    expect(lastCall().url).toBe(`${config.apiBaseUrl}/cases/${encodeURIComponent("C-1/../x?y=1")}`);
  });
});

describe("httpService: token and bodies", () => {
  it("sends the bearer token on every call except login", async () => {
    await httpService.getQueue();
    expect(lastCall().init.headers["Authorization"]).toBe("Bearer tok-1");

    await httpService.login("analyst-a", "pw");
    expect(lastCall().init.headers["Authorization"]).toBeUndefined();
  });

  it("sends the login credentials as a JSON body", async () => {
    await httpService.login("analyst-a", "pw");

    const { init } = lastCall();
    expect(init.headers["Content-Type"]).toBe("application/json");
    expect(JSON.parse(init.body ?? "null")).toEqual({ username: "analyst-a", password: "pw" });
  });

  it("sends transition as { to } and adds closingReason only when given", async () => {
    await httpService.transitionCase("C-1001", "Investigating");
    expect(JSON.parse(lastCall().init.body ?? "null")).toEqual({ to: "Investigating" });

    await httpService.transitionCase("C-1001", "Closed", "duplicate");
    expect(JSON.parse(lastCall().init.body ?? "null")).toEqual({ to: "Closed", closingReason: "duplicate" });
  });

  it("sends reassign as { userId } and a note as { text }", async () => {
    await httpService.reassignCase("C-1001", "u2");
    expect(JSON.parse(lastCall().init.body ?? "null")).toEqual({ userId: "u2" });

    await httpService.addNote("C-1001", "hello");
    expect(JSON.parse(lastCall().init.body ?? "null")).toEqual({ text: "hello" });
  });

  it("sends no body on GET calls and on claim", async () => {
    await httpService.getQueue();
    expect(lastCall().init.body).toBeNull();

    await httpService.claimCase("C-1001");
    expect(lastCall().init.body).toBeNull();
  });

  it("returns the parsed JSON body", async () => {
    fetchMock.mockResolvedValueOnce(reply(200, { cases: [], servedAt: "2026-01-01T00:00:00Z" }));

    await expect(httpService.getQueue()).resolves.toEqual({ cases: [], servedAt: "2026-01-01T00:00:00Z" });
  });
});

describe("httpService: error shapes", () => {
  it("maps { code, message } to an ApiError", async () => {
    fetchMock.mockResolvedValueOnce(reply(409, { code: "already_claimed", message: "Taken." }));

    const err = await httpService.claimCase("C-1001").catch((e: unknown) => e);

    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 409, code: "already_claimed", message: "Taken." });
  });

  it("reads FastAPI's { detail: string } as the message, with a generated code", async () => {
    fetchMock.mockResolvedValueOnce(reply(404, { detail: "Not Found" }));

    await expect(httpService.getCase("C-9")).rejects.toMatchObject({
      status: 404,
      code: "http_404",
      message: "Not Found",
    });
  });

  it("reads FastAPI's { detail: { message } } as the message", async () => {
    fetchMock.mockResolvedValueOnce(reply(422, { detail: { message: "Reason required." } }));

    await expect(httpService.getCase("C-9")).rejects.toMatchObject({
      status: 422,
      code: "http_422",
      message: "Reason required.",
    });
  });

  it("falls back to a generic message when the body is not JSON", async () => {
    fetchMock.mockResolvedValueOnce({ ok: false, status: 500, text: async () => "<html>boom</html>" });

    await expect(httpService.getQueue()).rejects.toMatchObject({
      status: 500,
      code: "http_500",
      message: "Request failed (500)",
    });
  });

  it("maps a network failure to ApiError(0, network_error)", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));

    await expect(httpService.getQueue()).rejects.toMatchObject({ status: 0, code: "network_error" });
  });

  it("clears the session on a 401 from an authenticated call", async () => {
    fetchMock.mockResolvedValueOnce(reply(401, { code: "unauthorized", message: "Expired." }));

    await expect(httpService.getQueue()).rejects.toMatchObject({ status: 401 });

    expect(session.get()).toBeNull();
  });

  it("keeps the session on a 401 from login (wrong password)", async () => {
    fetchMock.mockResolvedValueOnce(reply(401, { code: "invalid_credentials", message: "Nope." }));

    await expect(httpService.login("analyst-a", "bad")).rejects.toMatchObject({
      status: 401,
      code: "invalid_credentials",
    });

    expect(session.get()?.token).toBe("tok-1");
  });
});
