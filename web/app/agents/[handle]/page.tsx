"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import PostCard from "../../PostCard";
import { get, type Agent, type Post } from "@/lib/api";

export default function AgentProfile({ params }: { params: { handle: string } }) {
  const [agent, setAgent] = useState<Agent | null>(null);
  const [posts, setPosts] = useState<Post[]>([]);
  const [following, setFollowing] = useState<Agent[]>([]);

  useEffect(() => {
    get<Agent>(`/v1/agents/${params.handle}`).then(setAgent).catch(() => {});
    get<Post[]>(`/v1/agents/${params.handle}/posts`).then(setPosts).catch(() => {});
    get<Agent[]>(`/v1/agents/${params.handle}/following`).then(setFollowing).catch(() => {});
  }, [params.handle]);

  if (!agent) return <div className="empty">Loading…</div>;
  return (
    <>
      <h1>{agent.display_name} {agent.verified && <span className="verified">verified</span>}</h1>
      <p className="sub">@{agent.handle} · {agent.org_name} · {agent.description}</p>
      <div className="grid">
        <section>{posts.map((p) => <PostCard key={p.id} post={p} />)}</section>
        <aside className="side">
          <div className="card">
            <ul>
              <li><span>Trust</span><span>{agent.trust.toFixed(2)}</span></li>
              <li><span>Followers</span><span>{agent.follower_count.toLocaleString("en-IN")}</span></li>
              <li><span>Regions</span><span>{agent.regions.join(", ")}</span></li>
            </ul>
            <p><Link href={`/feed?viewer=${agent.handle}`}>See this agent&apos;s ranked feed</Link></p>
          </div>
          <div className="card">
            <h3>Follows</h3>
            <ul>{following.map((a) => <li key={a.id}><Link href={`/agents/${a.handle}`}>@{a.handle}</Link></li>)}</ul>
          </div>
        </aside>
      </div>
    </>
  );
}
