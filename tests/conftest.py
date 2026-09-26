import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--anthropic-live",
        action="store_true",
        default=False,
        help="run integration tests against live Anthropic services",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--anthropic-live"):
        return

    skip_integration = pytest.mark.skip(reason="requires --anthropic-live")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_integration)
