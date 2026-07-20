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

Run directly from Git with `uvx`, or install into an environment:

```bash
uvx --from git+https://github.com/tai42ai/tai-dynamic-postgres-mcp.git tai42-postgres-mcp
# or
pip install git+https://github.com/tai42ai/tai-dynamic-postgres-mcp.git
```

## Configuration

Connection and pooling are configured via environment variables:

| Variable | Default | Description |
| --- | --- | --- |
| `PG_HOST` | `localhost` | PostgreSQL host |
| `PG_PORT` | `5432` | PostgreSQL port |
| `PG_DB` | _required_ | Database name (no default; startup fails if unset) |
| `PG_USER` | _required_ | Database user, use a least-privilege role (no default) |
| `PG_PASSWORD` | _required_ | Database password (no default; startup fails if unset) |
| `PG_STATEMENT_TIMEOUT` | `30000` | Per-connection `statement_timeout` in ms (`0` disables) |
| `PG_POOL_MIN_SIZE` | `1` | Minimum pooled connections |
| `PG_POOL_MAX_SIZE` | `10` | Maximum pooled connections |
| `PG_POOL_TIMEOUT` | `10` | Pool acquire timeout (seconds) |
| `PG_POOL_MAX_LIFETIME` | `300` | Max connection lifetime (seconds) |
| `TOOLS_DIR` | `~/.cache/tai-dynamic-postgres-mcp/tools` | Where generated tool files are written |

## CLI options

| Flag | Default | Description |
| --- | --- | --- |
| `--overwrite / --no-overwrite` | on | Regenerate the tool files on startup so they reflect the current schema. Pass `--no-overwrite` to reuse existing generated files |
| `--readonly` | off | Generate only `select`/`select_joined` tools |
| `--allow-unfiltered` | off | Allow `update`/`delete` to run without a `WHERE` filter (affects every row) |
| `--select-joined a,b,c` | — | Generate a joined select over the given tables (repeatable) |
| `--ignore-insert-column` | `id` | Column to exclude from insert inputs (repeatable) |
| `--ignore-update-column` | `id` | Column to exclude from update inputs (repeatable) |
| `--ignore-select-column` | — | Column to exclude from select output models (repeatable) |
| `--ignore-select-joined-column` | — | Column to exclude from joined select models (repeatable) |
| `-t, --transport` | `stdio` | `stdio`, `http`, `sse`, or `streamable-http` |
| `--host` | `127.0.0.1` | Bind host (HTTP/SSE transports only) |
| `--port` | `8000` | Bind port (HTTP/SSE transports only) |

## Usage with an MCP client

```json
{
  "mcpServers": {
    "postgres": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/tai42ai/tai-dynamic-postgres-mcp.git",
        "tai42-postgres-mcp",
        "--readonly"
      ],
      "env": {
        "PG_HOST": "localhost",
        "PG_PORT": "5432",
        "PG_DB": "dbname",
        "PG_USER": "agent",
        "PG_PASSWORD": "password"
      }
    }
  }
}
```

## Generated tools

For a table `public.orders` you get (unless `--readonly`):

- `select_public_orders(where, order_by, limit, offset)`
- `insert_public_orders(params, raise_on_conflict)`
- `update_public_orders(data, where)`
- `delete_public_orders(where)`

Column types map to native Python: temporal columns to `datetime`/`date`/`time`,
`uuid` to `uuid.UUID`, `numeric`/`decimal` to `Decimal`, `json`/`jsonb` to `Any`,
and array columns to `list[...]`. `insert` returns the table's real primary key
(a scalar list for a single-column key, a list of lists for a composite key, or
the affected row count when the table has no primary key); columns with a
database default are omittable. `order_by` items accept an optional `nulls`
(`FIRST`/`LAST`); when unset PostgreSQL's default applies.

### Filtering — `WhereFilter`

`select`, `update`, and `delete` accept a `where` argument. Field names must be
real columns of the table; unknown fields are rejected.

```jsonc
// Simple field filters (implicitly ANDed)
{ "status": { "eq": "open" }, "total": { "gte": 100 } }

// Logical composition
{ "AND": [ { "status": { "eq": "open" } },
           { "OR": [ { "total": { "gt": 1000 } }, { "vip": { "eq": true } } ] } ] }
```

Supported operators: `eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `like`, `not_like`,
`ilike`, `not_ilike`, `in`, `not_in`, `between`, `is_null`, and `knn`
(pgvector). Logical keys: `AND`, `OR`, `NOT`.

### Vector search (pgvector)

When a column is a `vector`, filter or order by similarity:

```jsonc
{ "embedding": { "knn": { "query": [0.1, 0.2, 0.3],
                          "distance": "cosine",   // l2 | inner_product | cosine
                          "threshold": 0.5 } } }
```

Requires the `pgvector` extension enabled in the database.

## Docker

```bash
docker build -t tai42-postgres-mcp .
docker run --rm -e PG_HOST=... -e PG_DB=... -e PG_USER=... -e PG_PASSWORD=... \
  tai42-postgres-mcp tai42-postgres-mcp --readonly
```

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md).

```bash
uv sync --extra dev --extra test-integration
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest                 # unit tests
uv run pytest -m integration  # CRUD tests against real Postgres (needs Docker)
uv run --group docs mkdocs build --strict
```

## License

Apache-2.0. See `LICENSE` and `NOTICE`.
