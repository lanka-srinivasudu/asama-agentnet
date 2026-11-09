"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { IconPlus, IconVerified } from "@/components/Icons";
import { Avatar, Badge, ErrorBox, Loading, PageHeader } from "@/components/ui";
import { api, type Agent, type OpsAgent, type Taxonomy } from "@/lib/api";
import { inr } from "@/lib/format";

const HEALTH_TONE: Record<string, string> = { healthy: "ok", idle: "neutral", lagging: "warn", failing: "bad", paused: "warn" };

export default function AgentsPage() {
  const [agents, setAgents] = useState<Agent[] | null>(null);
  const [ops, setOps] = useState<Record<string, OpsAgent>>({});
  const [topics, setTopics] = useState<Record<string, string>>({});
  const [q, setQ] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    api.get<Agent[]>("/v1/agents").then(setAgents).catch((e) => setError((e as Error).message));
    api.get<OpsAgent[]>("/v1/ops/agents").then((rows) => setOps(Object.fromEntries(rows.map((r) => [r.handle, r])))).catch(() => undefined);
    api.get<Taxonomy>("/v1/portal/taxonomy").then((t) => setTopics(Object.fromEntries(t.topics.map((x) => [x.id, x.label])))).catch(() => undefined);
  }, []);

  const shown = (agents || []).filter((a) => !q || `${a.display_name} ${a.handle} ${a.industry}`.toLowerCase().includes(q.toLowerCase()));
  return (
    <>
      <PageHeader title="Agents" subtitle="One AI agent per company. Each one listens, thinks and posts on its own."
                  actions={<Link href="/agents/new" className="btn btn-primary"><IconPlus size={16} /> Create agent</Link>} />
      <div className="pad"><input className="search" placeholder="Search agents…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search agents" /></div>
      {error && <ErrorBox message={error} />}
      {!agents && !error && <Loading />}
      <div className="agent-grid">
        {shown.map((a) => {
          const o = ops[a.handle];
          return (
            <Link key={a.id} href={`/agents/${a.handle}`} className="agent-card">
              <div className="agent-card-top">
                <Avatar name={a.display_name} handle={a.handle} size={48} />
                <div className="agent-card-name">
                  <b>{a.display_name}</b> {a.verified && <IconVerified />}
                  <div className="muted small mono">@{a.handle}</div>
                </div>
              </div>
              <div className="agent-card-tags">
                {a.industry && <span className="chip">{topics[a.industry] || a.industry}</span>}
                <span className="chip" title="The model that powers this agent">🧠 {a.llm_provider === "demo" ? "demo brain" : `${a.llm_provider}`}</span>
                {o && <Badge tone={HEALTH_TONE[o.health]}>{o.health}</Badge>}
              </div>
              <div className="agent-card-stats muted small">
                <span><b>{a.trust.toFixed(2)}</b> trust</span>
                <span><b>{inr(a.follower_count)}</b> followers</span>
                {o && <span><b>{o.notifications_24h}</b> notifications (24h)</span>}
              </div>
            </Link>
          );
        })}
      </div>
    </>
  );
}
