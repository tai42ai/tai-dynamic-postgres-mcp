import pytest

from tai_dynamic_postgres_mcp.gen.schema.introspect import _require_safe_identifier


@pytest.mark.parametrize(
    "name",
    # Soft keywords (match, case, type) are contextual, remain valid plain
    # identifiers, and are common column names, so they must pass on every
    # supported Python version.
    ["id", "user_name", "Col123", "embedding", "match", "case", "type"],
)
def test_valid_identifiers_pass(name):
    _require_safe_identifier("column", name)  # must not raise


@pytest.mark.parametrize(
    "name",
    # Leading-underscore names (_, _private, _x) are rejected: emitted as bare
    # class attributes they become Pydantic private attributes, not fields, so
    # the column would silently vanish.
    ["first name", "user-name", "1col", 'a"b', "a;b", "DROP TABLE x", "", "café", "_", "_private", "_x"],
)
def test_invalid_identifiers_raise(name):
    with pytest.raises(ValueError, match="Unsupported column name"):
        _require_safe_identifier("column", name)


@pytest.mark.parametrize("name", ["class", "from", "import", "return", "global", "as", "def"])
def test_python_keywords_rejected(name):
    # Hard keywords cannot be used as identifiers, so they would produce
    # uncompilable generated code and are rejected loudly.
    with pytest.raises(ValueError, match="Unsupported column name"):
        _require_safe_identifier("column", name)
