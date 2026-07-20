import pytest

from tai42_dynamic_postgres_mcp.gen.schema.codegen import (
    is_json_type,
    sql_columns_to_pydantic_model,
    sql_type_to_python_type,
)


@pytest.mark.parametrize(
    ("sql_type", "nullable", "expected"),
    [
        ("integer", False, "int"),
        ("bigint", False, "int"),
        ("double precision", False, "float"),
        ("boolean", False, "bool"),
        ("text", True, "Optional[str]"),
        # Native mappings (psycopg returns native objects, not strings).
        ("uuid", False, "uuid.UUID"),
        ("uuid", True, "Optional[uuid.UUID]"),
        ("date", False, "datetime.date"),
        ("timestamp", False, "datetime.datetime"),
        ("timestamp without time zone", False, "datetime.datetime"),
        ("timestamp with time zone", False, "datetime.datetime"),
        ("timestamptz", False, "datetime.datetime"),
        ("time", False, "datetime.time"),
        ("time with time zone", False, "datetime.time"),
        # numeric/decimal -> Decimal
        ("numeric", False, "Decimal"),
        ("numeric(10,2)", False, "Decimal"),
        ("decimal", True, "Optional[Decimal]"),
        # json/jsonb -> Any (scalar json such as 42/"x"/true must validate)
        ("json", False, "Any"),
        ("jsonb", False, "Any"),
        ("jsonb", True, "Optional[Any]"),
        # Vector and arrays
        ("vector", False, "List[float]"),
        ("vector(768)", False, "List[float]"),
        ("integer[]", False, "List[int]"),
        ("text[]", True, "Optional[List[str]]"),
        ("numeric(10,2)[]", False, "List[Decimal]"),
    ],
)
def test_sql_type_to_python_type(sql_type, nullable, expected):
    assert sql_type_to_python_type(sql_type, nullable) == expected


def test_enum_column_maps_to_str():
    # A user-defined enum type maps to str rather than aborting generation.
    assert sql_type_to_python_type("mood", False, enum_types={"mood"}) == "str"
    assert sql_type_to_python_type("mood", True, enum_types={"mood"}) == "Optional[str]"
    # Schema-qualified enum name is matched by its bare name too.
    assert sql_type_to_python_type("app.mood", False, enum_types={"mood"}) == "str"
    # Enum arrays.
    assert sql_type_to_python_type("mood[]", False, enum_types={"mood"}) == "List[str]"


def test_unknown_type_raises():
    # Unknown (non-enum) types raise rather than silently widening to Any.
    with pytest.raises(ValueError, match="Unsupported PostgreSQL type"):
        sql_type_to_python_type("some_custom_enum", False)
    with pytest.raises(ValueError, match="Unsupported PostgreSQL type"):
        sql_type_to_python_type("money[]", False)


@pytest.mark.parametrize(
    ("sql_type", "expected"),
    [
        ("json", True),
        ("jsonb", True),
        ("integer", False),
        ("text[]", False),
        ("jsonb[]", False),  # a json array is an array (native list), not Json-wrapped
        ("vector", False),
    ],
)
def test_is_json_type(sql_type, expected):
    assert is_json_type(sql_type) is expected


def test_pydantic_model_generation():
    name, code = sql_columns_to_pydantic_model("insert", "public.users", [("id", "int"), ("name", "Optional[str]")])
    assert name == "InsertPublicUsersRow"
    compile(code, "<model>", "exec")
    assert "id: int" in code
    assert "name: Optional[str] = None" in code


def test_json_scalar_value_validates_against_any_model():
    # A jsonb column maps to Any, so a scalar json value (42 / "x" / true) as well
    # as a dict/list must validate through the generated model.
    from typing import Any

    from pydantic import BaseModel

    _, code = sql_columns_to_pydantic_model("select", "public.doc", [("meta", "Any")])
    ns: dict = {"BaseModel": BaseModel, "Any": Any}
    exec(compile(code, "<model>", "exec"), ns)
    model = ns["SelectPublicDocRow"]
    for value in (42, "x", True, {"k": 1}, [1, 2, 3]):
        assert model(meta=value).meta == value


def test_empty_model_uses_pass():
    # A model with no columns must still be valid Python (no empty class body).
    _, code = sql_columns_to_pydantic_model("insert", "public.empty", [])
    compile(code, "<model>", "exec")
    assert "pass" in code
