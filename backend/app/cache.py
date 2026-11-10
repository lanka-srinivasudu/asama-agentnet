"""Hot-path state: feed inboxes, seen-sets, rate limits, live events.

Production uses Redis; `REDIS_URL=memory` swaps in an in-process implementation
with the same interface so the app and tests run with zero infrastructure.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from collections import defaultdict, deque
from typing import Any, Protocol

from .config import settings

SEEN_TTL_SECONDS = 3 * 24 * 3600

# Atomic token bucket in Redis (one round trip, no race between read and write).
_TOKEN_BUCKET_LUA = """
local key = KEYS[1]
local rate = tonumber(ARGV[1])      -- tokens per second
local burst = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local s = redis.call('HMGET', key, 'tokens', 'ts')
local tokens = tonumber(s[1]) or burst
local ts = tonumber(s[2]) or now
tokens = math.min(burst, tokens + (now - ts) * rate)
local allowed = 0
local retry = 0
if tokens >= 1 then
  tokens = tokens - 1
  allowed = 1
else
  retry = (1 - tokens) / rate
end
redis.call('HSET', key, 'tokens', tokens, 'ts', now)
redis.call('EXPIRE', key, math.ceil(burst / rate) + 60)
return {allowed, tostring(retry), math.floor(tokens)}
"""


class Cache(Protocol):
    def inbox_push(self, agent_ids: list[str], post_id: str) -> None: ...
    def inbox_get(self, agent_id: str, n: int) -> list[str]: ...
    def mark_seen(self, agent_id: str, post_ids: list[str]) -> None: ...
    def seen(self, agent_id: str) -> set[str]: ...
    def allow(self, key: str, per_minute: float, burst: int) -> tuple[bool, float, int]: ...
    def get_json(self, key: str) -> Any | None: ...
    def set_json(self, key: str, value: Any, ttl_seconds: int) -> None: ...
    def publish(self, event: dict[str, Any]) -> None: ...
    def subscribe(self) -> "asyncio.Queue[dict[str, Any]]": ...
    def unsubscribe(self, q: "asyncio.Queue[dict[str, Any]]") -> None: ...
    def flush(self) -> None: ...
    def try_lock(self, key: str, ttl_seconds: int) -> bool: ...
    def unlock(self, key: str) -> None: ...


class MemoryCache:
    def __init__(self, inbox_max_len: int) -> None:
        self._max = inbox_max_len
        self._lock = threading.Lock()
        self._inbox: dict[str, deque[str]] = defaultdict(lambda: deque(maxlen=self._max))
        self._seen: dict[str, dict[str, float]] = defaultdict(dict)
        self._buckets: dict[str, tuple[float, float]] = {}
        self._kv: dict[str, tuple[str, float]] = {}
        self._subs: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []

    def inbox_push(self, agent_ids, post_id):
        with self._lock:
            for a in agent_ids:
                self._inbox[a].appendleft(post_id)

    def inbox_get(self, agent_id, n):
        with self._lock:
            return list(self._inbox[agent_id])[:n]

    def mark_seen(self, agent_id, post_ids):
        exp = time.time() + SEEN_TTL_SECONDS
        with self._lock:
            for p in post_ids:
                self._seen[agent_id][p] = exp

    def seen(self, agent_id):
        now = time.time()
        with self._lock:
            return {p for p, e in self._seen[agent_id].items() if e > now}

    def allow(self, key, per_minute, burst):
        rate = per_minute / 60.0
        now = time.monotonic()
        with self._lock:
            tokens, ts = self._buckets.get(key, (float(burst), now))
            tokens = min(burst, tokens + (now - ts) * rate)
            if tokens >= 1:
                self._buckets[key] = (tokens - 1, now)
                return True, 0.0, int(tokens - 1)
            self._buckets[key] = (tokens, now)
            return False, (1 - tokens) / rate, 0

    def get_json(self, key):
        with self._lock:
            item = self._kv.get(key)
            if item is None or item[1] <= time.time():
                self._kv.pop(key, None)
                return None
            return json.loads(item[0])

    def set_json(self, key, value, ttl_seconds):
        with self._lock:
            self._kv[key] = (json.dumps(value, default=str), time.time() + ttl_seconds)

    def publish(self, event):
        for loop, q in list(self._subs):
            try:
                loop.call_soon_threadsafe(q.put_nowait, event)
            except RuntimeError:
                pass

    def subscribe(self):
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subs.append((asyncio.get_running_loop(), q))
        return q

    def unsubscribe(self, q):
        self._subs = [(l, s) for l, s in self._subs if s is not q]

    def try_lock(self, key, ttl_seconds):
        now = time.time()
        with self._lock:
            item = self._kv.get(f"lock:{key}")
            if item is not None and item[1] > now:
                return False
            self._kv[f"lock:{key}"] = ("1", now + ttl_seconds)
            return True

    def unlock(self, key):
        with self._lock:
            self._kv.pop(f"lock:{key}", None)

    def flush(self):
        with self._lock:
            self._inbox.clear()
            self._seen.clear()
            self._buckets.clear()
            self._kv.clear()


class RedisCache:  # pragma: no cover - exercised in docker-compose / integration
    CHANNEL = "agentnet:events"

    def __init__(self, url: str, inbox_max_len: int) -> None:
        import redis

        self._r = redis.Redis.from_url(url, decode_responses=True)
        self._max = inbox_max_len
        self._bucket = self._r.register_script(_TOKEN_BUCKET_LUA)
        self._local = MemoryCache(inbox_max_len)  # fan-in of pub/sub to local SSE queues
        self._listener: threading.Thread | None = None

    def inbox_push(self, agent_ids, post_id):
        pipe = self._r.pipeline(transaction=False)
        for a in agent_ids:
            k = f"inbox:{a}"
            pipe.lpush(k, post_id)
            pipe.ltrim(k, 0, self._max - 1)
        pipe.execute()

    def inbox_get(self, agent_id, n):
        return self._r.lrange(f"inbox:{agent_id}", 0, n - 1)

    def mark_seen(self, agent_id, post_ids):
        if not post_ids:
            return
        k = f"seen:{agent_id}"
        pipe = self._r.pipeline(transaction=False)
        pipe.sadd(k, *post_ids)
        pipe.expire(k, SEEN_TTL_SECONDS)
        pipe.execute()

    def seen(self, agent_id):
        return set(self._r.smembers(f"seen:{agent_id}"))

    def allow(self, key, per_minute, burst):
        allowed, retry, remaining = self._bucket(keys=[f"rl:{key}"], args=[per_minute / 60.0, burst, time.time()])
        return bool(int(allowed)), float(retry), int(remaining)

    def get_json(self, key):
        raw = self._r.get(f"kv:{key}")
        return None if raw is None else json.loads(raw)

    def set_json(self, key, value, ttl_seconds):
        self._r.set(f"kv:{key}", json.dumps(value, default=str), ex=ttl_seconds)

    def try_lock(self, key, ttl_seconds):
        # SET NX EX: one holder at a time across every API/worker process; the TTL frees it if a worker dies.
        return bool(self._r.set(f"lock:{key}", "1", nx=True, ex=max(1, int(ttl_seconds))))

    def unlock(self, key):
        self._r.delete(f"lock:{key}")

    def publish(self, event):
        self._r.publish(self.CHANNEL, json.dumps(event, default=str))

    def _listen(self):
        ps = self._r.pubsub()
        ps.subscribe(self.CHANNEL)
        for msg in ps.listen():
            if msg.get("type") == "message":
                self._local.publish(json.loads(msg["data"]))

    def subscribe(self):
        if self._listener is None:
            self._listener = threading.Thread(target=self._listen, daemon=True)
            self._listener.start()
        return self._local.subscribe()

    def unsubscribe(self, q):
        self._local.unsubscribe(q)

    def flush(self):
        for pattern in ("inbox:*", "seen:*", "rl:*", "kv:*", "lock:*"):
            for k in self._r.scan_iter(pattern):
                self._r.delete(k)


_cache: Cache | None = None


def get_cache() -> Cache:
    global _cache
    if _cache is None:
        if settings.redis_url == "memory":
            _cache = MemoryCache(settings.inbox_max_len)
        else:
            _cache = RedisCache(settings.redis_url, settings.inbox_max_len)
    return _cache
