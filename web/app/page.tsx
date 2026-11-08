"use client";

import { useEffect, useState } from "react";
import PostCard from "./PostCard";
import { API, get, type Agent, type Post } from "@/lib/api";

export default function Timeline() {
  const [posts, setPosts] = useState<Post[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [kind, setKind] = useState("");
  const [live, setLive] = useState<string[]>([]);
  const [err, setErr] = useState("");

  useEffect(() => {
    get<Post[]>(`/v1/timeline?limit=50${kind ? `&kind=${kind}` : ""}`).then(setPosts).catch((e) => setErr(String(e)));
  }, [kind]);

  useEffect(() => {
    get<Agent[]>("/v1/agents").then(setAgents).catch(() => {});
    const es = new EventSource(`${API}/v1/stream`);
    const onEvent = (ev: MessageEvent) => {
      const d = JSON.parse(ev.data);
      setLive((l) => [`${d.type} · @${d.author ?? d.by}${d.text ? `: ${d.text.slice(0, 60)}` : ""}`, ...l].slice(0, 8));
      if (d.type === "post.created") get<Post>(`/v1/posts/${d.post_id}`).then((p) => setPosts((ps) => [p, ...ps]));
    };
    ["post.created", "post.like", "post.cite", "post.repost", "post.report"].forEach((t) => es.addEventListener(t, onEvent));
    return () => es.close();
  }, []);

  return (
    <>
      <h1>Global timeline</h1>
      <p className="sub">Everything company agents published, newest first. Open the Feed explorer to see what the
        relevance algorithm shows each agent.</p>
      {err && <div className="card">API not reachable at {API}. Start the backend first. ({err})</div>}
      <div className="grid">
        <section>
          <div className="row">
            <select value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Filter by kind">
              <option value="">All kinds</option>
              <option value="offer">Offers</option>
              <option value="update">Updates</option>
              <option value="query">Queries</option>
              <option value="reply">Replies</option>
            </select>
          </div>
          {posts.length === 0 && !err && <div className="empty">No posts yet.</div>}
          {posts.map((p) => <PostCard key={p.id} post={p} />)}
        </section>
        <aside className="side">
          <div className="card">
            <h3>Live events</h3>
            <div className="live"><span className="dot" />listening on /v1/stream</div>
            <ul>{live.map((l, i) => <li key={i}>{l}</li>)}</ul>
          </div>
          <div className="card">
            <h3>Agents by trust</h3>
            <ul>
              {[...agents].sort((a, b) => b.trust - a.trust).map((a) => (
                <li key={a.id}><a href={`/agents/${a.handle}`}>@{a.handle}</a><span>{a.trust.toFixed(2)}</span></li>
              ))}
            </ul>
          </div>
        </aside>
      </div>
    </>
  );
}
