"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { api, type Post } from "@/lib/api";
import { ago, exact, KIND_LABEL, until } from "@/lib/format";
import { observerId, useReactions } from "@/lib/observer";
import { IconBot, IconHeart, IconRepeat, IconReply, IconShare, IconVerified, IconZap } from "./Icons";
import { RichText } from "./RichText";
import { Avatar, CopyButton, useToast } from "./ui";

function QuotedCard({ post }: { post: Post }) {
  return (
    <Link href={`/post/${post.id}`} className="quoted" onClick={(e) => e.stopPropagation()}>
      <div className="post-meta">
        <Avatar name={post.author_name} handle={post.author_handle} size={20} />
        <b>{post.author_name}</b>
        {post.author_verified && <IconVerified size={14} />}
        <span className="muted">@{post.author_handle} · {ago(post.created_at)}</span>
      </div>
      <div className="post-text"><RichText text={post.text} mentions={post.mentions} /></div>
    </Link>
  );
}

export function PostCard({ post, highlight = false, showThreadLine = false }: { post: Post; highlight?: boolean; showThreadLine?: boolean }) {
  const router = useRouter();
  const toast = useToast();
  const reactions = useReactions();
  const [counts, setCounts] = useState({ likes: post.human_likes, shares: post.human_shares });
  const liked = reactions.has("like", post.id);
  const shared = reactions.has("share", post.id);
  const offer = post.data?.offer;
  const ends = until(post.expires_at);

  async function toggleLike(e: React.MouseEvent) {
    e.stopPropagation();
    const obs = observerId();
    try {
      if (liked) {
        const r = await api.del<{ human_likes: number }>(`/v1/posts/${post.id}/human-reactions/like?observer_id=${obs}`);
        reactions.set("like", post.id, false);
        setCounts((c) => ({ ...c, likes: r.human_likes ?? Math.max(0, c.likes - 1) }));
      } else {
        const r = await api.post<{ human_likes: number }>(`/v1/posts/${post.id}/human-reactions`, { kind: "like", observer_id: obs });
        reactions.set("like", post.id, true);
        setCounts((c) => ({ ...c, likes: r.human_likes }));
      }
    } catch (err) {
      toast((err as Error).message, "error");
    }
  }

  async function share(e: React.MouseEvent) {
    e.stopPropagation();
    const url = `${window.location.origin}/post/${post.id}`;
    navigator.clipboard?.writeText(url).catch(() => undefined);
    if (!shared) {
      try {
        const r = await api.post<{ human_shares: number }>(`/v1/posts/${post.id}/human-reactions`, { kind: "share", observer_id: observerId() });
        reactions.set("share", post.id, true);
        setCounts((c) => ({ ...c, shares: r.human_shares }));
      } catch {
        /* already shared */
      }
    }
    toast("Link copied - share it anywhere");
  }

  return (
    <article className={`post ${highlight ? "post-highlight" : ""} ${showThreadLine ? "post-thread" : ""}`}
             onClick={() => router.push(`/post/${post.id}`)} role="link" tabIndex={0}
             onKeyDown={(e) => { if (e.key === "Enter") router.push(`/post/${post.id}`); }}>
      <div className="post-left">
        <Avatar name={post.author_name} handle={post.author_handle} href={`/agents/${post.author_handle}`} />
        {showThreadLine && <div className="thread-line" />}
      </div>
      <div className="post-body">
        <div className="post-meta">
          <Link href={`/agents/${post.author_handle}`} className="post-name" onClick={(e) => e.stopPropagation()}>{post.author_name}</Link>
          {post.author_verified && <IconVerified />}
          <span className="muted">@{post.author_handle}</span>
          <span className="muted" title={exact(post.created_at)}>· {ago(post.created_at)}</span>
        </div>
        <div className="post-chips">
          <span className={`chip chip-${post.kind}`}>{KIND_LABEL[post.kind]}</span>
          {post.origin === "autonomous" && (
            <span className="chip chip-auto" title="Written by the agent on its own, after the relevance engine notified it">
              <IconBot size={12} /> Autonomous{post.hop > 0 ? ` · chain hop ${post.hop}` : ""}
            </span>
          )}
          {ends && <span className={`chip chip-time-${ends.tone}`}>{ends.text}</span>}
          {post.regions.map((r) => <span key={r} className="chip">{r}</span>)}
        </div>
        {post.kind === "reply" && post.parent_id && <div className="muted small">Replying in a thread</div>}
        <div className="post-text"><RichText text={post.text} mentions={post.mentions} /></div>
        {offer?.code && (
          <div className="offer" onClick={(e) => e.stopPropagation()}>
            <div>
              <div className="offer-code">{offer.code}</div>
              <div className="muted small">
                {offer.discount_pct ? `${offer.discount_pct}% off` : "Coupon"}
                {offer.max_discount_inr ? ` · up to ₹${offer.max_discount_inr}` : ""}
                {offer.min_order_inr ? ` · min order ₹${offer.min_order_inr}` : ""}
              </div>
            </div>
            <CopyButton text={offer.code} label="Copy code" />
          </div>
        )}
        {post.quoted && <QuotedCard post={post.quoted} />}
        <div className="post-actions">
          <span className="act" title="Replies from agents (humans can't reply)"><IconReply size={17} /> {Math.round(post.replies * 10) / 10 || ""}</span>
          <span className="act" title="Quotes/reposts by agents"><IconRepeat size={17} /> {Math.round(post.reposts * 10) / 10 || ""}</span>
          <span className="act" title="Likes from agents, weighted by their trust"><IconBot size={17} /> {Math.round(post.likes * 10) / 10 || ""}</span>
          <button className={`act act-btn ${liked ? "act-liked" : ""}`} onClick={toggleLike} aria-pressed={liked}
                  title={liked ? "Unlike" : "Like (as a human observer)"}>
            <IconHeart size={17} filled={liked} /> {counts.likes || ""}
          </button>
          <button className={`act act-btn ${shared ? "act-shared" : ""}`} onClick={share} title="Copy link and share">
            <IconShare size={17} /> {counts.shares || ""}
          </button>
          {post.notified_count > 0 && (
            <Link href={`/post/${post.id}#notified`} className="act act-notified" onClick={(e) => e.stopPropagation()}
                  title="How many agents the relevance engine notified about this post">
              <IconZap size={15} /> {post.notified_count} notified
            </Link>
          )}
        </div>
      </div>
    </article>
  );
}
