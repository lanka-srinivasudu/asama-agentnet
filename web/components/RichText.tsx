import Link from "next/link";
import type { ReactNode } from "react";

// Turns @handles and #hashtags into links, like Twitter.
export function RichText({ text, mentions = [] }: { text: string; mentions?: string[] }) {
  const parts: ReactNode[] = [];
  const re = /(@[a-z0-9_]{3,48}|#[A-Za-z0-9_]{2,40})/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) parts.push(text.slice(last, m.index));
    const tok = m[0];
    if (tok.startsWith("@")) {
      const h = tok.slice(1);
      parts.push(mentions.includes(h) || h.includes("_agent_")
        ? <Link key={i++} href={`/agents/${h}`} className="mention" onClick={(e) => e.stopPropagation()}>{tok}</Link>
        : tok);
    } else {
      parts.push(<Link key={i++} href={`/?tag=${tok.slice(1).toLowerCase()}`} className="hashtag" onClick={(e) => e.stopPropagation()}>{tok}</Link>);
    }
    last = m.index + tok.length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return <>{parts}</>;
}
