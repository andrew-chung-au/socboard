import { request } from "./http";
import type {
  AuditEntry,
  CaseDetail,
  CaseSummary,
  ClosingReason,
  LoginResponse,
  Note,
  QueueResponse,
  SocService,
  Status,
  User,
} from "./types";

const enc = encodeURIComponent;

export const httpService: SocService = {
  login: (username, password) =>
    request<LoginResponse>("POST", "/login", { username, password }, { auth: false }),
  getQueue: () => request<QueueResponse>("GET", "/queue"),
  getClosedCases: () => request<CaseSummary[]>("GET", "/cases?status=Closed"),
  getCase: (id) => request<CaseDetail>("GET", `/cases/${enc(id)}`),
  claimCase: (id) => request<CaseDetail>("POST", `/cases/${enc(id)}/claim`),
  transitionCase: (id: string, to: Status, closingReason?: ClosingReason) =>
    request<CaseDetail>("POST", `/cases/${enc(id)}/transition`, {
      to,
      ...(closingReason ? { closingReason } : {}),
    }),
  reassignCase: (id, userId) =>
    request<CaseDetail>("POST", `/cases/${enc(id)}/reassign`, { userId }),
  addNote: (id, text) => request<Note>("POST", `/cases/${enc(id)}/notes`, { text }),
  getAudit: (id) => request<AuditEntry[]>("GET", `/cases/${enc(id)}/audit`),
  listUsers: () => request<User[]>("GET", "/users"),
};
