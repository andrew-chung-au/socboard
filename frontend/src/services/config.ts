const env = import.meta.env;

export const config = {
  useMock: (env["VITE_USE_MOCK"] ?? "true") !== "false",
  apiBaseUrl: (env["VITE_API_BASE_URL"] as string | undefined) || "/api",
  pollIntervalMs: Number(env["VITE_POLL_INTERVAL_MS"]) > 0 ? Number(env["VITE_POLL_INTERVAL_MS"]) : 3000,
};
