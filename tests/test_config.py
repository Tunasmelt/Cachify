from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from pcg.config import AuthStyle, Settings, WireFormat, get_settings

ENV_VARS = (
    "PCG_UPSTREAMS",
    "PCG_DEFAULT_UPSTREAM",
    "PCG_ADMIN_TOKEN",
    "PCG_DB_URL",
    "PCG_VECTOR_BACKEND",
    "PCG_VECTOR_URL",
    "PCG_VECTOR_API_KEY",
    "PCG_EMBED_MODEL",
    "PCG_SIM_THRESHOLD",
    "PCG_CACHE_TTL_S",
    "PCG_PROVIDER_CACHE_TTL_S",
    "PCG_LOG_MODE",
    "PCG_BODY_RETENTION_H",
)


@pytest.fixture(autouse=True)
def clean_settings_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()


def test_defaults_load_without_environment() -> None:
    settings = Settings(_env_file=None)

    assert settings.upstreams == {}
    assert settings.default_upstream is None
    assert settings.admin_token is None
    assert settings.db_url == "sqlite:///./pcg.db"
    assert settings.vector_backend == "memory"
    assert settings.vector_url is None
    assert settings.vector_api_key is None
    assert settings.embed_model == "BAAI/bge-small-en-v1.5"
    assert settings.sim_threshold == 0.85
    assert settings.cache_ttl_s == 3600
    assert settings.provider_cache_ttl_s == 300
    assert settings.log_mode == "hash"
    assert settings.body_retention_h == 24


def test_environment_overrides_dotenv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "PCG_LOG_MODE=off\nPCG_CACHE_TTL_S=42\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PCG_LOG_MODE", "full")

    settings = Settings()

    assert settings.log_mode == "full"
    assert settings.cache_ttl_s == 42


def test_upstreams_json_parses_into_typed_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "PCG_UPSTREAMS",
        '{"primary":{"wire_format":"anthropic-messages",'
        '"base_url":"https://provider.example.invalid",'
        '"auth_style":"x-api-key"}}',
    )

    upstream = Settings(_env_file=None).upstreams["primary"]

    assert upstream.wire_format is WireFormat.ANTHROPIC_MESSAGES
    assert upstream.base_url == "https://provider.example.invalid"
    assert upstream.auth_style is AuthStyle.X_API_KEY


def test_invalid_upstreams_json_raises_validation_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PCG_UPSTREAMS", "not-json")

    with pytest.raises(ValidationError, match="PCG_UPSTREAMS|upstreams"):
        Settings(_env_file=None)


def test_external_vector_backend_requires_url() -> None:
    with pytest.raises(ValidationError, match="vector_url is required for qdrant backend"):
        Settings(_env_file=None, vector_backend="qdrant")


@pytest.mark.parametrize("threshold", [-0.01, 1.01])
def test_similarity_threshold_must_be_between_zero_and_one(threshold: float) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, sim_threshold=threshold)


def test_secrets_are_masked_in_string_representations() -> None:
    admin_token = "admin-secret-value"  # noqa: S105 - synthetic test value
    vector_api_key = "vector-secret-value"
    settings = Settings(
        _env_file=None,
        admin_token=admin_token,
        vector_api_key=vector_api_key,
    )

    assert isinstance(settings.admin_token, SecretStr)
    for rendered in (repr(settings), str(settings)):
        assert admin_token not in rendered
        assert vector_api_key not in rendered


def test_get_settings_caches_only_factory_instances(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(Path(__file__).parent)

    cached = get_settings()

    assert get_settings() is cached
    assert Settings(_env_file=None) is not cached
