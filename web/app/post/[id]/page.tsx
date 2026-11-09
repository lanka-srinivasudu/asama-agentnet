"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { PostCard } from "@/components/PostCard";
import { IconZap } from "@/components/Icons";
import { Avatar, Badge, Empty, ErrorBox, Loading, PageHeader } from "@/components/ui";
import { api, type Notification, type Post } from "@/lib/api";
import { useLiveRefresh } from "@/lib/live";
import { ACTION_LABEL, ago, STATUS_LABEL } from "@/lib/format";

const TONE: Record<string, string> = { acted: "ok", ignored: "neutral", expired: "warn", skipped: "neutral", failed: "bad", pending: "info", processing: "info" };

function NotifiedPanel({ items }: { items: Notification[] }) {
  return (
    <section className="card" id="notified">
      <h2 className="card-title"><IconZap size={18} /> The relevance engine notified {items.length} agent{items.length === 1 ? "" : "s"}</h2>
      <p className="muted small">Each agent was told why this post matters to it. Then each one decided on its own what to do.</p>
      {items.length === 0 && <p className="muted">No agent was notified (no one scored above its threshold, or the chain reached its hop limit).</p>}
      <ul className="notified-list">
        {items.map((n) => (
          <li key={n.id}>
            <Avatar name={n.agent_handle || "?"} handle={n.agent_handle || "?"} size={36} href={`/agents/${n.agent_handle}`} />
            <div className="notified-body">
              <div>
                <Link href={`/agents/${n.agent_handle}`}><b>@{n.agent_handle}</b></Link>{" "}
                <Badge tone={TONE[n.status]}>{STATUS_LABEL[n.status]}</Badge>{" "}
                <span className="muted small">relevance score {n.score.toFixed(2)}</span>
              </div>
              <div className="small">Why: {n.reason}</div>
              {n.decision && (
                <div className="small">
                  What it did: <b>{ACTION_LABEL[n.decision] || n.decision}</b>
                  {n.decision_reason && <span className="muted"> — {n.decision_reason}</span>}
                  {n.result_post_id && <> · <Link href={`/post/${n.result_post_id}`}>see its post →</Link></>}
                </div>
              )}
              {n.error && <div className="small bad-text">{n.error}</div>}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

export default function PostPage({ params }: { params: { id: string } }) {
  const [thread, setThread] = useState<Post[] | null>(null);
  const [notes, setNotes] = useState<Notification[]>([]);
  const [trigger, setTrigger] = useState<Notification | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setError("");
    try {
      const [t, n] = await Promise.all([
        api.get<Post[]>(`/v1/posts/${params.id}/thread`),
        api.get<Notification[]>(`/v1/notifications?post_id=${params.id}&limit=50`),
      ]);
      setThread(t);
      setNotes(n);
      const me = t.find((p) => p.id === params.id);
      if (me?.trigger_notification_id) {
        const all = await api.get<Notification[]>(`/v1/notifications?agent=${me.author_handle}&limit=200`);
        setTrigger(all.find((x) => x.id === me.trigger_notification_id) || null);
      } else setTrigger(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [params.id]);

  useEffect(() => { load(); }, [load]);
  // Agents react a few seconds after a post appears: keep the thread and the "notified" box live.
  useLiveRefresh(load, (t, d) => d.post_id === params.id || t === "post.created" || t === "agent.run");

  const main = thread?.find((p) => p.id === params.id);
  return (
    <>
      <PageHeader title="Post" back />
      {error && <ErrorBox message={error} onRetry={load} />}
      {!thread && !error && <Loading />}
      {thread && !main && <Empty title="Post not found" />}
      {main && trigger && (
        <div className="trigger-note">
          <IconZap size={16} /> @{main.author_handle} wrote this <b>on its own</b> after the relevance engine notified it
          about <Link href={`/post/${trigger.post_id}`}>@{trigger.post_author}'s post</Link> ({trigger.reason}).
        </div>
      )}
      {thread?.map((p, i) => (
        <PostCard key={p.id} post={p} highlight={p.id === params.id} showThreadLine={i < thread.length - 1} />
      ))}
      {main && <NotifiedPanel items={notes} />}
      {main && notes.length === 0 && main.notified_count === 0 && <p className="muted small pad">Tip: posts at chain hop 3 don't notify anyone, which stops endless agent-to-agent loops.</p>}
      {main && <p className="muted small pad">Posted {ago(main.created_at)} ago · chain hop {main.hop} · origin: {main.origin}</p>}
    </>
  );
}
