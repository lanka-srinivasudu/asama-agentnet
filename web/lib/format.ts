export function ago(iso: string | null | undefined): string {
  if (!iso) return "never";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return `${Math.max(1, Math.floor(s))}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  return `${Math.floor(s / 86400)}d`;
}

export function exact(iso: string): string {
  return new Date(iso).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" });
}

export function until(iso: string | null): { text: string; tone: "ok" | "soon" | "over" } | null {
  if (!iso) return null;
  const h = (new Date(iso).getTime() - Date.now()) / 3.6e6;
  if (h <= 0) return { text: "Expired", tone: "over" };
  if (h < 1) return { text: `Ends in ${Math.ceil(h * 60)}m`, tone: "soon" };
  if (h < 6) return { text: `Ends in ${Math.floor(h)}h`, tone: "soon" };
  if (h < 48) return { text: `Ends in ${Math.floor(h)}h`, tone: "ok" };
  return { text: `Ends in ${Math.floor(h / 24)}d`, tone: "ok" };
}

export function inr(n: number): string {
  return n.toLocaleString("en-IN");
}

export function pct(n: number | null | undefined): string {
  return n === null || n === undefined ? "–" : `${Math.round(n * 100)}%`;
}

const PALETTE = ["#e8590c", "#5f3dc4", "#0b7285", "#2b8a3e", "#c2255c", "#1971c2", "#e67700", "#495057"];

export function colorFor(key: string): string {
  let h = 0;
  for (const ch of key) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return PALETTE[h % PALETTE.length];
}

export function initials(name: string): string {
  const parts = name.replace(/[^A-Za-z0-9 ]/g, " ").split(" ").filter(Boolean);
  return ((parts[0]?.[0] || "?") + (parts[1]?.[0] || "")).toUpperCase();
}

export const KIND_LABEL: Record<string, string> = {
  update: "Update", offer: "Offer", query: "Question", reply: "Reply", repost: "Quote",
};

export const STATUS_LABEL: Record<string, string> = {
  pending: "Waiting", processing: "Thinking…", acted: "Acted", ignored: "Ignored",
  expired: "Expired (TTL)", skipped: "Skipped", failed: "Failed",
};

export const ACTION_LABEL: Record<string, string> = {
  ignore: "ignored it", like: "liked it", reply: "replied", quote: "quoted it", post: "wrote a new post",
};
