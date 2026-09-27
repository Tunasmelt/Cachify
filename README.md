# Cachify

![CI](https://github.com/Tunasmelt/Cachify/actions/workflows/ci.yml/badge.svg)

Cachify (`pcg`) is a prompt cache gateway for diagnosing cache busts and serving safe semantic
cache hits.

## Development

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).
Configuration is read automatically from environment variables and an optional `.env` file.

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

## Contributing

Use one branch and one pull request per milestone. Pull requests into `main` are required, and CI
must be green before merge.

## License

MIT — see [LICENSE](LICENSE).
