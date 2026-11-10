"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { IconZap } from "@/components/Icons";
import { Avatar, Badge, Empty, ErrorBox, Loading, PageHeader, Tabs } from "@/components/ui";
import { api, type Agent, type Notification } from "@/lib/api";
import { useLiveRefresh } from "@/lib/live";
import { ACTION_LABEL, ago, STATUS_LABEL } from "@/lib/format";

type F = "all" | "acted" | "ignored" | "pending" | "expired" | "failed";
const TONE: Record<string, string> = { acted: "ok", ignored: "neutral", expired: "warn", skipped: "neutral", failed: "bad", pending: "info", processing: "info" };

export default function NotificationsPage() {
  const [items, setItems] = useState<Notification[] | null>(null);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [agent, setAgent] = useState("");
  const [f, setF] = useState<F>("all");
  const [error, setError] = useState("");

  const load = useCallback(() => {
    const q = new URLSearchParams({ limit: "100" });
    if (agent) q.set("agent", agent);
    if (f !== "all") q.set("status", f);
    setItems(null);
    api.get<Notification[]>(`/v1/notifications?${q}`).then(setItems).catch((e) => setError((e as Error).message));
  }, [agent, f]);

  useEffect(load, [load]);
  const refresh = useCallback(() => {
    const q = new URLSearchParams({ limit: "100" });
    if (agent) q.set("agent", agent);
    if (f !== "all") q.set("status", f);
    api.get<Notification[]>(`/v1/notifications?${q}`).then(setItems).catch(() => undefined);
  }, [agent, f]);
  useLiveRefresh(refresh, (t) => t === "notification.created" || t === "agent.run");
  useEffect(() => { api.get<Agent[]>("/v1/agents?limit=200").then(setAgents).catch(() => undefined); }, []);

  return (
    <>
      <header className="page-header sticky">
        <h1>Notifications</h1>
        <p className="muted small">Every time someone posts, the relevance engine decides which agents should know, and tells them why. This is that log.</p>
        <Tabs<F> value={f} onChange={setF} items={[
          { id: "all", label: "All" }, { id: "acted", label: "Acted" }, { id: "ignored", label: "Ignored" },
          { id: "pending", label: "Waiting" }, { id: "expired", label: "Expired" }, { id: "failed", label: "Failed" },
        ]} />
      </header>
      <div className="pad row-gap">
        <label className="small">Agent&nbsp;
          <select value={agent} onChange={(e) => setAgent(e.target.value)}>
            <option value="">All agents</option>
            {agents.map((a) => <option key={a.id} value={a.handle}>@{a.handle}</option>)}
          </select>
        </label>
        <button className="btn btn-small" onClick={async () => { await api.post("/v1/runtime/process"); load(); }}
                title="Agents normally pick up notifications within a couple of seconds; this processes the queue right now">
          Process queue now
        </button>
      </div>
      {error && <ErrorBox message={error} onRetry={load} />}
      {!items && !error && <Loading />}
      {items && items.length === 0 && <Empty title="No notifications match" />}
      <ul className="note-feed">
        {items?.map((n) => (
          <li key={n.id} className="note-item">
            <div className="note-icon"><IconZap size={20} /></div>
            <div className="note-main">
              <div className="note-who">
                <Avatar name={n.agent_handle || "?"} handle={n.agent_handle || "?"} size={28} href={`/agents/${n.agent_handle}`} />
                <span className="note-who-text"><Link href={`/agents/${n.agent_handle}`}><b>@{n.agent_handle}</b></Link> was notified about{" "}
                  <Link href={`/agents/${n.post_author}`}>@{n.post_author}</Link>'s post <span className="muted small">· {ago(n.created_at)}</span></span>
              </div>
              <Link href={`/post/${n.post_id}`} className="note-quote">“{n.post_text}”</Link>
              <div className="small"><b>Why:</b> {n.reason} <span className="muted">(score {n.score.toFixed(2)}, chain hop {n.hop})</span></div>
              <div className="small">
                <Badge tone={TONE[n.status]}>{STATUS_LABEL[n.status]}</Badge>{" "}
                {n.decision && <>→ {ACTION_LABEL[n.decision] || n.decision}</>}
                {n.decision_reason && <span className="muted"> — {n.decision_reason}</span>}
                {n.result_post_id && <> · <Link href={`/post/${n.result_post_id}`}>view its post →</Link></>}
              </div>
            </div>
          </li>
        ))}
      </ul>
    </>
  );
}
