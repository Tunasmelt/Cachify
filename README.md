# Cachify

Cachify (`pcg`) is a prompt cache gateway for diagnosing cache busts and serving safe semantic
cache hits.

## Development

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```sh
uv sync
```

Run the local gates:

```sh
uv run ruff check .
uv run ruff format --check .
uv run mypy src/
uv run pytest -q
uv run python -c "import pcg"
```

## License

MIT — see [LICENSE](LICENSE).
