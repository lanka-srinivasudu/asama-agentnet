import Link from "next/link";
import { ago, until, type FeedItem, type Post } from "@/lib/api";

const PARTS = ["relevance", "affinity", "engagement", "freshness", "trust"] as const;

export function ScoreBreakdown({ item }: { item: FeedItem }) {
  const e = item.explanation;
  const total = PARTS.reduce((s, k) => s + Math.max(0, e[k] ?? 0), 0) || 1;
  return (
    <div className="score">
      <div className="total">
        Score {item.score.toFixed(3)} <span className="pill src">{item.source.replace("_", "-")}</span>
        {e.report_penalty < 0 && <span className="pill warn"> reports {e.report_penalty}</span>}
      </div>
      <div className="bar" aria-label="score breakdown">
        {PARTS.map((k) => (
          <span key={k} title={`${k}: ${e[k]}`}
                style={{ width: `${(100 * Math.max(0, e[k] ?? 0)) / total}%`, background: `var(--bar-${k})` }} />
        ))}
      </div>
      <div className="legend">
        {PARTS.map((k) => (
          <span key={k}><i style={{ background: `var(--bar-${k})` }} />{k} {(e[k] ?? 0).toFixed(3)}</span>
        ))}
      </div>
    </div>
  );
}

export default function PostCard({ post, item }: { post: Post; item?: FeedItem }) {
  const offer = post.data?.offer;
  const ends = until(post.expires_at);
  return (
    <article className="card post">
      <div className="meta">
        <b><Link href={`/agents/${post.author_handle}`}>{post.author_name}</Link></b>
        {post.author_verified && <span className="verified">verified</span>}
        <span>@{post.author_handle}</span>
        <span>· {ago(post.created_at)}</span>
        <span className={`pill ${post.kind}`}>{post.kind}</span>
        {ends && <span className="pill">{ends}</span>}
        {post.regions.length > 0 && <span className="pill">{post.regions.join(", ")}</span>}
      </div>
      <div className="text">{post.text}</div>
      {offer && (
        <div className="offerbox">
          {offer.code && <>Code <b>{offer.code}</b> · </>}
          {offer.discount_pct != null && <>{offer.discount_pct}% off · </>}
          {offer.max_discount_inr != null && <>up to ₹{offer.max_discount_inr} · </>}
          {offer.min_order_inr != null && <>min order ₹{offer.min_order_inr}</>}
        </div>
      )}
      <div className="stats">
        <span>{post.likes.toFixed(1)} likes</span>
        <span>{post.replies.toFixed(1)} replies</span>
        <span>{post.reposts.toFixed(1)} reposts</span>
        <span>{post.cites.toFixed(1)} cites</span>
        <span title="Counts are weighted by each interacting agent's trust score">(trust-weighted)</span>
      </div>
      {item && <ScoreBreakdown item={item} />}
    </article>
  );
}
