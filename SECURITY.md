# Security Policy

## Reporting a Vulnerability

Please do not report security vulnerabilities through public issues.

Instead, email **oss@tai42.ai** with:

- a description of the issue and its impact,
- steps to reproduce (a proof-of-concept if possible),
- the affected version(s).

We aim to acknowledge reports within a few business days and will keep you
updated on remediation. Please give us a reasonable opportunity to address the
issue before any public disclosure.

## Supported Versions

This project is pre-1.0; security fixes are applied to the latest released
version only.

## The security model

This project generates a fixed set of typed MCP tools from your PostgreSQL
schema and exposes **only those tools** to an agent. There is no raw-SQL tool
and no schema-mutation tool. This document explains what that does and does not
protect, and how to deploy it safely.

### In one sentence

The generated tool layer restricts *which operations exist*; the **PostgreSQL
role you connect as** restricts *what those operations are physically allowed to
do*. Treat the database role as the real boundary and the tool layer as
convenience on top of it.

## Use a least-privilege database role — this is required, not optional

The tools run every statement as the role configured by `PG_USER`. If that role
is a superuser or owns every table, a bug or an unexpected query path inherits
that power. **Do not connect as a superuser.**

Create a dedicated role and grant it only what the agent should be able to do:

```sql
CREATE ROLE agent LOGIN PASSWORD '...';

-- Read-only example
GRANT USAGE ON SCHEMA app TO agent;
GRANT SELECT ON ALL TABLES IN SCHEMA app TO agent;

-- Read/write example: add only the verbs you want
GRANT INSERT, UPDATE, DELETE ON app.events TO agent;
```

Restrict the visible surface further with `search_path` and by exposing only the
schema you intend the agent to touch. Anything the role cannot do, the agent
cannot do — regardless of what tools are generated.

## What the tool layer protects against

- **No arbitrary SQL.** The agent can only call the generated `insert_*`,
  `select_*`, `update_*`, `delete_*`, and `select_joined_*` tools.
- **No identifier injection.** Filter and order-by field names are validated
  against the table's real columns and emitted only as quoted identifiers; an
  unknown field raises an error and never reaches the query as text. All values
  are passed as bound parameters.
- **No accidental full-table writes.** `update_*` and `delete_*` require a
  `WHERE` filter. A call with no filter raises an error unless the server was
  started with `--allow-unfiltered` (see below).
- **No surprise schemas.** Tables/columns whose names are not valid identifiers
  (`[A-Za-z_][A-Za-z0-9_]*`) are rejected at generation time with a clear error
  rather than producing broken or ambiguous code.

## What it does NOT protect against

- **Row-level scoping / multi-tenancy.** Nothing here confines an agent to a
  subset of rows. If one agent must not read or modify another tenant's rows,
  enforce that with PostgreSQL Row-Level Security (RLS) policies on the role.
- **Column-level secrets.** `ignore_*` flags shape the generated tool inputs and
  models; they are not an access-control mechanism. Use `GRANT`/`REVOKE` at the
  column level for real restriction.
- **Resource exhaustion.** A `select_*` with no `limit` can return large result
  sets. Apply statement timeouts and connection limits on the role.

## The `--allow-unfiltered` flag

By default, unfiltered `update`/`delete` calls raise. Starting the server with
`--allow-unfiltered` disables that guard for every generated `update`/`delete`
tool, so an agent can modify or remove all rows of a table in one call. This is
an operator decision made at launch — the agent cannot enable it itself. Enable
it only when you understand the blast radius.
