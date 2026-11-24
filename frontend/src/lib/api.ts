// Talks to the Mocksie backend. The API host follows the page's host (localhost <-> localhost), so the login cookie
// (SameSite=Strict) is sent. Override with NEXT_PUBLIC_API_URL only if you know why.

export function apiBase(): string {
  if (process.env.NEXT_PUBLIC_API_URL) return process.env.NEXT_PUBLIC_API_URL;
  if (typeof window === "undefined") return "http://localhost:8000";
  return `${window.location.protocol}//${window.location.hostname}:8000`;
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function parse(res: Response): Promise<unknown> {
  const text = await res.text();
  try {
    return text ? JSON.parse(text) : null;
  } catch {
    return text;
  }
}

function detail(body: unknown, fallback: string): string {
  if (body && typeof body === "object" && "detail" in body) {
    const d = (body as { detail: unknown }).detail;
    if (typeof d === "string") return d;
    if (Array.isArray(d)) return d.map((e) => (e && typeof e === "object" && "msg" in e ? String((e as { msg: unknown }).msg) : String(e))).join("; ");
  }
  return fallback;
}

export async function api<T>(path: string, init: { method?: string; json?: unknown; body?: BodyInit; contentType?: string } = {}): Promise<T> {
  const headers: Record<string, string> = {};
  let body: BodyInit | undefined = init.body;
  if (init.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(init.json);
  } else if (init.contentType) {
    headers["Content-Type"] = init.contentType;
  }
  let res: Response;
  try {
    res = await fetch(apiBase() + path, { method: init.method ?? (body !== undefined ? "POST" : "GET"), headers, body, credentials: "include" });
  } catch {
    throw new ApiError(0, "Cannot reach the Mocksie server. Is it running (python -m backend)?");
  }
  const data = await parse(res);
  if (!res.ok) throw new ApiError(res.status, detail(data, `Request failed (${res.status})`));
  return data as T;
}

export const errorText = (e: unknown): string => (e instanceof Error ? e.message : String(e));

export function mmss(total: number): string {
  const s = Math.max(0, Math.floor(total));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

// ---- shapes returned by the backend (only what the pages use)
export type Message = { role: "interviewer" | "candidate"; text: string };
export type SampleTest = { stdin: string; expected: string };
export type Problem = {
  id: string;
  question: string;
  statement?: string;
  io?: { input?: string; output?: string } | string;
  signature?: string;
  starter_code?: Record<string, string>;
  sample_tests?: SampleTest[];
  schema?: string[];
  sample_rows?: Record<string, unknown[][]>;
  sample_expected?: unknown[][];
  columns?: string[];
};
export type Task = {
  kind: "coding" | "sql";
  problem: Problem;
  timer_s: number;
  remaining_s: number;
  draft: { language: string; code: string } | null;
  languages: string[];
};
export type SessionView = { status: string; state: string; remaining_s: number; messages: Message[]; task: Task | null };
export type StepResult = { events: { type: string; text?: string }[]; status: string };

export type Criterion = {
  id: string; label: string; scale: [number, number]; source: string; weight: number;
  ai_value: number | null; evidence: string[]; confidence: string | null; override: number | null; override_note: string; value: number | null;
};
export type TextField = { label: string; ai_value: string; override: string | null; value: string };
export type ResultsView = {
  form: string; criteria: Criterion[]; overall_score: number | null; overall_assessment: string | null; overall_assessment_ai: string | null;
  recommendation: string; recommendation_ai: string; text_fields: TextField[]; provisional: boolean; missing_inputs: string[]; formula: string;
};
export type QuestionRow = {
  id: string; state: string; question: string; topic: string; skipped: boolean; answer: string; score: number | null; confidence: string;
  needs_review: boolean; evidence: string[]; key_points: string[]; covered: number[]; missed: number[];
};
export type CodingRow = {
  id: string; kind: string; level: number; pct: number; language?: string; tests_passed?: number; tests_total?: number; timeouts?: number;
  compile_error?: string | null; skipped?: boolean; auto_submitted?: boolean; datasets_passed?: number; datasets_total?: number; code?: string;
  pasted_chars?: number;
};
export type Results = {
  id: string; candidate_name: string; status: string; status_label: string; finalized_at: number | null;
  scoring: { state: string | null; message: string };
  view?: ResultsView; options?: { assessment: string[]; recommendation: string[] };
  questions?: QuestionRow[]; coding?: CodingRow[]; integrity?: string[]; warnings?: string[];
  transcript?: { t: number; role: string; text: string }[]; profile_warnings?: string[];
  prep_references?: { label: string }[];
};
export type InterviewRow = { id: string; token: string; status: string; status_label: string; created_at: number; candidate_name: string };
