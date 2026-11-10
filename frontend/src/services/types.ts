export type Role = "analyst" | "lead";
export interface User {
  id: string;
  username: string;
  displayName: string;
  role: Role;
}
export type Severity = "Low" | "Medium" | "High" | "Critical";
export type Status = "New" | "Investigating" | "Closed";
export type ClosingReason = "true_positive" | "false_positive" | "duplicate";
export type AssetCriticality = "Low" | "Medium" | "High" | "Critical";

export interface CaseSummary {
  id: string;
  title: string;
  alertType: string;
  source: string;
  asset: string;
  assetCriticality: AssetCriticality;
  severity: Severity;
  status: Status;
  owner: User | null;
  alertTimestamp: string;
  ingestedAt: string;
  closingReason: ClosingReason | null;
}
export interface Note {
  id: string;
  author: User;
  text: string;
  createdAt: string;
}
export interface CaseDetail extends CaseSummary {
  description: string;
  severityRule: string;
  notes: Note[];
}
export type AuditAction = "ingested" | "claimed" | "status_changed" | "note_added" | "reassigned";
export interface AuditEntry {
  id: string;
  at: string;
  actor: User | "system";
  action: AuditAction;
  detail: string;
}
export interface LoginResponse {
  token: string;
  user: User;
}
export interface QueueResponse {
  cases: CaseSummary[];
  servedAt: string;
}

export class ApiError extends Error {
  status: number;
  code: string;
  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
    this.name = "ApiError";
  }
}

/** The contract both the HTTP and mock implementations satisfy. */
export interface SocService {
  login(username: string, password: string): Promise<LoginResponse>;
  getQueue(): Promise<QueueResponse>;
  getClosedCases(): Promise<CaseSummary[]>;
  getCase(id: string): Promise<CaseDetail>;
  claimCase(id: string): Promise<CaseDetail>;
  transitionCase(id: string, to: Status, closingReason?: ClosingReason): Promise<CaseDetail>;
  reassignCase(id: string, userId: string): Promise<CaseDetail>;
  addNote(id: string, text: string): Promise<Note>;
  getAudit(id: string): Promise<AuditEntry[]>;
  listUsers(): Promise<User[]>;
}

export const CLOSING_REASON_LABELS: Record<ClosingReason, string> = {
  true_positive: "True positive",
  false_positive: "False positive",
  duplicate: "Duplicate",
};
