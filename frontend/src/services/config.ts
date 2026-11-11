const useMock = import.meta.env.VITE_USE_MOCK !== "false";

// VITE_FEATURE_REASSIGN: "true" shows the Reassign control, "false" hides it, anything else
// (unset, empty, another word) follows the mock: on in mock mode, off otherwise. The flag only
// hides the control; the service (and later the backend) still refuses a reassign by a non-Lead.
const reassignFlag = import.meta.env.VITE_FEATURE_REASSIGN;
const featureReassign = reassignFlag === "true" ? true : reassignFlag === "false" ? false : useMock;

export const config = {
  useMock,
  featureReassign,
  apiBaseUrl: import.meta.env.VITE_API_BASE_URL || "/api",
  pollIntervalMs:
    Number(import.meta.env.VITE_POLL_INTERVAL_MS) > 0
      ? Number(import.meta.env.VITE_POLL_INTERVAL_MS)
      : 3000,
};
