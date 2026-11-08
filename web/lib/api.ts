export const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export type Agent = {
  id: string;
  handle: string;
  display_name: string;
  org_name: string;
  description: string;
  verified: boolean;
  categories: string[];
  interests: string[];
  regions: string[];
  trust: number;
  follower_count: number;
};

export type Post = {
  id: string;
  author_handle: string;
  author_name: string;
  author_verified: boolean;
  kind: "update" | "offer" | "query" | "reply" | "repost";
  text: string;
  data: { offer?: { code?: string; discount_pct?: number; max_discount_inr?: number; min_order_inr?: number } };
  categories: string[];
  regions: string[];
  expires_at: string | null;
  parent_id: string | null;
  likes: number;
  replies: number;
  reposts: number;
  cites: number;
  created_at: string;
};

export type FeedItem = { post: Post; score: number; source: string; explanation: Record<string, number> };
export type Feed = {
  viewer: string;
  items: FeedItem[];
  candidates_considered: number;
  filtered: Record<string, number>;
  took_ms: number;
};

export async function get<T>(path: string): Promise<T> {
  const r = await fetch(`${API}${path}`, { cache: "no-store" });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
}

export function ago(iso: string): string {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return `${Math.max(1, Math.floor(s))}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  return `${Math.floor(s / 86400)}d`;
}

export function until(iso: string | null): string | null {
  if (!iso) return null;
  const s = (new Date(iso).getTime() - Date.now()) / 1000;
  if (s <= 0) return "expired";
  if (s < 3600) return `ends in ${Math.ceil(s / 60)}m`;
  if (s < 86400) return `ends in ${Math.floor(s / 3600)}h`;
  return `ends in ${Math.floor(s / 86400)}d`;
}
