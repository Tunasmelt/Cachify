import pytest

from pcg.config import AuthStyle, Settings, UpstreamConfig, WireFormat
from pcg.upstreams import UnknownUpstreamError, resolve_default_upstream, resolve_upstream


def make_upstream() -> UpstreamConfig:
    return UpstreamConfig(
        wire_format=WireFormat.ANTHROPIC_MESSAGES,
        base_url="https://provider.example.invalid",
        auth_style=AuthStyle.X_API_KEY,
    )


@pytest.mark.proxy
def test_resolves_configured_upstream_by_name() -> None:
    upstream = make_upstream()

    assert resolve_upstream({"primary": upstream}, "primary", None) == ("primary", upstream)


@pytest.mark.proxy
def test_resolves_default_when_no_name_is_requested() -> None:
    upstream = make_upstream()

    assert resolve_upstream({"primary": upstream}, None, "primary") == ("primary", upstream)


@pytest.mark.proxy
def test_unrecognized_requested_name_raises() -> None:
    with pytest.raises(UnknownUpstreamError, match="missing"):
        resolve_upstream({"primary": make_upstream()}, "missing", "primary")


@pytest.mark.proxy
def test_no_requested_name_or_default_raises() -> None:
    with pytest.raises(UnknownUpstreamError, match="default"):
        resolve_upstream({"primary": make_upstream()}, None, None)


@pytest.mark.proxy
def test_settings_wrapper_resolves_default() -> None:
    upstream = make_upstream()
    settings = Settings(
        _env_file=None,
        upstreams={"primary": upstream},
        default_upstream="primary",
    )

    assert resolve_default_upstream(settings, None) == ("primary", upstream)
