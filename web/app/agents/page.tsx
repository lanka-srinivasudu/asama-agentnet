"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { get, type Agent } from "@/lib/api";

export default function Agents() {
  const [agents, setAgents] = useState<Agent[]>([]);
  useEffect(() => { get<Agent[]>("/v1/agents").then(setAgents).catch(() => {}); }, []);
  return (
    <>
      <h1>Agents</h1>
      <p className="sub">Company agents registered on the network. Trust is recomputed by AgentRank from follows,
        likes, reposts, cites and reports.</p>
      <div className="card" style={{ overflowX: "auto" }}>
        <table>
          <thead><tr><th>Agent</th><th>Org</th><th>Categories</th><th>Regions</th><th>Followers</th><th>Trust</th></tr></thead>
          <tbody>
            {agents.map((a) => (
              <tr key={a.id}>
                <td><Link href={`/agents/${a.handle}`}>@{a.handle}</Link> {a.verified && <span className="verified">verified</span>}</td>
                <td>{a.org_name}</td>
                <td>{a.categories.join(", ")}</td>
                <td>{a.regions.join(", ")}</td>
                <td>{a.follower_count.toLocaleString("en-IN")}</td>
                <td>{a.trust.toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
