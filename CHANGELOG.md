# Changelog

All notable changes to `tai42-dynamic-postgres-mcp` are documented here; the format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Until 1.0.0 the API is not stable: **minor (0.x) releases may contain breaking
changes.**

## [Unreleased]

First release (0.1.0) in preparation — nothing published yet.

### Added

- Schema introspection that generates one FastMCP tool per DML operation per
  table: `insert`, `select`, `update`, `delete`, plus optional `select_joined`.
- A dedicated Pydantic input model per tool, derived from the table's columns.
- `WhereFilter` filtering with comparison, `in`/`not_in`, `between`, `is_null`,
  `like`/`ilike`, logical `AND`/`OR`/`NOT`, and pgvector KNN operators; plus
  ordering with `OrderByItem` (including KNN ordering).
- `OFFSET` pagination on `select`/`select_joined` (alongside `limit`).
- `NULLS FIRST`/`NULLS LAST` control on `order_by` items (defaults to
  PostgreSQL's placement when unset).
- `--allow-unfiltered` CLI flag (operator-controlled; agents cannot enable it).
- Column exclusion flags for insert/select/update/joined tools.
- Configurable per-connection `statement_timeout` (`PG_STATEMENT_TIMEOUT`,
  default 30000 ms), applied to every pooled connection.
- Read-only `select` tools are generated for views and materialized views;
  partitioned tables are treated as ordinary tables.
- User-defined enum columns are supported (mapped to `str`).
- `stdio`, `http`, `sse`, and `streamable-http` transports.
- Container image release workflow: `v*` tags build and push a provenance-signed
  image to GHCR.
- Column types map to native Python types: temporal → `datetime`/`date`/
  `time`, `uuid` → `uuid.UUID`, `numeric`/`decimal` → `Decimal`, `json`/`jsonb`
  → `Any`, and array columns → `list[...]`.
- `insert` returns the table's actual primary key (scalar list, list of lists
  for a composite key, or the row count when there is no primary key). Columns
  with a database default are omittable on insert and the default is applied
  when omitted.
- `insert`/`update` choose the value adapter by the introspected column type
  (json/jsonb → `Json`; arrays are passed natively), so array columns are not
  JSON-wrapped.
- `select_joined` builds its SELECT/FROM/JOIN entirely from quoted identifiers,
  and join aliases are schema-qualified.
- The pool is opened/closed via the app lifespan; the pool is only closed if one
  was actually created; write templates use an explicit transaction that commits
  on success and rolls back on error; reads do not issue a needless commit.
- `--overwrite` defaults on (`--no-overwrite` to opt out) so a restart
  regenerates tools to match the current schema.
- Licensed under Apache-2.0. Requires `fastmcp>=3` and `pydantic>=2.12`.
- Filter and order-by field names are validated against the table's real
  columns and emitted only as quoted identifiers; unknown fields raise. Values
  are always bound parameters, so there is no SQL injection path through field
  names.
- `update`/`delete` require a `WHERE` filter; an unfiltered call raises unless
  the server is started with `--allow-unfiltered`.
- The database name, user, and password are required settings with no defaults;
  a missing value raises at startup (no silent connect with a default password).
  The password is a `SecretStr` and the conninfo is built with
  `psycopg.conninfo.make_conninfo` (escaped) and never logged.
- A supplied-but-empty `where` filter raises instead of silently matching every
  row; an empty `update` payload raises; `--readonly` never serves stale write
  tools (the output dir is pruned to the current tool set); table names that
  flatten to the same tool name raise a collision error.
- Schema identifiers that are not valid identifiers
  (`[A-Za-z_][A-Za-z0-9_]*`) are rejected at generation time with a clear error.
- A filter that mixes logical keys (`AND`/`OR`/`NOT`) with field filters in the
  same object is rejected, rather than silently dropping the field filters.
- Unknown filter operators, empty `in`/`not_in` lists, and wrong-arity `between`
  are rejected at validation instead of producing empty or invalid SQL.
- Unmapped PostgreSQL column types raise at generation instead of silently
  becoming `Any`; identifiers that are reserved Python words are also rejected.

### Notes

- The schema is read as structured data directly (no DDL text round-trip), so
  composite (multi-column) foreign keys and parameterized array types
  (e.g. `numeric(10,2)[]`) are handled correctly.
- Ships a `py.typed` marker (PEP 561) for downstream type checkers.
