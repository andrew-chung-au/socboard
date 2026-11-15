const useMock = import.meta.env.VITE_USE_MOCK !== "false";

// VITE_FEATURE_REASSIGN: with the mock off the Reassign control is never shown, whatever the flag
// says, because `reassignCase` is `x-status: planned` in openapi.yaml and the backend has no such
// route (#131). In mock mode "false" hides it and anything else (unset, empty, "true", another
// word) shows it. The flag only hides the control; the service still refuses a non-Lead.
const reassignFlag = import.meta.env.VITE_FEATURE_REASSIGN;
const featureReassign = useMock && reassignFlag !== "false";

export const config = {
  useMock,
  featureReassign,
  apiBaseUrl: import.meta.env.VITE_API_BASE_URL || "/api",
  pollIntervalMs:
    Number(import.meta.env.VITE_POLL_INTERVAL_MS) > 0
      ? Number(import.meta.env.VITE_POLL_INTERVAL_MS)
      : 3000,
};
