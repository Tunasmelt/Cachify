import json
from enum import StrEnum
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class WireFormat(StrEnum):
    ANTHROPIC_MESSAGES = "anthropic-messages"
    OPENAI_CHAT = "openai-chat"
    OPENAI_RESPONSES = "openai-responses"
    GEMINI = "gemini"


class AuthStyle(StrEnum):
    X_API_KEY = "x-api-key"
    BEARER = "bearer"
    X_GOOG_API_KEY = "x-goog-api-key"


class UpstreamConfig(BaseModel):
    wire_format: WireFormat
    base_url: str = Field(min_length=1)
    auth_style: AuthStyle


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PCG_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    upstreams: Annotated[dict[str, UpstreamConfig], NoDecode] = Field(default_factory=dict)
    default_upstream: str | None = None
    admin_token: SecretStr | None = None
    tenant_hash_salt: SecretStr | None = None
    db_url: str = "sqlite:///./pcg.db"
    vector_backend: Literal["qdrant", "pinecone", "upstash", "memory"] = "memory"
    vector_url: str | None = None
    vector_api_key: SecretStr | None = None
    embed_model: str = "BAAI/bge-small-en-v1.5"
    sim_threshold: float = Field(default=0.85, ge=0, le=1)
    cache_ttl_s: int = Field(default=3600, gt=0)
    provider_cache_ttl_s: int = Field(default=300, gt=0)
    log_mode: Literal["off", "hash", "full"] = "hash"
    body_retention_h: int = Field(default=24, gt=0)

    @field_validator("upstreams", mode="before")
    @classmethod
    def parse_upstreams(cls, value: object) -> object:
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError as error:
                raise ValueError("upstreams must be a valid JSON object") from error
        return value

    @field_validator("tenant_hash_salt")
    @classmethod
    def validate_tenant_hash_salt(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and len(value.get_secret_value()) < 32:
            raise ValueError("tenant_hash_salt must be at least 32 characters")
        return value

    @model_validator(mode="after")
    def require_vector_url(self) -> "Settings":
        if self.vector_backend != "memory" and not self.vector_url:
            raise ValueError(f"vector_url is required for {self.vector_backend} backend")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
