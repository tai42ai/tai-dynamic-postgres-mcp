import pytest

from tai42_dynamic_postgres_mcp.gen.filters.builder import resolver_from_columns
from tai42_dynamic_postgres_mcp.gen.order.builder import build_order_by_clause
from tai42_dynamic_postgres_mcp.gen.order.models import OrderByItem


@pytest.fixture
def resolver():
    return resolver_from_columns(["id", "name", "embedding"])


def test_empty_order_returns_none(resolver):
    sql, params = build_order_by_clause(None, resolver)
    assert sql is None
    assert params == []


def test_single_and_multi_order(resolver):
    sql, params = build_order_by_clause([OrderByItem(field="name", direction="DESC")], resolver)
    assert sql.as_string(None) == 'ORDER BY "name" DESC'
    assert params == []

    sql, _ = build_order_by_clause(
        [OrderByItem(field="name", direction="ASC"), OrderByItem(field="id", direction="DESC")],
        resolver,
    )
    assert sql.as_string(None) == 'ORDER BY "name" ASC, "id" DESC'


def test_knn_order(resolver):
    item = OrderByItem(field="embedding", knn={"query": [1.0, 2.0], "distance": "l2", "direction": "DESC"})
    sql, params = build_order_by_clause([item], resolver)
    assert sql.as_string(None) == 'ORDER BY "embedding" <-> (%s)::vector DESC'
    assert params == ["[1.0,2.0]"]


def test_unknown_order_field_raises(resolver):
    with pytest.raises(ValueError, match="Unknown order_by field"):
        build_order_by_clause([OrderByItem(field="id; DROP TABLE x", direction="ASC")], resolver)


def test_nulls_first_and_last(resolver):
    sql, _ = build_order_by_clause([OrderByItem(field="name", direction="ASC", nulls="FIRST")], resolver)
    assert sql.as_string(None) == 'ORDER BY "name" ASC NULLS FIRST'

    sql, _ = build_order_by_clause([OrderByItem(field="id", direction="DESC", nulls="LAST")], resolver)
    assert sql.as_string(None) == 'ORDER BY "id" DESC NULLS LAST'


def test_nulls_unset_uses_postgres_default(resolver):
    # Without nulls set, no NULLS clause is emitted (Postgres default applies).
    sql, _ = build_order_by_clause([OrderByItem(field="name", direction="ASC")], resolver)
    assert "NULLS" not in sql.as_string(None)


def test_nulls_on_knn_order(resolver):
    item = OrderByItem(
        field="embedding",
        nulls="LAST",
        knn={"query": [1.0, 2.0], "distance": "l2", "direction": "DESC"},
    )
    sql, params = build_order_by_clause([item], resolver)
    assert sql.as_string(None) == 'ORDER BY "embedding" <-> (%s)::vector DESC NULLS LAST'
    assert params == ["[1.0,2.0]"]
