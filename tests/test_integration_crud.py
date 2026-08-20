"""End-to-end CRUD tests against a real PostgreSQL instance.

Marked `integration` and skipped automatically when Docker / testcontainers
are unavailable. Run with: pytest -m integration
"""

import datetime
import uuid
from decimal import Decimal

import pytest
from pydantic import BaseModel

from tai42_mcp_dynamic_postgres.gen.filters.models import WhereFilter

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def pg_dsn():
    testcontainers = pytest.importorskip("testcontainers.postgres")
    # pgvector image so the KNN / vector-column paths can run.
    with testcontainers.PostgresContainer("pgvector/pgvector:pg16") as pg:
        yield (
            f"host={pg.get_container_host_ip()} "
            f"port={pg.get_exposed_port(5432)} "
            f"dbname={pg.dbname} "
            f"user={pg.username} "
            f"password={pg.password}"
        )


@pytest.fixture
async def db(pg_dsn, monkeypatch):
    import tai42_mcp_dynamic_postgres.database.connection as conn_mod

    # Build the pool against the container by patching the conninfo builder, then
    # reset the process-wide singleton so it is rebuilt against the container.
    monkeypatch.setattr(conn_mod, "_build_conninfo", lambda: pg_dsn)
    await conn_mod.close_connection_pool()

    from tai42_mcp_dynamic_postgres.database.connection import close_connection_pool, cursor

    async with cursor() as cur:
        await cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        await cur.execute("DROP TABLE IF EXISTS public.items")
        await cur.execute("CREATE TABLE public.items (id serial PRIMARY KEY, name text, qty integer)")
        await cur.connection.commit()

    yield

    await close_connection_pool()


COLS = ["id", "name", "qty"]


class ItemUpdate(BaseModel):
    name: str | None = None
    qty: int | None = None


async def test_insert_select_update_delete_roundtrip(db):
    from tai42_mcp_dynamic_postgres.gen.templates.delete import delete_tmpl
    from tai42_mcp_dynamic_postgres.gen.templates.insert import insert_tmpl
    from tai42_mcp_dynamic_postgres.gen.templates.select import select_tmpl
    from tai42_mcp_dynamic_postgres.gen.templates.update import update_tmpl

    ids = await insert_tmpl("public.items", ["name", "qty"], [("a", 1), ("b", 2)], pk_columns=["id"])
    assert len(ids) == 2

    rows = await select_tmpl("public.items", COLS, where=WhereFilter.model_validate({"name": {"eq": "a"}}))
    assert [r["qty"] for r in rows] == [1]

    updated = await update_tmpl(
        "public.items",
        COLS,
        ItemUpdate(qty=9),
        where=WhereFilter.model_validate({"name": {"eq": "a"}}),
    )
    assert updated == 1

    rows = await select_tmpl("public.items", COLS, where=WhereFilter.model_validate({"qty": {"gte": 5}}))
    assert [r["name"] for r in rows] == ["a"]

    deleted = await delete_tmpl("public.items", COLS, where=WhereFilter.model_validate({"name": {"eq": "b"}}))
    assert deleted == 1

    remaining = await select_tmpl("public.items", COLS)
    assert len(remaining) == 1


async def test_select_limit_and_offset(db):
    from tai42_mcp_dynamic_postgres.gen.order.models import OrderByItem
    from tai42_mcp_dynamic_postgres.gen.templates.insert import insert_tmpl
    from tai42_mcp_dynamic_postgres.gen.templates.select import select_tmpl

    await insert_tmpl("public.items", ["name", "qty"], [("a", 1), ("b", 2), ("c", 3)], pk_columns=["id"])
    ordered = [OrderByItem(field="qty", direction="ASC")]
    page = await select_tmpl("public.items", COLS, order_by=ordered, limit=1, offset=1)
    assert [r["qty"] for r in page] == [2]


async def test_unfiltered_update_blocked_by_default(db):
    from tai42_mcp_dynamic_postgres.gen.templates.update import update_tmpl

    with pytest.raises(ValueError, match="without a WHERE filter"):
        await update_tmpl("public.items", COLS, ItemUpdate(qty=0))


async def test_unfiltered_delete_blocked_by_default(db):
    from tai42_mcp_dynamic_postgres.gen.templates.delete import delete_tmpl

    with pytest.raises(ValueError, match="without a WHERE filter"):
        await delete_tmpl("public.items", COLS)


async def test_unfiltered_delete_allowed_with_flag(db):
    from tai42_mcp_dynamic_postgres.gen.templates.delete import delete_tmpl
    from tai42_mcp_dynamic_postgres.gen.templates.insert import insert_tmpl

    await insert_tmpl("public.items", ["name", "qty"], [("x", 1), ("y", 2)], pk_columns=["id"])
    deleted = await delete_tmpl("public.items", COLS, allow_unfiltered=True)
    assert deleted >= 2


async def test_unfiltered_update_allowed_with_flag(db):
    from tai42_mcp_dynamic_postgres.gen.templates.insert import insert_tmpl
    from tai42_mcp_dynamic_postgres.gen.templates.update import update_tmpl

    await insert_tmpl("public.items", ["name", "qty"], [("a", 1), ("b", 2)], pk_columns=["id"])
    updated = await update_tmpl("public.items", COLS, ItemUpdate(qty=0), allow_unfiltered=True)
    assert updated >= 2


async def test_insert_default_column_applies_db_default(db):
    from tai42_mcp_dynamic_postgres.database.connection import cursor
    from tai42_mcp_dynamic_postgres.gen.templates.insert import insert_tmpl
    from tai42_mcp_dynamic_postgres.gen.templates.select import select_tmpl

    async with cursor() as cur:
        await cur.execute("DROP TABLE IF EXISTS public.defs")
        await cur.execute("CREATE TABLE public.defs (id serial PRIMARY KEY, n integer NOT NULL DEFAULT 7)")
        await cur.connection.commit()

    # n omitted (not in the provided set) with a DB default -> the default (7)
    # is applied, not NULL.
    await insert_tmpl(
        "public.defs", ["n"], [(None,)], default_columns=["n"], pk_columns=["id"], provided_fields=[set()]
    )
    rows = await select_tmpl("public.defs", ["id", "n"])
    assert [r["n"] for r in rows] == [7]


async def test_insert_explicit_null_overrides_default(db):
    from tai42_mcp_dynamic_postgres.database.connection import cursor
    from tai42_mcp_dynamic_postgres.gen.templates.insert import insert_tmpl
    from tai42_mcp_dynamic_postgres.gen.templates.select import select_tmpl

    async with cursor() as cur:
        await cur.execute("DROP TABLE IF EXISTS public.defs_null")
        await cur.execute("CREATE TABLE public.defs_null (id serial PRIMARY KEY, n integer DEFAULT 7)")
        await cur.connection.commit()

    # n explicitly sent as None (in the provided set) -> NULL is written, even
    # though the column has a DB default. Omitting n applies the default (7).
    await insert_tmpl(
        "public.defs_null", ["n"], [(None,)], default_columns=["n"], pk_columns=["id"], provided_fields=[{"n"}]
    )
    await insert_tmpl(
        "public.defs_null", ["n"], [(None,)], default_columns=["n"], pk_columns=["id"], provided_fields=[set()]
    )
    from tai42_mcp_dynamic_postgres.gen.order.models import OrderByItem

    rows = await select_tmpl("public.defs_null", ["id", "n"], order_by=[OrderByItem(field="id", direction="ASC")])
    assert [r["n"] for r in rows] == [None, 7]


async def test_update_explicit_null_sets_null(db):
    from tai42_mcp_dynamic_postgres.database.connection import cursor
    from tai42_mcp_dynamic_postgres.gen.templates.insert import insert_tmpl
    from tai42_mcp_dynamic_postgres.gen.templates.select import select_tmpl
    from tai42_mcp_dynamic_postgres.gen.templates.update import update_tmpl

    async with cursor() as cur:
        await cur.execute("DROP TABLE IF EXISTS public.upd_null")
        await cur.execute("CREATE TABLE public.upd_null (id serial PRIMARY KEY, name text, qty integer)")
        await cur.connection.commit()

    ids = await insert_tmpl("public.upd_null", ["name", "qty"], [("a", 5)], pk_columns=["id"])

    # Explicit null -> SET name = NULL; qty omitted -> left unchanged (still 5).
    updated = await update_tmpl(
        "public.upd_null",
        ["id", "name", "qty"],
        ItemUpdate(name=None),
        where=WhereFilter.model_validate({"id": {"eq": ids[0]}}),
    )
    assert updated == 1
    rows = await select_tmpl("public.upd_null", ["id", "name", "qty"])
    assert rows[0]["name"] is None
    assert rows[0]["qty"] == 5


async def test_select_does_not_fetch_ignored_columns(db):
    from tai42_mcp_dynamic_postgres.database.connection import cursor
    from tai42_mcp_dynamic_postgres.gen.templates.insert import insert_tmpl
    from tai42_mcp_dynamic_postgres.gen.templates.select import select_tmpl

    async with cursor() as cur:
        await cur.execute("DROP TABLE IF EXISTS public.secrets")
        await cur.execute("CREATE TABLE public.secrets (id serial PRIMARY KEY, name text, secret text)")
        await cur.connection.commit()

    await insert_tmpl("public.secrets", ["name", "secret"], [("a", "hidden")], pk_columns=["id"])

    # "secret" stays filterable via the allowlist, but is excluded from the SQL
    # projection, so the returned rows never carry it.
    rows = await select_tmpl(
        "public.secrets",
        ["id", "name", "secret"],
        where=WhereFilter.model_validate({"secret": {"eq": "hidden"}}),
        select_columns=["id", "name"],
    )
    assert len(rows) == 1
    assert set(rows[0].keys()) == {"id", "name"}


async def test_native_types_roundtrip(db):
    from tai42_mcp_dynamic_postgres.database.connection import cursor
    from tai42_mcp_dynamic_postgres.gen.templates.insert import insert_tmpl
    from tai42_mcp_dynamic_postgres.gen.templates.select import select_tmpl

    async with cursor() as cur:
        await cur.execute("DROP TABLE IF EXISTS public.natives")
        await cur.execute(
            "CREATE TABLE public.natives ("
            "  id serial PRIMARY KEY, uid uuid, ts timestamptz, d date,"
            "  amount numeric(10,2), meta jsonb, tags text[])"
        )
        await cur.connection.commit()

    class Native(BaseModel):
        id: int
        uid: uuid.UUID | None = None
        ts: datetime.datetime | None = None
        d: datetime.date | None = None
        amount: Decimal | None = None
        meta: object | None = None
        tags: list[str] | None = None

    the_uid = uuid.uuid4()
    await insert_tmpl(
        "public.natives",
        ["uid", "ts", "d", "amount", "meta", "tags"],
        [
            (
                the_uid,
                datetime.datetime(2024, 1, 2, 3, 4, 5, tzinfo=datetime.timezone.utc),
                datetime.date(2024, 1, 2),
                Decimal("12.34"),
                {"k": 1},
                ["x", "y"],
            )
        ],
        json_columns=["meta"],
        pk_columns=["id"],
    )
    cols = ["id", "uid", "ts", "d", "amount", "meta", "tags"]
    rows = await select_tmpl("public.natives", cols, model=Native)
    assert len(rows) == 1
    row = rows[0]
    # Native objects flow through: pydantic accepts them without str coercion.
    assert row.uid == the_uid
    assert isinstance(row.ts, datetime.datetime)
    assert row.d == datetime.date(2024, 1, 2)
    assert row.amount == Decimal("12.34")
    assert row.meta == {"k": 1}
    assert row.tags == ["x", "y"]


async def test_insert_on_conflict_do_nothing(db):
    from tai42_mcp_dynamic_postgres.database.connection import cursor
    from tai42_mcp_dynamic_postgres.gen.templates.insert import insert_tmpl

    async with cursor() as cur:
        await cur.execute("DROP TABLE IF EXISTS public.uniq")
        await cur.execute("CREATE TABLE public.uniq (id serial PRIMARY KEY, code text UNIQUE)")
        await cur.connection.commit()

    await insert_tmpl("public.uniq", ["code"], [("x",)], pk_columns=["id"])
    # Conflicting row is skipped (no id returned) instead of raising.
    ids = await insert_tmpl("public.uniq", ["code"], [("x",)], raise_on_conflict=False, pk_columns=["id"])
    assert ids == []


async def test_insert_composite_pk_returns_tuples(db):
    from tai42_mcp_dynamic_postgres.database.connection import cursor
    from tai42_mcp_dynamic_postgres.gen.templates.insert import insert_tmpl

    async with cursor() as cur:
        await cur.execute("DROP TABLE IF EXISTS public.pair")
        await cur.execute("CREATE TABLE public.pair (a integer, b integer, note text, PRIMARY KEY (a, b))")
        await cur.connection.commit()

    result = await insert_tmpl("public.pair", ["a", "b", "note"], [(1, 2, "x")], pk_columns=["a", "b"])
    assert result == [[1, 2]]


async def test_introspect_schema_structured(db):
    from tai42_mcp_dynamic_postgres.database.connection import cursor
    from tai42_mcp_dynamic_postgres.gen.schema.introspect import introspect_schema

    async with cursor() as cur:
        await cur.execute("DROP TABLE IF EXISTS public.child")
        await cur.execute("DROP TABLE IF EXISTS public.parent")
        await cur.execute("CREATE TABLE public.parent (a integer, b integer, PRIMARY KEY (a, b))")
        await cur.execute(
            "CREATE TABLE public.child ("
            "  a integer, b integer, tags text[], embedding vector(3),"
            "  FOREIGN KEY (a, b) REFERENCES public.parent (a, b))"
        )
        await cur.connection.commit()

    tables, fks = await introspect_schema()

    child = tables["public.child"]
    by_name = {c.name: c for c in child.columns}
    assert by_name["tags"].python_type == "Optional[List[str]]"
    # vector(3) -> the type modifier is stripped, mapped to List[float].
    assert by_name["embedding"].python_type == "Optional[List[float]]"
    assert child.primary_key == []

    parent = tables["public.parent"]
    assert parent.primary_key == ["a", "b"]

    # Composite FK reconstructed with ordered, aligned columns.
    assert ("public.child", ["a", "b"], "public.parent", ["a", "b"]) in fks


async def test_enum_and_view_generation(db):
    from tai42_mcp_dynamic_postgres.database.connection import cursor
    from tai42_mcp_dynamic_postgres.gen.schema.introspect import introspect_schema

    async with cursor() as cur:
        await cur.execute("DROP VIEW IF EXISTS public.mood_view")
        await cur.execute("DROP TABLE IF EXISTS public.moods")
        await cur.execute("DROP TYPE IF EXISTS mood")
        await cur.execute("CREATE TYPE mood AS ENUM ('sad', 'ok', 'happy')")
        await cur.execute("CREATE TABLE public.moods (id serial PRIMARY KEY, feeling mood)")
        await cur.execute("CREATE VIEW public.mood_view AS SELECT id, feeling FROM public.moods")
        await cur.connection.commit()

    tables, _ = await introspect_schema()

    moods = tables["public.moods"]
    by_name = {c.name: c for c in moods.columns}
    # Enum column maps to str; generation does not abort.
    assert by_name["feeling"].python_type == "Optional[str]"

    view = tables["public.mood_view"]
    assert view.kind == "v"
    assert view.writable is False


async def test_select_joined_runtime(db):
    from tai42_mcp_dynamic_postgres.database.connection import cursor
    from tai42_mcp_dynamic_postgres.gen.templates.select_joined import select_joined_tmpl

    async with cursor() as cur:
        await cur.execute("DROP TABLE IF EXISTS public.books")
        await cur.execute("DROP TABLE IF EXISTS public.authors")
        await cur.execute("CREATE TABLE public.authors (id serial PRIMARY KEY, name text)")
        await cur.execute(
            "CREATE TABLE public.books (id serial PRIMARY KEY, title text,"
            "  author_id integer REFERENCES public.authors (id))"
        )
        await cur.execute("INSERT INTO public.authors (id, name) VALUES (1, 'Ann')")
        await cur.execute("INSERT INTO public.books (title, author_id) VALUES ('B1', 1)")
        await cur.connection.commit()

    select_items = [
        (["public", "authors", "name"], "public_authors_name"),
        (["public", "books", "title"], "public_books_title"),
    ]
    from_parts = ["public", "authors"]
    joins = [(["public", "books"], [(["public", "authors", "id"], ["public", "books", "author_id"])])]
    column_map = {"public_authors_name": "public.authors.name", "public_books_title": "public.books.title"}

    rows = await select_joined_tmpl(
        select_items,
        from_parts,
        joins,
        column_map,
        where=WhereFilter.model_validate({"public_authors_name": {"eq": "Ann"}}),
    )
    assert rows == [{"public_authors_name": "Ann", "public_books_title": "B1"}]


async def test_knn_filter_runtime(db):
    from tai42_mcp_dynamic_postgres.database.connection import cursor
    from tai42_mcp_dynamic_postgres.gen.templates.select import select_tmpl

    async with cursor() as cur:
        await cur.execute("DROP TABLE IF EXISTS public.vecs")
        await cur.execute("CREATE TABLE public.vecs (id serial PRIMARY KEY, embedding vector(3))")
        await cur.execute("INSERT INTO public.vecs (embedding) VALUES ('[1,0,0]'), ('[0,1,0]')")
        await cur.connection.commit()

    where = WhereFilter.model_validate(
        {"embedding": {"knn": {"query": [1.0, 0.0, 0.0], "distance": "l2", "threshold": 0.5}}}
    )
    rows = await select_tmpl("public.vecs", ["id", "embedding"], where=where)
    assert [r["id"] for r in rows] == [1]


async def test_server_starts_and_serves_generated_tools(db):
    # End-to-end FastMCP v3 startup: generate tools, run the app in-memory
    # (exercising the lifespan / pool open+close), then list and call a tool.
    from fastmcp import Client

    from tai42_mcp_dynamic_postgres.core.app import mcp_app
    from tai42_mcp_dynamic_postgres.gen.loader import load_dynamic_tools

    await load_dynamic_tools(overwrite=True, readonly=False)

    async with Client(mcp_app) as client:
        tools = await client.list_tools()
        names = {t.name for t in tools}
        assert any(n.startswith("select_public_items") for n in names)
        assert any(n.startswith("insert_public_items") for n in names)

        result = await client.call_tool("select_public_items", {})
        assert result is not None
