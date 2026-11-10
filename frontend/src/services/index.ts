import { httpService } from "./httpService";
import { mockService } from "./mockService";
import type { SocService } from "./types";

// The comparison is written out here, not read from config, so the bundler sees a literal
// when VITE_USE_MOCK is "false" and drops mockService (and its demo accounts and fixtures)
// from a production bundle. `make frontend-build` checks that this worked.
export const api: SocService = import.meta.env.VITE_USE_MOCK !== "false" ? mockService : httpService;
export { config } from "./config";
export { session } from "./session";
export * from "./types";
