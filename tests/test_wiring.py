"""Unit tests for the DB/server glue: introspection assembly, the connection
pool singleton, type-loader registration, the tool loader, the CLI runner, and
the app lifespan. The database and FastMCP server are stubbed throughout.
"""

import warnings

import pytest

# --- introspection assembly -------------------------------------------------


class _FakeIntrospectCursor:
    def __init__(self, results):
        self._results = list(results)
        self._i = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, query, params=None):
        return None

    async def fetchall(self):
        result = self._results[self._i]
        self._i += 1
        return result


def _patch_introspect_cursor(monkeypatch, results):
    import tai_dynamic_postgres_mcp.gen.schema.introspect as introspect_mod

    def fake_cursor(*args, **kwargs):
        return _FakeIntrospectCursor(results)

    monkeypatch.setattr(introspect_mod, "cursor", fake_cursor)


async def test_introspect_assembles_tables_pk_enum_kinds(monkeypatch):
    from tai_dynamic_postgres_mcp.gen.schema.introspect import introspect_schema

    enum_rows = [("mood",)]
    column_rows = [
        ("public", "t", "r", "id", "integer", True, True),
        ("public", "t", "r", "name", "text", False, False),
        ("public", "t", "r", "feeling", "mood", False, False),
        ("public", "t", "r", "meta", "jsonb", False, False),
        ("public", "t", "r", "tags", "text[]", False, False),
        ("public", "t", "r", "created", "timestamptz", False, True),
        ("public", "v_t", "v", "id", "integer", False, False),
    ]
    pk_rows = [("public", "t", "id")]
    fk_rows = []
    _patch_introspect_cursor(monkeypatch, [enum_rows, column_rows, pk_rows, fk_rows])

    tables, fks = await introspect_schema()

    t = tables["public.t"]
    assert t.kind == "r"
    assert t.primary_key == ["id"]
    by_name = {c.name: c for c in t.columns}
    assert by_name["id"].has_default is True
    assert by_name["id"].nullable is False
    assert by_name["feeling"].python_type == "Optional[str]"  # enum
    assert by_name["meta"].is_json is True
    assert by_name["meta"].python_type == "Optional[Any]"
    assert by_name["tags"].python_type == "Optional[List[str]]"
    assert by_name["created"].has_default is True

    assert tables["public.v_t"].kind == "v"
    assert tables["public.v_t"].writable is False
    assert fks == []


async def test_introspect_assembles_composite_fk(monkeypatch):
    from tai_dynamic_postgres_mcp.gen.schema.introspect import introspect_schema

    column_rows = [
        ("public", "t", "r", "a", "integer", True, False),
        ("public", "t", "r", "b", "integer", True, False),
    ]
    fk_rows = [
        ("fk1", "public", "t", "a", "public", "p", "x"),
        ("fk1", "public", "t", "b", "public", "p", "y"),
    ]
    _patch_introspect_cursor(monkeypatch, [[], column_rows, [], fk_rows])

    _, fks = await introspect_schema()
    assert ("public.t", ["a", "b"], "public.p", ["x", "y"]) in fks


async def test_introspect_separates_same_named_fks_on_different_tables(monkeypatch):
    # A constraint name is unique only per table in PostgreSQL, so two different
    # tables can each own a FK named ``parent_fk``. They must reconstruct as two
    # independent single-column keys, not merge into one corrupted composite key.
    from tai_dynamic_postgres_mcp.gen.builders.select_joined_gen import SelectJoinedGen
    from tai_dynamic_postgres_mcp.gen.schema.introspect import introspect_schema

    column_rows = [
        ("public", "parents", "r", "id", "integer", True, True),
        ("public", "a", "r", "id", "integer", True, True),
        ("public", "a", "r", "parent_id", "integer", False, False),
        ("public", "b", "r", "id", "integer", True, True),
        ("public", "b", "r", "parent_id", "integer", False, False),
    ]
    pk_rows = [("public", "parents", "id"), ("public", "a", "id"), ("public", "b", "id")]
    # Both constraints share the name ``parent_fk`` but belong to different tables.
    fk_rows = [
        ("parent_fk", "public", "a", "parent_id", "public", "parents", "id"),
        ("parent_fk", "public", "b", "parent_id", "public", "parents", "id"),
    ]
    _patch_introspect_cursor(monkeypatch, [[], column_rows, pk_rows, fk_rows])

    _, fks = await introspect_schema()

    # Each table keeps its own single-column FK; neither is dropped or corrupted.
    assert ("public.a", ["parent_id"], "public.parents", ["id"]) in fks
    assert ("public.b", ["parent_id"], "public.parents", ["id"]) in fks
    assert len(fks) == 2

    # A join built from each FK references only that table's own columns.
    gen = SelectJoinedGen()
    cond_a = gen.find_join_condition("public.a", "public.parents", fks)
    assert cond_a == [(["public", "a", "parent_id"], ["public", "parents", "id"])]
    cond_b = gen.find_join_condition("public.b", "public.parents", fks)
    assert cond_b == [(["public", "b", "parent_id"], ["public", "parents", "id"])]


async def test_introspect_rejects_unsafe_identifier(monkeypatch):
    from tai_dynamic_postgres_mcp.gen.schema.introspect import introspect_schema

    column_rows = [("public", "t", "r", "bad name", "integer", False, False)]
    _patch_introspect_cursor(monkeypatch, [[], column_rows, [], []])
    with pytest.raises(ValueError, match="Unsupported column name"):
        await introspect_schema()


# --- connection pool singleton ----------------------------------------------


class _FakeCur:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self):
        self.closed = False
        self.autocommit_calls = []

    async def set_autocommit(self, value):
        self.autocommit_calls.append(value)

    def cursor(self, *args, **kwargs):
        return _FakeCur()


class _FakePool:
    def __init__(self, conninfo, **kwargs):
        self.conninfo = conninfo
        self.kwargs = kwargs
        self.opened = False
        self.closed = False
        self.conn = _FakeConn()
        self.returned = None

    async def open(self):
        self.opened = True

    async def close(self):
        self.closed = True

    async def getconn(self):
        return self.conn

    async def putconn(self, conn):
        self.returned = conn


@pytest.fixture
def fake_pool(monkeypatch):
    import tai_dynamic_postgres_mcp.database.connection as conn_mod

    monkeypatch.setattr(conn_mod, "AsyncConnectionPool", _FakePool)
    monkeypatch.setattr(conn_mod, "_pool", None)
    yield conn_mod
    conn_mod._pool = None


def test_build_conninfo_includes_statement_timeout():
    from tai_dynamic_postgres_mcp.database.connection import _build_conninfo

    info = _build_conninfo()
    assert "dbname=" in info
    assert "statement_timeout=30000" in info
    # The secret password value is included in the conninfo but never logged.
    assert "test_password" in info


async def test_pool_is_a_singleton_and_opened(fake_pool):
    p1 = await fake_pool.get_connection_pool()
    p2 = await fake_pool.get_connection_pool()
    assert p1 is p2
    assert p1.opened is True


async def test_close_only_closes_a_created_pool(fake_pool):
    # No pool built yet: closing is a no-op (does not build one just to close).
    await fake_pool.close_connection_pool()
    assert fake_pool._pool is None

    pool = await fake_pool.get_connection_pool()
    await fake_pool.close_connection_pool()
    assert pool.closed is True
    assert fake_pool._pool is None


async def test_get_async_connection_returns_conn_to_pool(fake_pool):
    async with fake_pool.get_async_connection() as conn:
        assert conn is not None
    pool = await fake_pool.get_connection_pool()
    assert pool.returned is pool.conn


async def test_read_path_runs_autocommit_and_resets_before_return(fake_pool):
    # The read path runs in autocommit so a SELECT leaves the connection IDLE
    # (no pool reset WARNING / rollback round-trip), then resets to the pool
    # default before returning so a later write checkout stays transactional.
    async with fake_pool.get_async_connection() as conn:
        assert conn.autocommit_calls == [True]
    assert conn.autocommit_calls == [True, False]


class _ResetBoomConn(_FakeConn):
    # A connection that died mid-statement: resetting autocommit before return
    # raises (psycopg's real _check_intrans_gen requires an IDLE connection).
    async def set_autocommit(self, value):
        self.autocommit_calls.append(value)
        if value is False:
            raise RuntimeError("reset failed: connection not IDLE")


class _ResetBoomPool(_FakePool):
    def __init__(self, conninfo, **kwargs):
        super().__init__(conninfo, **kwargs)
        self.conn = _ResetBoomConn()


async def test_putconn_runs_even_when_autocommit_reset_fails(fake_pool, caplog):
    # A failed autocommit reset on return must NOT skip putconn: the pool slot
    # has to go back (a skipped putconn permanently leaks the slot). The pool's
    # own reset then discards+replaces the broken connection, so returning it
    # unconditionally lets the pool self-heal.
    import logging

    fake_pool.AsyncConnectionPool = _ResetBoomPool
    with caplog.at_level(logging.ERROR):
        async with fake_pool.get_async_connection() as conn:
            assert conn is not None
    pool = await fake_pool.get_connection_pool()
    assert pool.returned is pool.conn
    # The reset failure is surfaced loudly (logged with traceback), not swallowed.
    reset_errors = [r for r in caplog.records if r.levelno == logging.ERROR and "reset autocommit" in r.message.lower()]
    assert reset_errors
    assert reset_errors[0].exc_info is not None


async def test_original_read_error_not_masked_by_failed_reset(fake_pool):
    # When an original read error is in flight AND the autocommit reset also
    # fails, the original read error must propagate (the reset error must not
    # mask it), and the connection is still returned to the pool.
    fake_pool.AsyncConnectionPool = _ResetBoomPool
    with pytest.raises(ValueError, match="original read boom"):
        async with fake_pool.get_async_connection():
            raise ValueError("original read boom")
    pool = await fake_pool.get_connection_pool()
    assert pool.returned is pool.conn


async def test_get_async_connection_logs_traceback_on_error(fake_pool, caplog):
    import logging

    class _BoomPool(_FakePool):
        async def getconn(self):
            raise RuntimeError("getconn failed")

    fake_pool.AsyncConnectionPool = _BoomPool
    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError, match="getconn failed"):
        async with fake_pool.get_async_connection():
            pass
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    # logging.exception attaches the traceback; logging.error(e) would not.
    assert errors
    assert errors[0].exc_info is not None


async def test_pool_open_failure_closes_partial_pool(fake_pool):
    created = []

    class _FailOpenPool(_FakePool):
        def __init__(self, conninfo, **kwargs):
            super().__init__(conninfo, **kwargs)
            created.append(self)

        async def open(self):
            raise RuntimeError("open failed")

    fake_pool.AsyncConnectionPool = _FailOpenPool
    with pytest.raises(RuntimeError, match="open failed"):
        await fake_pool.get_connection_pool()
    # The partially-constructed pool is closed (workers not orphaned) and no
    # half-open pool is cached.
    assert created
    assert created[0].closed is True
    assert fake_pool._pool is None


async def test_cursor_yields_cursor(fake_pool):
    async with fake_pool.cursor() as cur:
        assert cur is not None


# --- type-loader registration -----------------------------------------------


def test_vector_loader_parses_literal():
    from tai_dynamic_postgres_mcp.database.helpers import VectorLoader

    loader = VectorLoader.__new__(VectorLoader)
    assert loader.load(b"[1.0,2.5,3.0]") == [1.0, 2.5, 3.0]
    with pytest.raises(ValueError, match="Invalid vector format"):
        loader.load(b"1,2,3")


class _FakeAdapters:
    def __init__(self):
        self.dumpers = []
        self.loaders = []

    def register_dumper(self, cls, dumper):
        self.dumpers.append((cls, dumper))

    def register_loader(self, oid, loader):
        self.loaders.append((oid, loader))


class _FakeAdaptConn:
    def __init__(self):
        self.adapters = _FakeAdapters()


def test_register_json_dumpers_registers_dict():
    from tai_dynamic_postgres_mcp.database.helpers import register_json_dumpers

    conn = _FakeAdaptConn()
    register_json_dumpers(conn)
    assert any(cls is dict for cls, _ in conn.adapters.dumpers)


async def test_register_vector_warns_when_missing(monkeypatch):
    import tai_dynamic_postgres_mcp.database.helpers as helpers_mod

    async def fake_fetch(conn, name):
        return None

    monkeypatch.setattr(helpers_mod.TypeInfo, "fetch", staticmethod(fake_fetch))
    with pytest.warns(RuntimeWarning, match="pgvector"):
        await helpers_mod.register_vector_as_list(_FakeAdaptConn())


async def test_register_types_loaders_runs(monkeypatch):
    import tai_dynamic_postgres_mcp.database.helpers as helpers_mod

    async def fake_fetch(conn, name):
        return None

    monkeypatch.setattr(helpers_mod.TypeInfo, "fetch", staticmethod(fake_fetch))
    conn = _FakeAdaptConn()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        await helpers_mod.register_types_loaders(conn)
    assert any(cls is dict for cls, _ in conn.adapters.dumpers)


# --- tool loader ------------------------------------------------------------


@pytest.fixture
def loader_env(monkeypatch, tmp_path):
    import tai_dynamic_postgres_mcp.gen.builders.base_gen as base_gen
    import tai_dynamic_postgres_mcp.gen.loader as loader_mod

    monkeypatch.setattr(base_gen, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(loader_mod, "OUTPUT_DIR", tmp_path)

    imported: list = []
    monkeypatch.setattr(loader_mod.importlib, "import_module", lambda name: imported.append(name))

    from schema_helpers import col, schema, table

    users = table("public.users", [col("id", "int", has_default=True), col("name", "Optional[str]")], pk=["id"])
    tables = schema(users)

    async def fake_introspect():
        return tables, []

    monkeypatch.setattr(loader_mod, "introspect_schema", fake_introspect)
    return loader_mod, tmp_path, imported


async def test_loader_generates_and_imports_all_tools(loader_env):
    loader_mod, tmp_path, imported = loader_env
    await loader_mod.load_dynamic_tools(overwrite=True, readonly=False)
    names = {p.name for p in tmp_path.glob("*_tools.py")}
    assert names == {
        "select_joined_tools.py",
        "select_tools.py",
        "insert_tools.py",
        "update_tools.py",
        "delete_tools.py",
    }
    assert any(n.endswith("insert_tools") for n in imported)


async def test_generated_insert_tool_threads_model_fields_set(monkeypatch, tmp_path):
    # Exercise a real generated insert tool end to end: omitting a field vs.
    # sending it as null must reach insert_tmpl as distinct provided-field sets,
    # so an omitted column takes its DB default and an explicit null writes NULL.
    import sys

    from fastmcp import FastMCP

    import tai_dynamic_postgres_mcp.core.app as app_mod
    import tai_dynamic_postgres_mcp.gen.builders.base_gen as base_gen
    import tai_dynamic_postgres_mcp.gen.loader as loader_mod
    from schema_helpers import col, schema, table
    from tai_dynamic_postgres_mcp import tools

    # A fresh app so decorating the generated tool never pollutes the singleton.
    monkeypatch.setattr(app_mod, "mcp_app", FastMCP())
    monkeypatch.setattr(base_gen, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(loader_mod, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(loader_mod, "_registered_tools", {})

    users = table("public.users", [col("id", "int", has_default=True), col("name", "Optional[str]")], pk=["id"])

    async def fake_introspect():
        return schema(users), []

    monkeypatch.setattr(loader_mod, "introspect_schema", fake_introspect)

    generated_modules = [
        f"tai_dynamic_postgres_mcp.tools.{name}"
        for name in ("select_joined_tools", "select_tools", "insert_tools", "update_tools", "delete_tools")
    ]
    try:
        await loader_mod.load_dynamic_tools(overwrite=True, readonly=False)
        module = sys.modules["tai_dynamic_postgres_mcp.tools.insert_tools"]

        captured: dict = {}

        async def fake_insert_tmpl(table_name, columns, values, raise_on_conflict=True, **kwargs):
            captured["values"] = values
            captured["provided_fields"] = kwargs["provided_fields"]
            return [1, 2]

        monkeypatch.setattr(module, "insert_tmpl", fake_insert_tmpl)

        row_model = module.InsertPublicUsersRow
        # First row omits id (falls back to the DB default); second sends id=None
        # explicitly (must be written as NULL).
        result = await module.insert_public_users([row_model(name="a"), row_model(id=None, name="b")])

        assert result == [1, 2]
        assert captured["values"] == [(None, "a"), (None, "b")]
        assert captured["provided_fields"] == [{"name"}, {"id", "name"}]
    finally:
        for mod in generated_modules:
            sys.modules.pop(mod, None)
        if str(tmp_path) in tools.__path__:
            tools.__path__.remove(str(tmp_path))


async def test_loader_readonly_prunes_write_tools(loader_env):
    loader_mod, tmp_path, imported = loader_env
    # First a read/write run leaves write tool files on disk.
    await loader_mod.load_dynamic_tools(overwrite=True, readonly=False)
    assert (tmp_path / "insert_tools.py").exists()

    # A later readonly run must remove the stale write tool files and never
    # import them.
    imported.clear()
    await loader_mod.load_dynamic_tools(overwrite=True, readonly=True)
    assert not (tmp_path / "insert_tools.py").exists()
    assert not (tmp_path / "update_tools.py").exists()
    assert not (tmp_path / "delete_tools.py").exists()
    assert (tmp_path / "select_tools.py").exists()
    assert not any(n.endswith(("insert_tools", "update_tools", "delete_tools")) for n in imported)


async def test_loader_select_joined_name_collision_raises(monkeypatch, tmp_path):
    # Two distinct --select-joined groups whose derived names flatten to the same
    # tool/model name must raise, not let FastMCP silently overwrite one join tool.
    import tai_dynamic_postgres_mcp.gen.builders.base_gen as base_gen
    import tai_dynamic_postgres_mcp.gen.loader as loader_mod
    from schema_helpers import col, schema, table

    monkeypatch.setattr(base_gen, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(loader_mod, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(loader_mod, "_registered_tools", {})

    # "a" + "b_c" and "a_b" + "c" both flatten to select_joined_public_a_b_c.
    tables = schema(
        table("public.a", [col("id", "int", has_default=True)], pk=["id"]),
        table("public.b_c", [col("id", "int", has_default=True)], pk=["id"]),
        table("public.a_b", [col("id", "int", has_default=True)], pk=["id"]),
        table("public.c", [col("id", "int", has_default=True)], pk=["id"]),
    )

    async def fake_introspect():
        return tables, []

    monkeypatch.setattr(loader_mod, "introspect_schema", fake_introspect)

    with pytest.raises(ValueError, match="Select-joined name collision"):
        await loader_mod.load_dynamic_tools(
            overwrite=True,
            readonly=True,
            select_joined=[["public.a", "public.b_c"], ["public.a_b", "public.c"]],
        )


async def test_loader_select_joined_duplicate_group_raises(monkeypatch, tmp_path):
    # An exact duplicate group also collides on the derived name and must raise.
    import tai_dynamic_postgres_mcp.gen.builders.base_gen as base_gen
    import tai_dynamic_postgres_mcp.gen.loader as loader_mod
    from schema_helpers import col, schema, table

    monkeypatch.setattr(base_gen, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(loader_mod, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(loader_mod, "_registered_tools", {})

    tables = schema(
        table("public.users", [col("id", "int", has_default=True), col("org_id", "Optional[int]")], pk=["id"]),
        table("public.orgs", [col("id", "int", has_default=True)], pk=["id"]),
    )

    async def fake_introspect():
        return tables, [("public.users", ["org_id"], "public.orgs", ["id"])]

    monkeypatch.setattr(loader_mod, "introspect_schema", fake_introspect)

    with pytest.raises(ValueError, match="Select-joined name collision"):
        await loader_mod.load_dynamic_tools(
            overwrite=True,
            readonly=True,
            select_joined=[["public.users", "public.orgs"], ["public.users", "public.orgs"]],
        )


async def test_loader_select_joined_distinct_groups_generate(monkeypatch, tmp_path):
    # Non-colliding groups still generate a join tool file without error.
    import tai_dynamic_postgres_mcp.gen.builders.base_gen as base_gen
    import tai_dynamic_postgres_mcp.gen.loader as loader_mod
    from schema_helpers import col, schema, table

    monkeypatch.setattr(base_gen, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(loader_mod, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(loader_mod, "_registered_tools", {})
    monkeypatch.setattr(loader_mod.importlib, "import_module", lambda name: None)

    tables = schema(
        table("public.users", [col("id", "int", has_default=True), col("org_id", "Optional[int]")], pk=["id"]),
        table("public.orgs", [col("id", "int", has_default=True)], pk=["id"]),
        table("public.teams", [col("id", "int", has_default=True), col("org_id", "Optional[int]")], pk=["id"]),
    )
    fks = [
        ("public.users", ["org_id"], "public.orgs", ["id"]),
        ("public.teams", ["org_id"], "public.orgs", ["id"]),
    ]

    async def fake_introspect():
        return tables, fks

    monkeypatch.setattr(loader_mod, "introspect_schema", fake_introspect)

    await loader_mod.load_dynamic_tools(
        overwrite=True,
        readonly=True,
        select_joined=[["public.users", "public.orgs"], ["public.teams", "public.orgs"]],
    )
    code = (tmp_path / "select_joined_tools.py").read_text()
    assert "async def select_joined_public_users_orgs" in code
    assert "async def select_joined_public_teams_orgs" in code


async def test_loader_deregisters_write_tools_on_readonly_reload(monkeypatch, tmp_path):
    # A second load in the same process (e.g. an embedding host reloading in
    # --readonly) must remove the previously-registered write tools from the app,
    # not merely delete their files on disk.
    import sys

    from fastmcp import FastMCP

    import tai_dynamic_postgres_mcp.core.app as app_mod
    import tai_dynamic_postgres_mcp.gen.builders.base_gen as base_gen
    import tai_dynamic_postgres_mcp.gen.loader as loader_mod
    from schema_helpers import col, schema, table
    from tai_dynamic_postgres_mcp import tools

    monkeypatch.setattr(base_gen, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(loader_mod, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(loader_mod, "_registered_tools", {})

    # A fresh app so the real singleton (and other tests) is never polluted; the
    # generated modules bind to it via ``from core.app import mcp_app``.
    monkeypatch.setattr(app_mod, "mcp_app", FastMCP())

    users = table("public.users", [col("id", "int", has_default=True), col("name", "Optional[str]")], pk=["id"])

    async def fake_introspect():
        return schema(users), []

    monkeypatch.setattr(loader_mod, "introspect_schema", fake_introspect)

    generated_modules = [
        f"tai_dynamic_postgres_mcp.tools.{name}"
        for name in ("select_joined_tools", "select_tools", "insert_tools", "update_tools", "delete_tools")
    ]

    async def live_names():
        return {t.name for t in await app_mod.mcp_app.local_provider.list_tools()}

    try:
        await loader_mod.load_dynamic_tools(overwrite=True, readonly=False)
        names = await live_names()
        assert "insert_public_users" in names
        assert "update_public_users" in names
        assert "delete_public_users" in names
        assert "select_public_users" in names

        await loader_mod.load_dynamic_tools(overwrite=True, readonly=True)
        names = await live_names()
        assert not any(n.startswith(("insert_", "update_", "delete_")) for n in names)
        assert "select_public_users" in names
    finally:
        for mod in generated_modules:
            sys.modules.pop(mod, None)
        if str(tmp_path) in tools.__path__:
            tools.__path__.remove(str(tmp_path))


async def test_loader_tracks_partial_tools_when_reload_raises(monkeypatch, tmp_path):
    # If a generated module registers some @mcp_app.tool decorators and then
    # raises mid-module, those tools are live but the import failed loudly.
    # _registered_tools must still record them so a later run can deregister them
    # (otherwise a switch to --readonly would leak an untracked write tool).
    import sys

    from fastmcp import FastMCP

    import tai_dynamic_postgres_mcp.core.app as app_mod
    import tai_dynamic_postgres_mcp.gen.builders.base_gen as base_gen
    import tai_dynamic_postgres_mcp.gen.builders.select_gen as select_gen_mod
    import tai_dynamic_postgres_mcp.gen.loader as loader_mod
    from schema_helpers import col, schema, table
    from tai_dynamic_postgres_mcp import tools

    monkeypatch.setattr(base_gen, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(loader_mod, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(loader_mod, "_registered_tools", {})
    monkeypatch.setattr(app_mod, "mcp_app", FastMCP())

    users = table("public.users", [col("id", "int", has_default=True), col("name", "Optional[str]")], pk=["id"])

    async def fake_introspect():
        return schema(users), []

    monkeypatch.setattr(loader_mod, "introspect_schema", fake_introspect)

    # Make the select_tools module register one tool and then raise mid-import.
    def boom_generate_file(self, tables, fks):
        self.output_path.write_text(
            "from tai_dynamic_postgres_mcp.core.app import mcp_app\n"
            "\n"
            "@mcp_app.tool\n"
            "def partial_tool_select():\n"
            "    return None\n"
            "\n"
            "raise RuntimeError('boom mid select_tools import')\n"
        )

    monkeypatch.setattr(select_gen_mod.SelectGen, "generate_file", boom_generate_file)

    generated_modules = [f"tai_dynamic_postgres_mcp.tools.{name}" for name in ("select_joined_tools", "select_tools")]

    # Isolate the import search path and module cache so the real import reads
    # this test's boom file from tmp_path (other tests leave stale tools.__path__
    # entries whose valid select_tools.py would otherwise be found first).
    original_path = list(tools.__path__)
    tools.__path__[:] = []
    for mod in generated_modules:
        sys.modules.pop(mod, None)

    try:
        with pytest.raises(RuntimeError, match="boom mid select_tools import"):
            await loader_mod.load_dynamic_tools(overwrite=True, readonly=True)

        # The partially-registered tool is live AND tracked despite the raise.
        live = {t.name for t in await app_mod.mcp_app.local_provider.list_tools()}
        assert "partial_tool_select" in live
        assert loader_mod._registered_tools["select_tools"] == {"partial_tool_select"}

        # Tracked means removable: a later run can deregister the partial tool.
        loader_mod._deregister_module_tools("select_tools")
        live_after = {t.name for t in await app_mod.mcp_app.local_provider.list_tools()}
        assert "partial_tool_select" not in live_after
    finally:
        for mod in generated_modules:
            sys.modules.pop(mod, None)
        tools.__path__[:] = original_path


async def test_loader_raises_on_name_collision(loader_env, monkeypatch):
    loader_mod, _tmp_path, _imported = loader_env
    from schema_helpers import col, schema, table

    colliding = schema(
        table("a_b.c", [col("id", "int")]),
        table("a.b_c", [col("id", "int")]),
    )

    async def fake_introspect():
        return colliding, []

    monkeypatch.setattr(loader_mod, "introspect_schema", fake_introspect)
    with pytest.raises(ValueError, match="collision"):
        await loader_mod.load_dynamic_tools(overwrite=True, readonly=True)


# --- CLI runner + app lifespan ----------------------------------------------


@pytest.fixture
def runner_env(monkeypatch):
    import tai_dynamic_postgres_mcp.cli.main as main_mod

    calls = {"run_async": None, "closed": 0}

    async def fake_load(**kwargs):
        return None

    class _FakeAcm:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *exc):
            return False

    def fake_get_async_connection():
        return _FakeAcm()

    async def fake_run_async(transport=None, **kwargs):
        calls["run_async"] = {"transport": transport, **kwargs}

    async def fake_close():
        calls["closed"] += 1

    monkeypatch.setattr(main_mod, "load_dynamic_tools", fake_load)
    monkeypatch.setattr(main_mod, "get_async_connection", fake_get_async_connection)
    monkeypatch.setattr(main_mod.mcp_app, "run_async", fake_run_async)
    monkeypatch.setattr(main_mod, "close_connection_pool", fake_close)
    return main_mod, calls


async def test_runner_stdio_starts_server_and_closes_pool(runner_env):
    main_mod, calls = runner_env
    result = await main_mod.runner(
        overwrite=True,
        readonly=False,
        allow_unfiltered=False,
        select_joined=(),
        ignore_insert_column=("id",),
        ignore_select_column=(),
        ignore_update_column=("id",),
        ignore_select_joined_column=(),
        transport="stdio",
        host="127.0.0.1",
        port=8000,
    )
    assert result is None
    assert calls["run_async"]["transport"] == "stdio"
    assert calls["closed"] == 1


async def test_runner_stdio_rejects_host_port(runner_env):
    main_mod, _ = runner_env
    with pytest.raises(Exception, match="stdio"):
        await main_mod.runner(
            overwrite=True,
            readonly=False,
            allow_unfiltered=False,
            select_joined=(),
            ignore_insert_column=(),
            ignore_select_column=(),
            ignore_update_column=(),
            ignore_select_joined_column=(),
            transport="stdio",
            host="0.0.0.0",
            port=9000,
        )


async def test_runner_http_passes_host_port(runner_env):
    main_mod, calls = runner_env
    await main_mod.runner(
        overwrite=True,
        readonly=True,
        allow_unfiltered=False,
        select_joined=(),
        ignore_insert_column=(),
        ignore_select_column=(),
        ignore_update_column=(),
        ignore_select_joined_column=(),
        transport="http",
        host="127.0.0.1",
        port=9001,
    )
    assert calls["run_async"]["transport"] == "http"
    assert calls["run_async"]["host"] == "127.0.0.1"
    assert calls["run_async"]["port"] == 9001


async def test_app_lifespan_opens_and_closes_pool(monkeypatch):
    import tai_dynamic_postgres_mcp.core.app as app_mod

    events = []

    async def fake_get():
        events.append("open")

    async def fake_close():
        events.append("close")

    monkeypatch.setattr(app_mod, "get_connection_pool", fake_get)
    monkeypatch.setattr(app_mod, "close_connection_pool", fake_close)

    async with app_mod.lifespan(app_mod.mcp_app):
        assert events == ["open"]
    assert events == ["open", "close"]
