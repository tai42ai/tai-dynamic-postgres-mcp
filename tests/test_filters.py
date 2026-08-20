import pytest
from pydantic import ValidationError

from tai42_mcp_dynamic_postgres.gen.filters.builder import (
    build_where_clause,
    resolver_from_column_map,
    resolver_from_columns,
)
from tai42_mcp_dynamic_postgres.gen.filters.models import LogicalFilter, WhereFilter


def render(where_filter, resolver):
    clause, params = build_where_clause(where_filter, resolver)
    return (clause.as_string(None) if clause is not None else None), params


@pytest.fixture
def resolver():
    return resolver_from_columns(["id", "name", "embedding"])


def test_no_where_returns_none(resolver):
    # op is None (no `where` supplied) -> no predicate, no raise.
    sql, params = build_where_clause(None, resolver)
    assert sql is None
    assert params == []


def test_supplied_empty_filter_raises(resolver):
    # A `where` that was supplied but resolves to no predicate must raise rather
    # than silently dropping the WHERE clause and matching every row.
    with pytest.raises(ValueError, match="empty"):
        build_where_clause(WhereFilter.model_validate({}), resolver)
    with pytest.raises(ValueError, match="empty"):
        build_where_clause(WhereFilter.model_validate({"AND": []}), resolver)


def test_eq_is_parameterized_and_identifier_quoted(resolver):
    sql, params = render(WhereFilter.model_validate({"name": {"eq": "bob"}}), resolver)
    assert sql == '"name" = %s'
    assert params == ["bob"]


@pytest.mark.parametrize(
    ("op", "symbol", "value"),
    [
        ("ne", "!=", 5),
        ("gt", ">", 5),
        ("gte", ">=", 5),
        ("lt", "<", 5),
        ("lte", "<=", 5),
        ("like", "LIKE", "%x%"),
        ("not_like", "NOT LIKE", "%x%"),
        ("ilike", "ILIKE", "%x%"),
        ("not_ilike", "NOT ILIKE", "%x%"),
    ],
)
def test_comparison_operators(resolver, op, symbol, value):
    sql, params = render(WhereFilter.model_validate({"id": {op: value}}), resolver)
    assert sql == f'"id" {symbol} %s'
    assert params == [value]


def test_in_and_not_in(resolver):
    sql, params = render(WhereFilter.model_validate({"id": {"in": [1, 2, 3]}}), resolver)
    assert sql == '"id" IN (%s, %s, %s)'
    assert params == [1, 2, 3]

    sql, params = render(WhereFilter.model_validate({"id": {"not_in": [4, 5]}}), resolver)
    assert sql == '"id" NOT IN (%s, %s)'
    assert params == [4, 5]


def test_between(resolver):
    sql, params = render(WhereFilter.model_validate({"id": {"between": [1, 10]}}), resolver)
    assert sql == '"id" BETWEEN %s AND %s'
    assert params == [1, 10]


def test_is_null_true_and_false(resolver):
    sql, _ = render(WhereFilter.model_validate({"name": {"is_null": True}}), resolver)
    assert sql == '"name" IS NULL'
    sql, _ = render(WhereFilter.model_validate({"name": {"is_null": False}}), resolver)
    assert sql == '"name" IS NOT NULL'


def test_and_or_not_nesting(resolver):
    wf = WhereFilter.model_validate({"AND": [{"name": {"eq": "a"}}, {"OR": [{"id": {"gt": 1}}, {"id": {"lt": 0}}]}]})
    sql, params = render(wf, resolver)
    assert sql == '("name" = %s AND ("id" > %s OR "id" < %s))'
    assert params == ["a", 1, 0]

    sql, params = render(WhereFilter.model_validate({"NOT": {"id": {"eq": 7}}}), resolver)
    assert sql == '(NOT ("id" = %s))'
    assert params == [7]


def test_knn_filter(resolver):
    wf = WhereFilter.model_validate(
        {"embedding": {"knn": {"query": [1.0, 2.0], "distance": "cosine", "threshold": 0.5}}}
    )
    sql, params = render(wf, resolver)
    assert sql == '"embedding" <=> (%s)::vector < %s'
    assert params == ["[1.0,2.0]", 0.5]


def test_unknown_field_raises(resolver):
    # The core injection guard: a field name not in the allowlist must raise,
    # never reach the query as raw text.
    with pytest.raises(ValueError, match="Unknown filter field"):
        build_where_clause(WhereFilter.model_validate({"id = 1 OR 1=1 --": {"eq": 1}}), resolver)


def test_injection_payload_as_field_name_raises(resolver):
    with pytest.raises(ValueError, match="Unknown filter field"):
        build_where_clause(WhereFilter.model_validate({"name); DROP TABLE users; --": {"eq": "x"}}), resolver)


def test_unknown_operator_raises():
    # An unknown/typo'd operator must raise, not silently produce an empty filter.
    with pytest.raises(ValidationError):
        WhereFilter.model_validate({"name": {"eqq": "x"}})


def test_empty_in_list_raises():
    with pytest.raises(ValidationError):
        WhereFilter.model_validate({"id": {"in": []}})
    with pytest.raises(ValidationError):
        WhereFilter.model_validate({"id": {"not_in": []}})


def test_between_wrong_arity_raises():
    with pytest.raises(ValidationError):
        WhereFilter.model_validate({"id": {"between": [1]}})
    with pytest.raises(ValidationError):
        WhereFilter.model_validate({"id": {"between": [1, 2, 3]}})


def test_mixed_logical_and_field_rejected():
    # A field filter alongside a logical key must raise rather than silently
    # dropping the field filter.
    with pytest.raises(ValueError, match="Cannot mix logical keys"):
        WhereFilter.model_validate({"name": {"eq": "x"}, "AND": [{"id": {"gt": 1}}]})


def test_pure_logical_filter_still_valid():
    # Multiple logical keys together are fine.
    wf = WhereFilter.model_validate({"AND": [{"id": {"gt": 1}}], "OR": [{"id": {"lt": 0}}]})
    assert isinstance(wf.root, LogicalFilter)


def test_reserved_keys_rejected_by_model():
    # Using a reserved logical key as a direct field name (value shaped as a
    # FilterOp, not a list) must be rejected rather than silently misread.
    with pytest.raises(ValueError, match="reserved keys"):
        WhereFilter.model_validate({"AND": {"eq": 1}})


def test_join_resolver_quotes_qualified_names():
    resolver = resolver_from_column_map({"users_name": "public.users.name"})
    sql, params = render(WhereFilter.model_validate({"users_name": {"eq": "z"}}), resolver)
    assert sql == '"public"."users"."name" = %s'
    assert params == ["z"]
