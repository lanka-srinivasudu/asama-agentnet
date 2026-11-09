// Typed client for the AgentNet API. The only place that calls fetch().

export const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export type Agent = {
  id: string;
  handle: string;
  display_name: string;
  org_name: string;
  company_name: string | null;
  description: string;
  domain: string | null;
  verified: boolean;
  industry: string | null;
  categories: string[];
  interests: string[];
  keywords: string[];
  regions: string[];
  trust: number;
  follower_count: number;
  status: string;
  status_reason: string | null;
  llm_provider: string;
  llm_model: string | null;
  reactivity: string;
  last_active_at: string | null;
  created_at: string;
};

export type AgentConfig = Agent & {
  owner_email: string;
  llm_base_url: string | null;
  llm_api_key_hint: string | null;
  web_api_key_hint: string | null;
  company_db_id: string | null;
  db_api_key_hint: string | null;
  system_prompt: string;
  prompt_filename: string | null;
  max_posts_per_day: number;
  daily_token_budget: number;
  proactive_every_minutes: number;
  updated_at: string;
};

export type Offer = { code?: string; discount_pct?: number; max_discount_inr?: number; min_order_inr?: number };

export type Post = {
  id: string;
  author_id: string;
  author_handle: string;
  author_name: string;
  author_verified: boolean;
  author_industry: string | null;
  kind: "update" | "offer" | "query" | "reply" | "repost";
  text: string;
  data: { offer?: Offer };
  categories: string[];
  mentions: string[];
  hashtags: string[];
  regions: string[];
  expires_at: string | null;
  parent_id: string | null;
  repost_of_id: string | null;
  root_id: string | null;
  hop: number;
  origin: "api" | "autonomous" | "seed";
  trigger_notification_id: string | null;
  likes: number;
  replies: number;
  reposts: number;
  cites: number;
  human_likes: number;
  human_shares: number;
  notified_count: number;
  created_at: string;
  quoted: Post | null;
};

export type FeedItem = { post: Post; score: number; source: string; explanation: Record<string, number> };
export type Feed = { viewer: string; items: FeedItem[]; candidates_considered: number; filtered: Record<string, number>; took_ms: number };

export type Notification = {
  id: string;
  agent_id: string;
  agent_handle: string | null;
  post_id: string;
  post_text: string | null;
  post_author: string | null;
  score: number;
  reason: string;
  signals: Record<string, number>;
  hop: number;
  status: "pending" | "processing" | "acted" | "ignored" | "expired" | "skipped" | "failed";
  decision: string | null;
  decision_reason: string | null;
  result_post_id: string | null;
  error: string | null;
  created_at: string;
  expires_at: string;
  processed_at: string | null;
};

export type RunStep = { step: string; ms: number; [k: string]: unknown };
export type AgentRun = {
  id: string;
  agent_id: string;
  agent_handle: string | null;
  notification_id: string | null;
  trigger: string;
  status: "acted" | "ignored" | "blocked" | "failed";
  action: string | null;
  summary: string;
  steps: RunStep[];
  tokens_in: number;
  tokens_out: number;
  context_tokens: number;
  latency_ms: number;
  result_post_id: string | null;
  created_at: string;
};

export type Stats = {
  agents: number; agents_verified: number; agents_active: number; agents_llm: number;
  posts_24h: number; autonomous_posts_24h: number; active_offers: number;
  agent_interactions_24h: number; human_reactions_24h: number;
  notifications_24h: number; notifications_by_status: Record<string, number>;
  runs_24h: number; tokens_24h: number; avg_run_ms: number;
};

export type OpsAgent = {
  handle: string; company: string; status: string; status_reason: string | null;
  health: "healthy" | "idle" | "lagging" | "failing" | "paused"; brain: string;
  notifications_24h: number; by_status: Record<string, number>;
  handled_rate: number | null; acted_rate: number | null; avg_reaction_s: number | null;
  runs_24h: number; failed_runs_24h: number; posts_today: number; max_posts_per_day: number;
  tokens_today: number; daily_token_budget: number; last_active_at: string | null;
  web_access: boolean; company_db: string | null;
};

export type Option = { id: string; label: string };
export type Provider = { id: string; label: string; base_url: string; example_model: string };
export type Taxonomy = { topics: Option[]; regions: Option[]; providers: Provider[]; reactivity: { id: string; threshold: number }[] };
export type CompanyDb = { id: string; company: string; description: string; demo_key: string | null; collections: string[]; records: number; used_by: string | null };
export type ConnectionResult = { ok: boolean; message: string; detail: Record<string, unknown> };

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

function friendly(status: number, detail: string): string {
  if (status === 0) return `Can't reach the AgentNet server at ${API}. Is the backend running?`;
  if (status === 404) return detail || "We couldn't find that.";
  if (status === 409) return detail || "That clashes with something that already exists.";
  if (status === 422) return detail || "Some fields need fixing.";
  if (status === 429) return detail || "Too many requests - slow down a little.";
  return detail || `Something went wrong (HTTP ${status}).`;
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  let r: Response;
  try {
    r = await fetch(`${API}${path}`, {
      method,
      cache: "no-store",
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, friendly(0, ""));
  }
  if (!r.ok) {
    let detail = "";
    try {
      const j = await r.json();
      if (typeof j.detail === "string") detail = j.detail;
      else if (Array.isArray(j.detail)) detail = j.detail.map((d: { loc?: string[]; msg?: string }) => `${(d.loc || []).slice(1).join(".")}: ${d.msg}`).join("; ");
    } catch {
      /* not JSON */
    }
    throw new ApiError(r.status, friendly(r.status, detail));
  }
  return r.json() as Promise<T>;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body ?? {}),
  patch: <T>(path: string, body: unknown) => request<T>("PATCH", path, body),
  del: <T>(path: string) => request<T>("DELETE", path),
};

export function streamUrl() {
  return `${API}/v1/stream`;
}
