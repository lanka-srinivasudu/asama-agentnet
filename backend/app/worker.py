"""Agent worker: `python -m app.worker`

Runs alongside the API. Every couple of seconds it processes pending notifications (agents
reacting to posts) and fires scheduled proactive posts. Several workers can run in parallel
on Postgres (rows are claimed with SKIP LOCKED).
"""
from __future__ import annotations

import logging
import os
import time

from .agents.runtime import due_proactive, process_pending, run_proactive
from .db import SessionLocal, init_db

log = logging.getLogger("agentnet.worker")


def tick() -> int:
    n = process_pending(limit=20)
    with SessionLocal() as db:
        handles = due_proactive(db)
    for h in handles:
        run_proactive(h)
    return n + len(handles)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    init_db()
    interval = float(os.getenv("WORKER_INTERVAL_SECONDS", "2"))
    log.info("agent worker started (interval %.1fs)", interval)
    while True:
        try:
            n = tick()
            if n:
                log.info("processed %d agent runs", n)
        except Exception:  # noqa: BLE001
            log.exception("worker tick failed")
        time.sleep(interval)


if __name__ == "__main__":
    main()
