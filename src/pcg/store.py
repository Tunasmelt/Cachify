from dataclasses import asdict, dataclass
from pathlib import Path

from sqlalchemy import (
    Column,
    Float,
    Index,
    Integer,
    MetaData,
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
    Index("ix_requests_tenant_hash_created_at", "tenant_hash", "created_at"),
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

    def insert(self, record: RequestRecord) -> None:
        with self.engine.begin() as connection:
            connection.execute(requests.insert().values(**asdict(record)))

    def count(self) -> int:
        with self.engine.connect() as connection:
            return int(connection.scalar(select(func.count()).select_from(requests)) or 0)

    def dispose(self) -> None:
        self.engine.dispose()


__all__ = ["RequestRecord", "RequestStore", "metadata", "requests"]
