import { config } from "./config";
import { session } from "./session";
import { ApiError } from "./types";

/** The fixed error for a 2xx response whose body is not JSON (#131). */
export const BAD_RESPONSE_CODE = "bad_response";
export const BAD_RESPONSE_MESSAGE = "The server sent a response that could not be read.";

/** The single fetch wrapper: adds the bearer token, maps failures to ApiError, logs out on 401. */
export async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  opts: { auth?: boolean } = {},
): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  // The token this request sends: a 401 for it ends the session only if it is still the current one.
  const sentToken = opts.auth !== false ? session.get()?.token : undefined;
  if (sentToken) headers["Authorization"] = `Bearer ${sentToken}`;
  let res: Response;
  try {
    res = await fetch(`${config.apiBaseUrl}${path}`, {
      method,
      headers,
      body: body === undefined ? null : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "network_error", "Could not reach the server.");
  }
  const text = await res.text();
  let data: unknown = null;
  let unreadable = false;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = null;
      unreadable = true;
    }
  }
  if (res.ok && unreadable) {
    // A success whose body is not JSON is not data. The text itself is never shown or logged.
    throw new ApiError(res.status, BAD_RESPONSE_CODE, BAD_RESPONSE_MESSAGE);
  }
  if (!res.ok) {
    const d = (data ?? {}) as { code?: string; message?: string; detail?: unknown };
    const message =
      d.message ??
      (typeof d.detail === "string"
        ? d.detail
        : typeof (d.detail as { message?: string })?.message === "string"
          ? (d.detail as { message: string }).message
          : `Request failed (${res.status})`);
    const err = new ApiError(res.status, d.code ?? `http_${res.status}`, message);
    // A late 401 from a request sent with an older token must not end a newer session.
    if (res.status === 401 && opts.auth !== false && session.get()?.token === sentToken) {
      session.expire(message);
    }
    throw err;
  }
  return data as T;
}
