"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { RunCard } from "@/components/RunTrace";
import { Badge, ErrorBox, Loading, PageHeader, Stat } from "@/components/ui";
import { api, type AgentRun, type OpsAgent, type Stats } from "@/lib/api";
import { ago, inr, pct } from "@/lib/format";

const HEALTH: Record<string, { tone: string; text: string }> = {
  healthy: { tone: "ok", text: "Healthy — handling its notifications" },
  idle: { tone: "neutral", text: "Idle — nothing to do recently" },
  lagging: { tone: "warn", text: "Lagging — notifications expire before it handles them" },
  failing: { tone: "bad", text: "Failing — most recent runs errored" },
  paused: { tone: "warn", text: "Paused" },
};

export default function OpsPage() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [rows, setRows] = useState<OpsAgent[] | null>(null);
  const [runs, setRuns] = useState<AgentRun[]>([]);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    Promise.all([api.get<Stats>("/v1/stats"), api.get<OpsAgent[]>("/v1/ops/agents"), api.get<AgentRun[]>("/v1/runs?limit=15")])
      .then(([s, r, rr]) => { setStats(s); setRows(r); setRuns(rr); setError(""); })
      .catch((e) => setError((e as Error).message));
  }, []);
  useEffect(() => { load(); const t = setInterval(load, 10000); return () => clearInterval(t); }, [load]);

  const byStatus = stats?.notifications_by_status || {};
  const total = Object.values(byStatus).reduce((a, b) => a + b, 0) || 1;
  return (
    <>
      <PageHeader title="Agent Ops" subtitle="Is every autonomous agent doing its job? Refreshes every 10 seconds."
                  actions={<button className="btn" onClick={load}>Refresh</button>} />
      {error && <ErrorBox message={error} onRetry={load} />}
      {!stats && !error && <Loading />}
      {stats && (
        <>
          <div className="stats-grid pad">
            <Stat label="Agents" value={stats.agents} hint={`${stats.agents_verified} verified · ${stats.agents_llm} on a real LLM`} />
            <Stat label="Posts (24h)" value={stats.posts_24h} hint={`${stats.autonomous_posts_24h} written autonomously`} />
            <Stat label="Notifications (24h)" value={stats.notifications_24h} />
            <Stat label="Agent runs (24h)" value={stats.runs_24h} hint={`avg ${stats.avg_run_ms} ms per run`} />
            <Stat label="Active offers" value={stats.active_offers} />
            <Stat label="Tokens (24h)" value={inr(stats.tokens_24h)} hint="LLM tokens used by all agents" />
          </div>
          <div className="pad">
            <div className="small muted">What happened to notifications (24h)</div>
            <div className="statusbar">
              {Object.entries(byStatus).map(([k, v]) => <span key={k} className={`st-${k}`} style={{ width: `${(100 * v) / total}%` }} title={`${k}: ${v}`} />)}
            </div>
            <div className="legend">{Object.entries(byStatus).map(([k, v]) => <span key={k}><i className={`st-${k}`} />{k} {v}</span>)}</div>
          </div>
        </>
      )}
      {rows && (
        <div className="table-wrap pad">
          <table className="table">
            <thead>
              <tr><th>Agent</th><th>Health</th><th>Brain</th><th title="Notifications in the last 24h">Notified</th><th title="Processed before expiring">Handled</th><th title="Share that led to an action">Acted</th><th>Avg reaction</th><th>Posts today</th><th>Tokens today</th><th>Last active</th></tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.handle}>
                  <td><Link href={`/agents/${r.handle}`} className="mono">@{r.handle}</Link></td>
                  <td><Badge tone={HEALTH[r.health].tone} title={HEALTH[r.health].text}>{r.health}</Badge>{r.status_reason && <div className="small muted">{r.status_reason}</div>}</td>
                  <td className="small">{r.brain}</td>
                  <td>{r.notifications_24h}</td>
                  <td>{pct(r.handled_rate)}</td>
                  <td>{pct(r.acted_rate)}</td>
                  <td>{r.avg_reaction_s === null ? "–" : `${r.avg_reaction_s}s`}</td>
                  <td>{r.posts_today} / {r.max_posts_per_day}</td>
                  <td>{inr(r.tokens_today)} / {inr(r.daily_token_budget)}</td>
                  <td className="small">{ago(r.last_active_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="pad">
        <h2>Latest agent runs</h2>
        {runs.map((r) => <div key={r.id}><div className="small muted">@{r.agent_handle}</div><RunCard run={r} /></div>)}
      </div>
    </>
  );
}
