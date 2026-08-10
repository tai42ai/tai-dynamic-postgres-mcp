# tai42-dynamic-postgres-mcp

[![CI](https://github.com/tai42ai/tai-dynamic-postgres-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/tai42ai/tai-dynamic-postgres-mcp/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

Schema-driven generator for **safe, scoped PostgreSQL DML tools** in
[FastMCP](https://github.com/jlowin/fastmcp) agent systems.

Point it at a PostgreSQL database and it introspects the schema and generates one
typed MCP tool per DML operation per table — `insert`, `select`, `update`,
`delete`, plus optional `select_joined`. The agent gets exactly those tools and
nothing else: no raw SQL, no schema changes, no access to tables you didn't
expose.

## Why

Giving an agent database access usually means choosing between read-only access
or risking that the agent runs arbitrary SQL and changes data or settings you
never intended. This project gives you a controlled middle ground: a fixed set
of generated, validated, parameterized tools scoped to the tables and operations
you choose.

## What it does

- Introspects your schema (tables, columns, types, foreign keys, `pgvector`).
- Generates a FastMCP tool per DML operation per table, each with a dedicated
  Pydantic input model derived from the table.
- Supports rich, **injection-safe** filtering (`WhereFilter`) and ordering,
  including vector KNN search.
- Excludes chosen columns from generated tool inputs (e.g. `id`, `created_at`).
- Exposes only the generated tools — never raw SQL or DDL.

## Security model

> [!IMPORTANT]
> The generated tool layer controls *which operations exist*; the PostgreSQL
> role you connect as controls *what those operations can physically do*.
> **Connect as a dedicated least-privilege role, never a superuser.**
> Read [SECURITY.md](SECURITY.md) before deploying.

Highlights, in full detail in [SECURITY.md](SECURITY.md):

- Filter/order field names are validated against real columns and emitted only
  as quoted identifiers; values are always bound parameters. There is no path
  for SQL injection through field names.
- `update`/`delete` require a `WHERE` filter unless the server is started with
  `--allow-unfiltered`.
- The tool layer does **not** provide row-level scoping. Use PostgreSQL
  Row-Level Security and `GRANT`/`REVOKE` for that.

## Installation

Requires **Python 3.13+**. Install from PyPI into the environment that runs the
server, or run it without installing:

```bash
uv add tai42-dynamic-postgres-mcp
uvx --from tai42-dynamic-postgres-mcp tai42-postgres-mcp   # run it without installing
```

Or from source — clone this repo and add it as an editable dependency, or run
the clone with `uvx`:

```bash
git clone https://github.com/tai42ai/tai-dynamic-postgres-mcp   # next to your app checkout
cd /path/to/your/app
uv add --editable ../tai-dynamic-postgres-mcp
uvx --from ../tai-dynamic-postgres-mcp tai42-postgres-mcp
```

## Documentation

Full reference — every connection and pooling variable, the CLI scoping flags,
stdio and HTTP transport wiring, the generated-tool surface, `WhereFilter`
filtering, and pgvector search — lives on the plugin's documentation page. See
also [SECURITY.md](SECURITY.md) before deploying.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md).

```bash
uv venv --python 3.13
uv pip install --no-sources --editable ".[dev,test-integration]"
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync pyright
uv run --no-sync pytest --cov --cov-report=term-missing                 # unit tests
uv run --no-sync pytest -m integration  # CRUD tests against real Postgres (needs Docker)
```

## License

Apache-2.0. See `LICENSE` and `NOTICE`.
