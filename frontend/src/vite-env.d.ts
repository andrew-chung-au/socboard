/// <reference types="vite/client" />

// Declared so the code can read these with dot access. Vite replaces a dot access with a
// literal at build time, which lets the bundler drop the mock when VITE_USE_MOCK is "false".
interface ImportMetaEnv {
  readonly VITE_USE_MOCK?: string;
  readonly VITE_API_BASE_URL?: string;
  readonly VITE_POLL_INTERVAL_MS?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
