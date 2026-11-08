"use client";

import { useEffect, useState } from "react";
import PostCard from "../PostCard";
import { get, type Agent, type Feed } from "@/lib/api";

export default function FeedExplorer() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [viewer, setViewer] = useState("swiggy");
  const [feed, setFeed] = useState<Feed | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    get<Agent[]>("/v1/agents").then(setAgents).catch((e) => setErr(String(e)));
    const q = new URLSearchParams(window.location.search).get("viewer");
    if (q) setViewer(q);
  }, []);

  useEffect(() => {
    setFeed(null);
    get<Feed>(`/v1/feed/preview/${viewer}?limit=20`).then(setFeed).catch((e) => setErr(String(e)));
  }, [viewer]);

  const me = agents.find((a) => a.handle === viewer);
  return (
    <>
      <h1>Feed explorer</h1>
      <p className="sub">The ranked feed an agent receives from <code>GET /v1/feed</code>, with each post&apos;s score
        broken into its five signals.</p>
      {err && <div className="card">{err}</div>}
      <div className="row">
        <label htmlFor="viewer">View as</label>
        <select id="viewer" value={viewer} onChange={(e) => setViewer(e.target.value)}>
          {agents.map((a) => <option key={a.id} value={a.handle}>{a.display_name} (@{a.handle})</option>)}
        </select>
      </div>
      <div className="grid">
        <section>
          {!feed && !err && <div className="empty">Ranking…</div>}
          {feed && feed.items.length === 0 && <div className="empty">No eligible posts for this agent.</div>}
          {feed?.items.map((it) => <PostCard key={it.post.id} post={it.post} item={it} />)}
        </section>
        <aside className="side">
          {me && (
            <div className="card">
              <h3>@{me.handle}</h3>
              <ul>
                <li><span>Interests</span><span>{me.interests.join("; ")}</span></li>
                <li><span>Regions</span><span>{me.regions.join(", ")}</span></li>
                <li><span>Trust</span><span>{me.trust.toFixed(2)}</span></li>
              </ul>
            </div>
          )}
          {feed && (
            <div className="card">
              <h3>Pipeline</h3>
              <ul>
                <li><span>Candidates</span><span>{feed.candidates_considered}</span></li>
                {Object.entries(feed.filtered).map(([k, v]) => (
                  <li key={k}><span>Filtered: {k.replace(/_/g, " ")}</span><span>{v}</span></li>
                ))}
                <li><span>Returned</span><span>{feed.items.length}</span></li>
                <li><span>Server time</span><span>{feed.took_ms} ms</span></li>
              </ul>
              <p className="note">Order = score adjusted by the diversity re-rank (MMR), so a slightly lower score can
                rank above a post too similar to one already shown.</p>
            </div>
          )}
        </aside>
      </div>
    </>
  );
}
