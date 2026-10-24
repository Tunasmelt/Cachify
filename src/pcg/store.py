from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from sqlalchemy import (
    Boolean,
    Column,
    Float,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    Table,
    Text,
    create_engine,
    func,
    select,
)
from sqlalchemy.engine import Engine

metadata = MetaData()
requests = Table(
    "requests",
    metadata,
    Column("id", Text, primary_key=True),
    Column("created_at", Text, nullable=False),
    Column("tenant_hash", Text, nullable=False),
    Column("session_id", Text, nullable=True),
    Column("upstream", Text, nullable=False),
    Column("wire_format", Text, nullable=False),
    Column("cache_mode", Text, nullable=False),
    Column("model", Text, nullable=False),
    Column("stream", Integer, nullable=False),
    Column("upstream_status", Integer, nullable=True),
    Column("complete", Integer, nullable=True),
    Column("truncated", Integer, nullable=False, server_default="0"),
    Column("input_tokens", Integer, nullable=True),
    Column("output_tokens", Integer, nullable=True),
    Column("cache_write_tokens", Integer, nullable=True),
    Column("cache_read_tokens", Integer, nullable=True),
    Column("latency_ms", Float, nullable=False),
    Column("log_mode", Text, nullable=False),
    Column("predecessor_id", Text, nullable=True),
    Column("lineage_source", Text, nullable=True),
    Index("ix_requests_tenant_hash_created_at", "tenant_hash", "created_at"),
    Index("ix_requests_tenant_hash_session_id", "tenant_hash", "session_id", "created_at"),
)
segments = Table(
    "segments",
    metadata,
    Column("request_id", Text, nullable=False),
    Column("idx", Integer, nullable=False),
    Column("kind", Text, nullable=False),
    Column("role", Text, nullable=True),
    Column("raw_hash", Text, nullable=False),
    Column("canon_hash", Text, nullable=False),
    Column("byte_len", Integer, nullable=False),
    Column("has_breakpoint", Boolean, nullable=False),
    PrimaryKeyConstraint("request_id", "idx"),
)


@dataclass(frozen=True)
class RequestRecord:
    id: str
    created_at: str
    tenant_hash: str
    session_id: str | None
    upstream: str
    wire_format: str
    cache_mode: str
    model: str
    stream: int
    upstream_status: int | None
    complete: int | None
    truncated: int
    input_tokens: int | None
    output_tokens: int | None
    cache_write_tokens: int | None
    cache_read_tokens: int | None
    latency_ms: float
    log_mode: str
    predecessor_id: str | None = None
    lineage_source: str | None = None


@dataclass(frozen=True)
class SegmentRow:
    request_id: str
    idx: int
    kind: str
    role: str | None
    raw_hash: str
    canon_hash: str
    byte_len: int
    has_breakpoint: bool


class RequestStore:
    def __init__(self, db_url: str) -> None:
        connect_args = {"check_same_thread": False} if db_url.startswith("sqlite") else {}
        self.db_url = db_url
        self.engine: Engine = create_engine(db_url, connect_args=connect_args)

    def migrate(self) -> None:
        from alembic import command
        from alembic.config import Config

        root = Path(__file__).resolve().parents[2]
        config = Config(str(root / "alembic.ini"))
        config.set_main_option("script_location", str(root / "migrations"))
        config.set_main_option("sqlalchemy.url", self.db_url.replace("%", "%%"))
        command.upgrade(config, "head")

    def insert(self, record: RequestRecord, segment_rows: Sequence[SegmentRow] = ()) -> None:
        with self.engine.begin() as connection:
            connection.execute(requests.insert().values(**asdict(record)))
            if segment_rows:
                connection.execute(segments.insert(), [asdict(row) for row in segment_rows])

    def latest_in_session(self, tenant_hash: str, session_id: str) -> str | None:
        query = (
            select(requests.c.id)
            .where(requests.c.tenant_hash == tenant_hash, requests.c.session_id == session_id)
            .order_by(requests.c.created_at.desc())
            .limit(1)
        )
        with self.engine.connect() as connection:
            return connection.scalar(query)

    def recent_segment_hashes(
        self, tenant_hash: str, model: str, since: str, limit: int
    ) -> list[tuple[str, list[str]]]:
        """Newest-first (request id, ordered raw hashes) of recent same-tenant, same-model rows."""
        recent = (
            select(requests.c.id)
            .where(
                requests.c.tenant_hash == tenant_hash,
                requests.c.model == model,
                requests.c.created_at >= since,
            )
            .order_by(requests.c.created_at.desc())
            .limit(limit)
        )
        with self.engine.connect() as connection:
            ids: list[str] = list(connection.scalars(recent))
            if not ids:
                return []
            rows = connection.execute(
                select(segments.c.request_id, segments.c.raw_hash)
                .where(segments.c.request_id.in_(ids))
                .order_by(segments.c.request_id, segments.c.idx)
            )
            by_request: dict[str, list[str]] = {}
            for request_id, raw_hash in rows:
                by_request.setdefault(request_id, []).append(raw_hash)
        return [(request_id, by_request.get(request_id, [])) for request_id in ids]

    def count(self) -> int:
        with self.engine.connect() as connection:
            return int(connection.scalar(select(func.count()).select_from(requests)) or 0)

    def dispose(self) -> None:
        self.engine.dispose()


__all__ = ["RequestRecord", "RequestStore", "SegmentRow", "metadata", "requests", "segments"]
