"use client";

import Link from "next/link";
import { useState } from "react";
import type { AgentRun, RunStep } from "@/lib/api";
import { ago, exact } from "@/lib/format";
import { Badge } from "./ui";

const STEP_INFO: Record<string, { title: string; explain: string }> = {
  perceive: { title: "1 · Perceive", explain: "Built its context: what it is allowed to see, within a token budget" },
  decide: { title: "2 · Decide", explain: "Chose what to do and why" },
  gather: { title: "3 · Gather", explain: "Looked things up with its tools (company database, web search)" },
  compose: { title: "4 · Compose", explain: "Wrote the post" },
  guard: { title: "5 · Guardrails", explain: "Safety checks before publishing" },
  act: { title: "6 · Act", explain: "Did it on the platform" },
  remember: { title: "7 · Remember", explain: "Saved what happened to its memory" },
  error: { title: "Error", explain: "Something went wrong" },
};

const STATUS_TONE: Record<string, string> = { acted: "ok", ignored: "neutral", blocked: "warn", failed: "bad" };

function StepBody({ s }: { s: RunStep }) {
  if (s.step === "perceive") {
    const sections = (s.sections || {}) as Record<string, number>;
    const total = Object.values(sections).reduce((a, b) => a + b, 0) || 1;
    return (
      <div>
        <div className="small">{String(s.context_tokens)} tokens of context (budget 3,000)</div>
        <div className="ctx-bar">
          {Object.entries(sections).map(([name, n], i) => (
            <span key={name} style={{ width: `${(100 * n) / total}%`, opacity: 1 - i * 0.09 }} title={`${name}: ${n} tokens`} />
          ))}
        </div>
        <ul className="ctx-list">
          {Object.entries(sections).map(([name, n]) => <li key={name}><span>{name}</span><span className="muted">{n} tok</span></li>)}
        </ul>
      </div>
    );
  }
  if (s.step === "decide") {
    return (
      <div>
        <b>Action: {String(s.action)}</b> <span className="muted">(importance {String(s.importance)}/10)</span>
        <div>“{String(s.reason)}”</div>
        {s.db_query ? <div className="small muted">Will search company DB for: {String(s.db_query)}</div> : null}
        {s.web_query ? <div className="small muted">Will search the web for: {String(s.web_query)}</div> : null}
      </div>
    );
  }
  if (s.step === "gather") {
    return (
      <div className="small">
        {String(s.db_rows)} company-DB record(s) · {String(s.web_results)} web result(s)
        {s.web_error ? <span className="muted"> · web: {String(s.web_error)}</span> : null}
        {Array.isArray(s.sample) && s.sample.length > 0 && <div className="muted mono">{(s.sample as string[]).join("  |  ")}</div>}
      </div>
    );
  }
  if (s.step === "compose") {
    return <div><div className="quote-text">“{String(s.text)}”</div>{s.offer_code ? <div className="small muted">Coupon: {String(s.offer_code)}</div> : null}</div>;
  }
  if (s.step === "guard") {
    const problems = (s.problems || []) as string[];
    if (s.downgraded_to) return <div className="small">Nothing concrete to add → changed to a <b>like</b>.</div>;
    return s.ok ? <div className="small ok-text">All checks passed</div>
      : <ul className="small bad-text">{problems.map((p) => <li key={p}>{p}</li>)}</ul>;
  }
  if (s.step === "act") {
    return s.error ? <div className="small bad-text">{String(s.error)}</div>
      : s.post_id ? <Link className="small" href={`/post/${String(s.post_id)}`}>Published → view post</Link>
        : <div className="small">Done: {String(s.action)}</div>;
  }
  if (s.step === "error") return <div className="small bad-text">{String(s.error)}</div>;
  return null;
}

export function RunCard({ run, defaultOpen = false }: { run: AgentRun; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="run">
      <button className="run-head" onClick={() => setOpen(!open)} aria-expanded={open}>
        <Badge tone={STATUS_TONE[run.status]}>{run.status}</Badge>
        <span className="run-summary">{run.summary}</span>
        <span className="muted small" title={exact(run.created_at)}>{run.trigger} · {ago(run.created_at)} · {run.latency_ms} ms
          {run.tokens_in + run.tokens_out > 0 ? ` · ${run.tokens_in + run.tokens_out} tokens` : ""}</span>
        <span className="muted">{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <ol className="steps">
          {run.steps.map((s, i) => (
            <li key={i} className={`step step-${s.step}`}>
              <div className="step-title">{STEP_INFO[s.step]?.title || s.step} <span className="muted small">{s.ms} ms</span></div>
              <div className="muted small">{STEP_INFO[s.step]?.explain}</div>
              <StepBody s={s} />
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
