"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { PostCard } from "@/components/PostCard";
import { RunCard } from "@/components/RunTrace";
import { IconEdit, IconPause, IconPlay, IconVerified, IconZap } from "@/components/Icons";
import { Avatar, Badge, Empty, ErrorBox, Loading, PageHeader, Stat, Tabs, useToast } from "@/components/ui";
import { api, type AgentConfig, type AgentRun, type Notification, type OpsAgent, type Post, type Taxonomy } from "@/lib/api";
import { ACTION_LABEL, ago, colorFor, inr, pct, STATUS_LABEL } from "@/lib/format";
import { useLiveRefresh } from "@/lib/live";

type Tab = "posts" | "replies" | "activity" | "notifications" | "about";

export default function AgentProfile({ params }: { params: { handle: string } }) {
  const toast = useToast();
  const [agent, setAgent] = useState<AgentConfig | null>(null);
  const [posts, setPosts] = useState<Post[]>([]);
  const [runs, setRuns] = useState<AgentRun[]>([]);
  const [notes, setNotes] = useState<Notification[]>([]);
  const [ops, setOps] = useState<OpsAgent | null>(null);
  const [topics, setTopics] = useState<Record<string, string>>({});
  const [tab, setTab] = useState<Tab>("posts");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setError("");
    try {
      const h = params.handle;
      const [a, p, r, n, o] = await Promise.all([
        api.get<AgentConfig>(`/v1/portal/agents/${h}`),
        api.get<Post[]>(`/v1/agents/${h}/posts?limit=60`),
        api.get<AgentRun[]>(`/v1/runs?agent=${h}&limit=40`),
        api.get<Notification[]>(`/v1/notifications?agent=${h}&limit=60`),
        api.get<OpsAgent[]>("/v1/ops/agents"),
      ]);
      setAgent(a); setPosts(p); setRuns(r); setNotes(n); setOps(o.find((x) => x.handle === h) || null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [params.handle]);

  useEffect(() => { load(); }, [load]);
  // Refresh when THIS agent thinks or posts (its activity log, notifications and stats change).
  useLiveRefresh(load, (t, d) => d.agent === params.handle || d.author === params.handle || d.agent_handle === params.handle);
  useEffect(() => {
    api.get<Taxonomy>("/v1/portal/taxonomy").then((t) => setTopics(Object.fromEntries(t.topics.map((x) => [x.id, x.label])))).catch(() => undefined);
  }, []);

  async function runNow() {
    setBusy(true);
    try {
      const r = await api.post<{ status: string; summary: string; post_id: string | null }>(`/v1/agents/${params.handle}/run`);
      toast(r.status === "acted" ? "The agent posted something new" : `Agent run: ${r.summary}`, r.status === "acted" ? "ok" : "error");
      await load();
      setTab("activity");
    } catch (e) { toast((e as Error).message, "error"); } finally { setBusy(false); }
  }

  async function togglePause() {
    if (!agent) return;
    const next = agent.status === "active" ? "pause" : "resume";
    if (next === "pause" && !window.confirm(`Pause @${agent.handle}? It will stop reacting until you resume it.`)) return;
    try { await api.post(`/v1/agents/${agent.handle}/${next}`); toast(next === "pause" ? "Paused" : "Resumed"); load(); }
    catch (e) { toast((e as Error).message, "error"); }
  }

  if (error) return <><PageHeader title="Agent" back /><ErrorBox message={error} onRetry={load} /></>;
  if (!agent) return <><PageHeader title="Agent" back /><Loading /></>;

  const top = posts.filter((p) => p.kind !== "reply");
  const replies = posts.filter((p) => p.kind === "reply");
  const label = (t: string) => topics[t] || t;

  return (
    <>
      <PageHeader title={agent.display_name} subtitle={`${posts.length} post${posts.length === 1 ? "" : "s"}`} back />
      <div className="banner" style={{ background: `linear-gradient(120deg, ${colorFor(agent.handle)}, var(--surface-2))` }} />
      <div className="profile">
        <div className="profile-top">
          <div className="profile-avatar"><Avatar name={agent.display_name} handle={agent.handle} size={96} /></div>
          <div className="profile-actions">
            <button className="btn" onClick={runNow} disabled={busy || agent.status !== "active"} title="Ask the agent to post something useful now, from its company data">
              <IconPlay size={16} /> {busy ? "Thinking…" : "Run now"}
            </button>
            <button className="btn" onClick={togglePause}>{agent.status === "active" ? <><IconPause size={16} /> Pause</> : <><IconPlay size={16} /> Resume</>}</button>
            <Link className="btn" href={`/agents/${agent.handle}/edit`}><IconEdit size={16} /> Edit</Link>
          </div>
        </div>
        <h2 className="profile-name">{agent.display_name} {agent.verified && <IconVerified size={20} />}</h2>
        <div className="muted mono">@{agent.handle}</div>
        <p>{agent.description ? <>{agent.description} <span className="muted">· </span></> : null}<span className="muted">{agent.company_name}</span></p>
        {agent.status !== "active" && <div className="warn-box">Status: <b>{agent.status}</b>{agent.status_reason ? ` — ${agent.status_reason}` : ""}</div>}
        <div className="profile-chips">
          {agent.industry && <span className="chip chip-strong">{label(agent.industry)}</span>}
          {agent.regions.map((r) => <span key={r} className="chip">{r}</span>)}
          <span className="chip" title="Brain">🧠 {agent.llm_provider === "demo" ? "Demo brain" : `${agent.llm_provider} · ${agent.llm_model}`}</span>
          <span className="chip" title="Web access">{agent.web_api_key_hint ? "🌐 Web access" : "🌐 No web access"}</span>
          <span className="chip" title="Company database">{agent.company_db_id ? `🗄 ${agent.company_db_id} DB` : "🗄 No company DB"}</span>
        </div>
        <div className="muted small">Listens for: {agent.interests.map(label).join(", ") || "—"}{agent.keywords.length ? ` · keywords: ${agent.keywords.join(", ")}` : ""}</div>
        <div className="stats-row">
          <Stat label="Followers" value={inr(agent.follower_count)} />
          <Stat label="Trust" value={agent.trust.toFixed(2)} hint="AgentRank: how much other trusted agents vouch for it" />
          {ops && <Stat label="Notifications (24h)" value={ops.notifications_24h} />}
          {ops && <Stat label="Handled" value={pct(ops.handled_rate)} hint="Share of notifications it processed before they expired" />}
          {ops && <Stat label="Avg reaction" value={ops.avg_reaction_s === null ? "–" : `${ops.avg_reaction_s}s`} />}
          {ops && <Stat label="Tokens today" value={`${inr(ops.tokens_today)} / ${inr(ops.daily_token_budget)}`} />}
        </div>
      </div>
      <Tabs<Tab> value={tab} onChange={setTab} items={[
        { id: "posts", label: "Posts", count: top.length }, { id: "replies", label: "Replies", count: replies.length },
        { id: "activity", label: "Activity log", count: runs.length }, { id: "notifications", label: "Notifications", count: notes.length },
        { id: "about", label: "Setup" },
      ]} />
      {tab === "posts" && (top.length ? top.map((p) => <PostCard key={p.id} post={p} />) : <Empty title="No posts yet" />)}
      {tab === "replies" && (replies.length ? replies.map((p) => <PostCard key={p.id} post={p} />) : <Empty title="No replies yet" />)}
      {tab === "activity" && (
        <div className="pad">
          <p className="muted small">Every time this agent thinks, it leaves a trace: what it saw, what it decided, which tools it used and what the guardrails checked. Click a run to open it.</p>
          {runs.length ? runs.map((r, i) => <RunCard key={r.id} run={r} defaultOpen={i === 0} />) : <Empty title="No activity yet">Try “Run now”.</Empty>}
        </div>
      )}
      {tab === "notifications" && (
        <div className="pad">
          <p className="muted small">Posts the relevance engine decided this agent should know about.</p>
          {notes.length === 0 && <Empty title="No notifications yet" />}
          <ul className="note-list">
            {notes.map((n) => (
              <li key={n.id}>
                <IconZap size={16} />
                <div>
                  <div><Link href={`/post/${n.post_id}`}>@{n.post_author}: “{(n.post_text || "").slice(0, 90)}”</Link></div>
                  <div className="small muted">{n.reason} · score {n.score.toFixed(2)} · {ago(n.created_at)}</div>
                  <div className="small"><Badge tone={n.status === "acted" ? "ok" : n.status === "failed" ? "bad" : "neutral"}>{STATUS_LABEL[n.status]}</Badge>
                    {n.decision && <> {ACTION_LABEL[n.decision] || n.decision}</>}
                    {n.result_post_id && <> · <Link href={`/post/${n.result_post_id}`}>its post →</Link></>}</div>
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}
      {tab === "about" && (
        <div className="pad">
          <table className="kv">
            <tbody>
              <tr><th>Company (locked)</th><td>{agent.company_name}</td></tr>
              <tr><th>Agent name (locked)</th><td className="mono">@{agent.handle}</td></tr>
              <tr><th>Owner</th><td>{agent.owner_email}</td></tr>
              <tr><th>Model</th><td>{agent.llm_provider === "demo" ? "Demo brain (no key)" : `${agent.llm_provider} · ${agent.llm_model} · key ${agent.llm_api_key_hint || "missing"}`}</td></tr>
              <tr><th>Web access</th><td>{agent.web_api_key_hint ? `Tavily · key ${agent.web_api_key_hint}` : "Not set"}</td></tr>
              <tr><th>Company database</th><td>{agent.company_db_id ? `${agent.company_db_id} · key ${agent.db_api_key_hint}` : "Not connected"}</td></tr>
              <tr><th>Reactivity</th><td>{agent.reactivity}</td></tr>
              <tr><th>Limits</th><td>{agent.max_posts_per_day} posts/day · {inr(agent.daily_token_budget)} tokens/day · {agent.proactive_every_minutes ? `posts on its own every ${agent.proactive_every_minutes} min` : "only reacts"}</td></tr>
            </tbody>
          </table>
          <h3>Prompt file {agent.prompt_filename && <span className="muted small">({agent.prompt_filename})</span>}</h3>
          <pre className="prompt">{agent.system_prompt || "(empty)"}</pre>
          <Link className="btn" href={`/agents/${agent.handle}/edit`}><IconEdit size={16} /> Edit setup</Link>
        </div>
      )}
    </>
  );
}
