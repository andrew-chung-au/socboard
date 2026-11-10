import { config } from "./config";
import { session } from "./session";
import { ApiError } from "./types";

/** The single fetch wrapper: adds the bearer token, maps failures to ApiError, logs out on 401. */
export async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  opts: { auth?: boolean } = {},
): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (opts.auth !== false) {
    const token = session.get()?.token;
    if (token) headers["Authorization"] = `Bearer ${token}`;
  }
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
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = null;
    }
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
    if (res.status === 401 && opts.auth !== false) session.set(null);
    throw err;
  }
  return data as T;
}
