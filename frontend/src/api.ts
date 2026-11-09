// Step F1: everything the web page needs to talk to the Review API.
// The API address comes from .env.local (VITE_API_URL); the token is typed in by the user
// and kept only in this browser tab (sessionStorage), never in the code.

const API_URL = (import.meta.env.VITE_API_URL ?? "").replace(/\/$/, "");
const TOKEN_KEY = "po-token";

export type PoStatus = "POSTED" | "READY" | "NEEDS_REVIEW" | "DUPLICATE" | "REJECTED" | "FAILED";

export interface PoSummary {
  po_id: string;
  status: PoStatus;
  score: number | null;
  s3_key: string;
  created_at: number;            // seconds since 1970
  customer_name: string | null;
  po_number: string | null;
  issue_count: number;
}

export interface LineItem {
  line_number: number;
  part_number: string | null;
  description?: string | null;
  quantity: number | null;
  uom: string | null;
  unit_price: number | null;
  confidence?: number;
  catalog_status?: "found" | "not_found";
}

export interface PoData {
  customer_name?: string | null;
  po_number?: string | null;
  po_date?: string | null;
  requested_ship_date?: string | null;
  ship_to?: string | null;
  bill_to?: string | null;
  buyer_contact?: string | null;
  notes?: string | null;
  line_items: LineItem[];
  field_confidence?: Record<string, number>;
  missing_fields?: string[];
}

export interface Issue {
  severity: "error" | "warning";
  field: string;                 // e.g. "po_number" or "line 2"
  message: string;
}

export interface PoDetail {
  po_id: string;
  status: PoStatus;
  score: number | null;
  s3_key: string;
  created_at: number;
  po?: PoData;
  issues?: Issue[];
  document_url: string;          // 5-minute link to view the original file
  text_method?: string;          // pdf_text or ocr
  model_id?: string;
  ai_cost_usd?: number;
  config_version?: number;
  agent_trace?: { turn: number; tool: string; input?: { part_number?: string }; output?: { found?: boolean } }[];
  history?: { at: number; action: string; detail: string }[];
  erp_response?: { order_number?: number; order_type?: string; lines_created?: number; status?: string };
  reject_reason?: string;
  error?: string;
}

export type Role = "admin" | "csr" | "viewer";

/** S1: the read-only demo token. It is public on purpose: the server lets it look, never change. */
export const MAX_UPLOAD_BYTES = 5 * 1024 * 1024;   // S2: same limit as the server

export const DEMO_TOKEN = import.meta.env.VITE_DEMO_TOKEN ?? "";

export type Severity = "off" | "warning" | "error";
export type Importance = "required" | "important" | "optional";

export interface Config {
  version: number;
  accuracy: Record<string, number>;
  fields: Record<string, Importance>;
  rules: Record<string, Severity>;
  orchestration: {
    ocr_enabled: boolean;
    catalog_lookup_enabled: boolean;
    auto_post_enabled: boolean;
    max_agent_turns: number;
    model_id: string;
    daily_upload_limit: number;
    daily_processing_limit: number;
  };
}

export interface ConfigVersion {
  version: number;
  changed_by: string;
  changed_at: number;
  note: string;
}

export class ApiError extends Error {
  status: number;                // HTTP status code, e.g. 401, 403, 404
  issues?: Issue[];              // sent back when approval is blocked by rule errors
  problems?: string[];           // sent back when settings are refused
  constructor(status: number, message: string, issues?: Issue[], problems?: string[]) {
    super(message);
    this.status = status;
    this.issues = issues;
    this.problems = problems;
  }
}

export const token = {
  get: () => sessionStorage.getItem(TOKEN_KEY),
  set: (value: string) => sessionStorage.setItem(TOKEN_KEY, value.trim()),
  clear: () => sessionStorage.removeItem(TOKEN_KEY),
};

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  if (!API_URL) throw new ApiError(0, "VITE_API_URL is not set in frontend/.env.local");
  const response = await fetch(`${API_URL}${path}`, {
    method,
    headers: {
      Authorization: `Bearer ${token.get() ?? ""}`,
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const { message, issues, problems } = data as { message?: string; issues?: Issue[]; problems?: string[] };
    throw new ApiError(response.status, message ?? response.statusText, issues, problems);
  }
  return data as T;
}

export const api = {
  listPos: () => call<{ items: PoSummary[] }>("GET", "/pos").then((d) => d.items),
  getPo: (id: string) => call<PoDetail>("GET", `/pos/${encodeURIComponent(id)}`),
  updatePo: (id: string, po: PoData) => call<PoDetail>("PUT", `/pos/${encodeURIComponent(id)}`, { po }),
  approvePo: (id: string) => call<PoDetail>("POST", `/pos/${encodeURIComponent(id)}/approve`),
  rejectPo: (id: string, reason: string) =>
    call<PoDetail>("POST", `/pos/${encodeURIComponent(id)}/reject`, { reason }),

  // ---- admin only ----
  getConfig: () => call<Config>("GET", "/config"),
  saveConfig: (config: Omit<Config, "version">, note: string) => call<Config>("PUT", "/config", { config, note }),
  configHistory: () => call<{ current_version: number; items: ConfigVersion[] }>("GET", "/config/history"),
  rollbackConfig: (version: number) => call<Config>("POST", "/config/rollback", { version }),

  /** Which screens to show. The API still enforces the role on every call: this only hides buttons. */
  getRole: () => call<{ role: Role }>("GET", "/me").then((d) => d.role),

  /** Two steps: ask the API for a 5-minute upload form, then send the file straight to S3.
   *  S3 itself refuses files over the size limit or of a different type (S2 cost guard). */
  async uploadPo(file: File): Promise<string> {
    if (file.size > MAX_UPLOAD_BYTES) throw new ApiError(413, "Files must be 5 MB or smaller");
    const link = await call<{ upload_url: string; fields: Record<string, string>; s3_key: string }>(
      "POST", "/uploads", { filename: file.name });
    const form = new FormData();
    Object.entries(link.fields).forEach(([name, value]) => form.append(name, value));
    form.append("file", file);                          // S3 requires the file to be the last field
    const post = await fetch(link.upload_url, { method: "POST", body: form });
    if (!post.ok) throw new ApiError(post.status, "Upload to storage failed (is the file over 5 MB?)");
    return link.s3_key;
  },
};
