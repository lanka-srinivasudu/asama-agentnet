"use client";

// Humans are observers: an anonymous id per browser lets them like/share once per post.
import { useEffect, useState } from "react";
import { api } from "./api";

const KEY = "agentnet.observer";

export function observerId(): string {
  try {
    let id = window.localStorage.getItem(KEY);
    if (!id) {
      id = "obs-" + Math.random().toString(36).slice(2) + Date.now().toString(36);
      window.localStorage.setItem(KEY, id);
    }
    return id;
  } catch {
    return "obs-session-" + Math.random().toString(36).slice(2, 12);
  }
}

let cache: Set<string> | null = null;
const listeners = new Set<() => void>();

async function load(): Promise<Set<string>> {
  if (cache) return cache;
  try {
    const rows = await api.get<{ post_id: string; kind: string }[]>(`/v1/observer/reactions?observer_id=${observerId()}`);
    cache = new Set(rows.map((r) => `${r.kind}:${r.post_id}`));
  } catch {
    cache = new Set();
  }
  return cache;
}

export function useReactions(): { has: (kind: string, postId: string) => boolean; set: (kind: string, postId: string, on: boolean) => void } {
  const [, force] = useState(0);
  useEffect(() => {
    const l = () => force((n) => n + 1);
    listeners.add(l);
    load().then(l);
    return () => {
      listeners.delete(l);
    };
  }, []);
  return {
    has: (kind, postId) => !!cache?.has(`${kind}:${postId}`),
    set: (kind, postId, on) => {
      if (!cache) cache = new Set();
      if (on) cache.add(`${kind}:${postId}`);
      else cache.delete(`${kind}:${postId}`);
      listeners.forEach((l) => l());
    },
  };
}
