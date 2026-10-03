from pcg.config import Settings, UpstreamConfig


class UnknownUpstreamError(Exception):
    """Raised when an upstream name cannot be resolved from the registry."""


def resolve_upstream(
    upstreams: dict[str, UpstreamConfig],
    requested: str | None,
    default: str | None,
) -> tuple[str, UpstreamConfig]:
    name = requested if requested is not None else default
    if name is None:
        raise UnknownUpstreamError("No default upstream is configured")

    try:
        return name, upstreams[name]
    except KeyError as error:
        raise UnknownUpstreamError(f"Unknown upstream: {name!r}") from error


def resolve_default_upstream(
    settings: Settings, requested: str | None
) -> tuple[str, UpstreamConfig]:
    return resolve_upstream(settings.upstreams, requested, settings.default_upstream)
