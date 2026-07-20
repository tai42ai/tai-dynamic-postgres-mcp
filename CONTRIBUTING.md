# Contributing to tai-dynamic-postgres-mcp

`tai-dynamic-postgres-mcp` generates **safe, scoped PostgreSQL DML tools** for
FastMCP agent systems: it introspects a database schema and emits one typed MCP
tool per DML operation per table. The rule that shapes everything: **the agent
never sees raw SQL.** Identifiers come from a validated allowlist and are emitted
as quoted `psycopg.sql.Identifier` values; values are always bound parameters.

## Ground rules

- **Security first.** This server's value is its scoped, injection-safe access to
  PostgreSQL. SQL identifiers must only ever be emitted as quoted
  `psycopg.sql.Identifier` values drawn from a validated allowlist — never
  interpolated as raw text. Values must always be bound parameters. Add a test
  for any new filter/order/operator path, including a rejection test.
- **Fail loudly.** Errors must propagate. Do not swallow exceptions, silently
  drop input, or fall back in a way that hides a failure.
- **Typed package** (`py.typed`). Pyright runs clean in strict mode.
- **Changes to the tool contract or CLI** (tool signatures, generated behavior,
  environment variables, flags) are called out explicitly in the PR description
  and in `CHANGELOG.md` under "Unreleased", since they affect how users
  integrate.
- Keep comments describing what the code does now, not its history.

## Layout

- `cli/main.py` — the console-script entry point and its flags.
- `core/app.py` — the FastMCP app assembly.
- `config/settings.py` — the environment-driven settings.
- `database/` — the psycopg connection and the shared query helpers.
- `gen/schema/` — schema introspection (`introspect.py`) and the codegen driver.
- `gen/builders/` — one generator per operation: `insert`, `select`,
  `select_joined`, `update`, `delete`.
- `gen/templates/` — the SQL statement templates the builders render.
- `gen/filters/`, `gen/order/` — the `WhereFilter` and ordering models plus their
  allowlist-checked SQL builders.
- `gen/vector.py`, `gen/loader.py` — pgvector search support and tool loading.
- `docs/` — the mkdocs site sources.
- `tests/` — unit tests plus the `integration`-marked CRUD suite.

## Dev

The project uses [uv](https://docs.astral.sh/uv/) and requires Python 3.13+.
All of these must pass before a PR is merged; CI runs them on every push.

```bash
uv sync --extra dev --extra test-integration
uv run ruff check .                          # lint
uv run ruff format --check .                 # format
uv run pyright                               # type check (strict)
uv run pytest                                # unit tests (no Docker needed)
uv run pytest -m integration                 # CRUD tests against a real Postgres (needs Docker)
uv run --group docs mkdocs build --strict    # docs
```

Before any commit, run a secret scan over `src/` and `tests/` (e.g.
`detect-secrets scan`).

## License

By contributing you agree your contributions are licensed under Apache-2.0.
