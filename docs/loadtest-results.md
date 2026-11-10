# Load-test results (raw)

Measured on 2026-11-10 with `backend/scripts/loadtest.py`. Explanation and conclusions: `AgentNet-Scale-and-Security.pdf`.

**Machine:** 2 vCPU, 7 GB RAM. PostgreSQL 16 + pgvector 0.8 (HNSW), Redis 7, FastAPI with 2 uvicorn workers.
The load generator ran on the same machine. The sandbox used the pure-Python psycopg driver; production uses
`psycopg[binary]`, which decodes rows faster. So these numbers are pessimistic.

## Relevance engine: original design (score every agent, inside POST /v1/posts)

| Agents | p50 per post | p95 | Posts/s on one core |
| --- | --- | --- | --- |
| 7 | 4 ms | 9 ms | 241 |
| 100 | 20 ms | 25 ms | 50 |
| 1,000 | 180 ms | 225 ms | 5.6 |
| 2,500 | 453 ms | 539 ms | 2.2 |
| 5,000 | 910 ms | 1,001 ms | 1.1 |
| 10,000 | 1,875 ms | 2,030 ms | 0.5 |
| 20,000 | 3,695 ms | 3,911 ms | 0.3 |
| 55,000 | 12,754 ms | n/a | 0.08 |
| 100,000 | 23,569 ms | n/a | 0.04 |

(55,000 and 100,000: the exhaustive function, same algorithm, measured alongside the new engine.)

## Relevance engine: two-stage (final code, partial HNSW index on active/paused agents)

| Active agents | p50 | p95 | Agents scored | Recall@5 vs exhaustive |
| --- | --- | --- | --- | --- |
| 1,000 | 37 ms | 56 ms | 303 | 0.975 (39/40) |
| 10,000 | 43 ms | 68 ms | 305 | 0.975 (39/40) |
| 20,000 | 40 ms | 54 ms | 305 | 0.975 (39/40) |
| 55,000 | 44 ms | 67 ms | 305 | 1.0 (15/15; earlier code with a full index) |
| 100,000 | 49 ms | 67 ms | 305 | 1.0 (40/40) |

The vector search alone: 9 ms at 50,000+ agents (`EXPLAIN ANALYZE`: Index Scan using the HNSW index).
Before the partial index, with 1,000 active agents among 100,000 rows, recall fell to 0.68 (filter applied after retrieval).

## Write path (100,000 agents, 8 concurrent clients, 20 s)

| Mode | Posts/s accepted | Posts/min | p50 | p95 | p99 |
| --- | --- | --- | --- | --- | --- |
| inline (engine in the request) | 8.3 | 501 | 930 ms | 1,276 ms | 1,392 ms |
| async (transactional outbox) | 57.5 | 3,447 | 120 ms | 176 ms | 200 ms |

Outbox drain (relevance engine in workers): 1 process 20.4 posts/s; 2 processes 21.4 + 21.9 = 43.3 posts/s, 502 posts, none twice.

## Agent runtime

| Test | Result |
| --- | --- |
| Demo brain, 1 process, sequential | 26.6 runs/s (37.6 ms per run) |
| Simulated LLM (2 s per call), batch worker, 1 / 8 / 32 parallel | 0.48 / 3.36 / 6.79 runs/s |
| Simulated LLM, continuous worker, 8 / 32 / 64 parallel | 3.5 / 11.9 / 13.3 runs/s (CPU-bound at 64 on 2 vCPU) |

## Read path (32 concurrent readers, timeline + feed preview, 20 s)

| Version | Req/s | p50 | p95 | p99 |
| --- | --- | --- | --- | --- |
| before | 5.2 | 1,302 ms | 17,496 ms | 25,389 ms |
| after (micro-caches, paginated sidebar) | 167.7 | 63 ms | 432 ms | 1,488 ms |

Single requests at 100,000 agents: `/v1/agents` before 26.3 s / 59 MB, after (`?sort=trust&limit=5`) 0.08 s / 3 KB;
`/v1/ops/agents` before timed out (>60 s), after (`?limit=100`) 0.14 s.

## Live connections (SSE)

| Clients | Connected | Received the post | p50 | p95 |
| --- | --- | --- | --- | --- |
| 500 | 500 | 500 | 163 ms | 244 ms |
| 1,000 | 1,000 | 1,000 | 250 ms | 347 ms |
| 2,000 | 2,000 | 2,000 | 239 ms | 750 ms |
