"use client";

/**
 * One shared live connection (Server-Sent Events) per browser tab.
 *
 * Every page that wants live updates subscribes here instead of opening its own EventSource.
 * Browsers allow only ~6 open connections per host over HTTP/1.1, so several tabs that each opened
 * two streams could use them all up and silently stop receiving updates. One stream per tab avoids that,
 * and pages also poll as a fallback (see useLiveRefresh) so they stay fresh even if the stream drops.
 */
import { useEffect, useRef } from "react";
import { streamUrl } from "./api";

export type LiveHandler = (type: string, data: Record<string, unknown>) => void;

const TYPES = ["post.created", "notification.created", "agent.run", "post.like", "post.repost", "post.cite",
  "human.like", "human.share"];

let source: EventSource | null = null;
let online = false;
const handlers = new Set<LiveHandler>();
const statusHandlers = new Set<(on: boolean) => void>();

function setOnline(on: boolean) {
  online = on;
  statusHandlers.forEach((h) => h(on));
}

function ensure() {
  if (source || typeof window === "undefined") return;
  try {
    source = new EventSource(streamUrl());
    source.onopen = () => setOnline(true);
    source.onerror = () => setOnline(false);   // EventSource reconnects by itself
    TYPES.forEach((t) => source?.addEventListener(t, (e) => {
      let data: Record<string, unknown> = {};
      try { data = JSON.parse((e as MessageEvent).data); } catch { return; }
      handlers.forEach((h) => h(t, data));
    }));
  } catch {
    setOnline(false);
  }
}

export function subscribeLive(h: LiveHandler): () => void {
  ensure();
  handlers.add(h);
  return () => { handlers.delete(h); };
}

export function onLiveStatus(h: (on: boolean) => void): () => void {
  ensure();
  statusHandlers.add(h);
  h(online);
  return () => { statusHandlers.delete(h); };
}

/**
 * Call `refresh` when a matching live event arrives (debounced), every `pollMs` as a fallback,
 * and when the tab becomes visible again.
 */
export function useLiveRefresh(refresh: () => void, match: (type: string, data: Record<string, unknown>) => boolean,
                               pollMs = 15000) {
  const ref = useRef({ refresh, match });
  ref.current = { refresh, match };
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | null = null;
    const soon = () => {
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => ref.current.refresh(), 700);
    };
    const off = subscribeLive((t, d) => { if (ref.current.match(t, d)) soon(); });
    const poll = setInterval(() => { if (document.visibilityState === "visible") ref.current.refresh(); }, pollMs);
    const onVis = () => { if (document.visibilityState === "visible") soon(); };
    document.addEventListener("visibilitychange", onVis);
    return () => {
      off();
      clearInterval(poll);
      document.removeEventListener("visibilitychange", onVis);
      if (timer) clearTimeout(timer);
    };
  }, [pollMs]);
}
