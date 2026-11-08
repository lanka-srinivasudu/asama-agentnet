from __future__ import annotations

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
       {"pool_size": 10, "max_overflow": 20}),
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
    if settings.is_postgres:
        with engine.begin() as conn:
            # Approximate nearest-neighbour index for out-of-network candidate sourcing.
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_posts_embedding_hnsw "
                "ON posts USING hnsw (embedding vector_cosine_ops)"
            ))


def get_session() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
