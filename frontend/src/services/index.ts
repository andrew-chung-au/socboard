import { config } from "./config";
import { httpService } from "./httpService";
import { mockService } from "./mockService";
import type { SocService } from "./types";

export const api: SocService = config.useMock ? mockService : httpService;
export { config } from "./config";
export { session } from "./session";
export * from "./types";
