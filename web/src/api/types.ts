export type PersonaRole = "dispatcher" | "technician_lead" | "owner";

export const PERSONA_ROLES: PersonaRole[] = [
  "dispatcher",
  "technician_lead",
  "owner",
];

export const PERSONA_LABELS: Record<PersonaRole, string> = {
  dispatcher: "Dispatcher",
  technician_lead: "Technician Lead",
  owner: "Owner",
};

export const PERSONA_DESCRIPTIONS: Record<PersonaRole, string> = {
  dispatcher:
    "Front-desk view. PII columns are redacted; sensitivity-tagged columns are masked.",
  technician_lead:
    "Field-tech view for the assigned service region. Full PII; sensitivity columns masked.",
  owner:
    "Unrestricted view. Real-looking PII plus revenue, costs, and other sensitivity-tagged columns.",
};

export interface AskRequest {
  question: string;
  persona: PersonaRole;
  service_region?: string | null;
}

export interface AskResponse {
  text: string;
  persona: PersonaRole;
  service_region: string | null;
  tools_called: string[];
  guardrail_blocks: number;
  duration_seconds: number;
  session_id: string;
}

export interface PreviewRequest {
  table: string;
  persona: PersonaRole;
  service_region?: string | null;
  limit?: number;
}

export interface PreviewResponse {
  table: string;
  api_path: string;
  template: string | null;
  persona: PersonaRole;
  service_region: string | null;
  limit: number;
  row_count: number;
  rows: Array<Record<string, string | null>>;
  columns: string[];
}

export interface ApiErrorBody {
  error?: string;
  message?: string;
}

export class ApiError extends Error {
  status: number;
  code: string;
  detail: string;

  constructor(status: number, code: string, detail: string) {
    super(`${code}: ${detail || "(no detail)"} [${status}]`);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}
