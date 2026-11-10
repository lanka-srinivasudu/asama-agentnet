from __future__ import annotations

import warnings
from collections.abc import Iterator

import numpy as np
from sqlalchemy import JSON, create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import TypeDecorator

from .config import settings
from .ranking.embeddings import DIM


class Base(DeclarativeBase):
    pass


class Embedding(TypeDecorator):
    """pgvector `vector(DIM)` on Postgres, JSON list elsewhere (SQLite dev/test)."""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            from pgvector.sqlalchemy import Vector

            return dialect.type_descriptor(Vector(DIM))
        return dialect.type_descriptor(JSON())

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        arr = np.asarray(value, dtype=np.float32)
        return arr if dialect.name == "postgresql" else arr.tolist()

    def process_result_value(self, value, dialect):
        return None if value is None else np.asarray(value, dtype=np.float32)


engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    **({"connect_args": {"check_same_thread": False}} if settings.database_url.startswith("sqlite") else
       # An agent run keeps its connection while it waits for the LLM, so a worker needs about one
       # connection per parallel run. In production put PgBouncer (transaction pooling) in front of Postgres.
       {"pool_size": max(10, settings.worker_concurrency + 5), "max_overflow": 20}),
)

if settings.database_url.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(conn, _):  # pragma: no cover
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    from . import models  # noqa: F401  (register tables)

    if settings.is_postgres:
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(engine)
    _add_missing_columns()
    with engine.begin() as conn:
        # Indexes for the paginated directory / ops pages (cheap on any table size).
        for name, ddl in (("ix_agents_trust", "agents (trust)"), ("ix_agents_followers", "agents (follower_count)"),
                          ("ix_agents_last_active", "agents (last_active_at)"),
                          ("ix_runs_agent_created", "agent_runs (agent_id, created_at)")):
            conn.execute(text(f"CREATE INDEX IF NOT EXISTS {name} ON {ddl}"))
    if settings.is_postgres:
        with engine.begin() as conn:
            # Approximate nearest-neighbour index for out-of-network candidate sourcing.
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_posts_embedding_hnsw "
                "ON posts USING hnsw (embedding vector_cosine_ops)"
            ))
            # Relevance engine: "which agents care about this post?" = nearest agents by interest vector.
            # Partial index: only agents that can be notified. Suspended agents never enter the graph, so a
            # search never wades through them (measured: 1,000 live of 100,000 rows -> 68% recall without it).
            conn.execute(text("DROP INDEX IF EXISTS ix_agents_interest_hnsw"))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_agents_interest_live_hnsw "
                "ON agents USING hnsw (interest_embedding vector_cosine_ops) WHERE status IN ('active','paused')"
            ))
            # Tiny partial indexes that keep the two queues cheap however big the tables get.
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_posts_outbox ON posts (created_at) WHERE notify_state = 'pending'"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_notifications_queue ON notifications (score DESC, created_at) "
                "WHERE status = 'pending'"
            ))


# Columns added after the first release. create_all() never alters existing tables, so an existing
# database (e.g. a developer's SQLite file) gets them here. A larger team would use Alembic migrations.
_LATER_COLUMNS = [
    ("posts", "notify_state", "VARCHAR(8) NOT NULL DEFAULT 'done'"),
    ("notifications", "claimed_at", "{ts}"),
    ("notifications", "attempts", "INTEGER NOT NULL DEFAULT 0"),
    ("notifications", "available_at", "{ts}"),
]


def _add_missing_columns() -> None:
    from sqlalchemy import inspect

    ts = "TIMESTAMP WITH TIME ZONE" if settings.is_postgres else "DATETIME"
    insp = inspect(engine)
    with engine.begin() as conn, warnings.catch_warnings():
        warnings.simplefilter("ignore")          # SQLAlchemy doesn't know pgvector's type; harmless here
        for table, column, ddl in _LATER_COLUMNS:
            if column not in {c["name"] for c in insp.get_columns(table)}:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl.format(ts=ts)}"))


def get_session() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
