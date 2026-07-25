# tai42-dynamic-postgres-mcp

Schema-driven generator for **safe, scoped PostgreSQL DML tools** in
[FastMCP](https://github.com/jlowin/fastmcp) agent systems.

Point it at a PostgreSQL database and it introspects the schema and generates
one typed MCP tool per DML operation per table — `insert`, `select`, `update`,
`delete`, plus optional `select_joined`. The agent gets exactly those tools and
nothing else: no raw SQL, no schema changes, no access to tables you did not
expose.

## Security model

The generated tool layer controls *which operations exist*; the PostgreSQL role
you connect as controls *what those operations can physically do*. Connect as a
dedicated least-privilege role, never a superuser. Filter and order-by field
names are validated against real columns and emitted only as quoted identifiers;
all values are bound parameters. See
[SECURITY.md](https://github.com/tai42ai/tai-dynamic-postgres-mcp/blob/main/SECURITY.md).

## Pages

- **CLI** — the command-line entry point and its flags.
- **Config** — connection, pool, and statement-timeout settings.
- **Generation** — schema introspection, type mapping, and the DML tool
  builders.
