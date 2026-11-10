export const config = {
  useMock: import.meta.env.VITE_USE_MOCK !== "false",
  apiBaseUrl: import.meta.env.VITE_API_BASE_URL || "/api",
  pollIntervalMs:
    Number(import.meta.env.VITE_POLL_INTERVAL_MS) > 0
      ? Number(import.meta.env.VITE_POLL_INTERVAL_MS)
      : 3000,
};
