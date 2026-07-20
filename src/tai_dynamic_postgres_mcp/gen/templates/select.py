from typing import Any, List, Optional, Sequence, Type

from psycopg import sql

from tai_dynamic_postgres_mcp.gen.filters.builder import resolver_from_columns
from tai_dynamic_postgres_mcp.gen.filters.models import WhereFilter
from tai_dynamic_postgres_mcp.gen.order.models import OrderByItem
from tai_dynamic_postgres_mcp.gen.templates.common import run_select


async def select_tmpl(
    table: str,
    columns: List[str],
    where: Optional[WhereFilter] = None,
    order_by: Optional[List[OrderByItem]] = None,
    limit: Optional[int] = None,
    offset: Optional[int] = None,
    model: Optional[Type[Any]] = None,
    select_columns: Optional[Sequence[str]] = None,
) -> List[Any]:
    """Select rows, projecting exactly the exposed columns.

    ``select_columns`` is the explicit projection: the tool lists these columns
    in the SQL so columns it hides (e.g. ``ignore_select_columns``) are never
    fetched from the database. ``columns`` stays the full allowlist that
    ``where``/``order_by`` may reference, so a hidden column can still be
    filtered on without being returned. When ``select_columns`` is omitted the
    whole allowlist is projected. An empty projection raises rather than
    emitting a ``SELECT`` with no columns.
    """
    projection = list(select_columns) if select_columns is not None else columns
    if not projection:
        raise ValueError(f"Refusing to build a SELECT on {table!r} with no columns to project.")
    projection_sql = sql.SQL(", ").join(sql.Identifier(col) for col in projection)
    query = sql.SQL("SELECT {} FROM {}").format(projection_sql, sql.Identifier(*table.split(".")))
    return await run_select(query, resolver_from_columns(columns), where, order_by, limit, offset, model)
