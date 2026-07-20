from typing import AbstractSet, Any, List, Optional, Sequence, Tuple, Union

from psycopg import sql
from psycopg.types.json import Json

from tai42_dynamic_postgres_mcp.database.connection import get_connection_pool


async def insert_tmpl(
    table: str,
    columns: List[str],
    values: List[Tuple[Any, ...]],
    raise_on_conflict: bool = True,
    default_columns: Optional[Sequence[str]] = None,
    json_columns: Optional[Sequence[str]] = None,
    pk_columns: Optional[Sequence[str]] = None,
    provided_fields: Optional[Sequence[AbstractSet[str]]] = None,
) -> Union[List[Any], int]:
    """Insert rows, returning the primary key of each inserted row.

    ``provided_fields`` gives, per row, the set of column names the caller
    actually supplied (Pydantic's ``model_fields_set``). It separates a field
    the caller omitted from one the caller explicitly sent as ``null``:

    - A column the caller omitted (not in the row's provided set) that has a
      database default (``default_columns``, incl. serial/identity) is emitted
      as ``DEFAULT`` so the database default applies.
    - A column the caller supplied always binds its value, so an explicit
      ``None`` writes SQL ``NULL`` even when the column has a database default.

    When ``provided_fields`` is ``None`` every listed column is treated as
    supplied, so no ``DEFAULT`` is emitted. ``json_columns`` values are wrapped
    in ``Json(...)``; array and every other value is bound natively. With
    ``pk_columns`` the query uses ``RETURNING`` those columns (a scalar list for
    a single-column key, a list of tuples for a composite key); without a
    primary key it returns the affected row count.
    """
    if not values:
        return [] if pk_columns else 0

    default_set = set(default_columns or [])
    json_set = set(json_columns or [])

    params: List[Any] = []
    row_sqls: List[sql.Composable] = []
    for i, row in enumerate(values):
        provided = provided_fields[i] if provided_fields is not None else None
        cells: List[sql.Composable] = []
        for col, val in zip(columns, row, strict=True):
            # DEFAULT only when the caller omitted the column and it has a
            # database default; a supplied value (including None -> NULL) binds.
            omitted = provided is not None and col not in provided
            if omitted and col in default_set:
                cells.append(sql.SQL("DEFAULT"))
            else:
                cells.append(sql.Placeholder())
                params.append(Json(val) if col in json_set else val)
        row_sqls.append(sql.SQL("({})").format(sql.SQL(", ").join(cells)))

    conflict_clause = sql.SQL("") if raise_on_conflict else sql.SQL("ON CONFLICT DO NOTHING")

    query = sql.SQL("INSERT INTO {table} ({columns}) VALUES {values} {conflict}").format(
        table=sql.Identifier(*table.split(".")),
        columns=sql.SQL(", ").join(sql.Identifier(col) for col in columns),
        values=sql.SQL(", ").join(row_sqls),
        conflict=conflict_clause,
    )
    if pk_columns:
        query = query + sql.SQL(" RETURNING ") + sql.SQL(", ").join(sql.Identifier(col) for col in pk_columns)

    # pool.connection() commits on success and rolls back on exception.
    pool = await get_connection_pool()
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(query, params)
        if not pk_columns:
            return cur.rowcount
        rows = await cur.fetchall()

    if len(pk_columns) == 1:
        return [row[0] for row in rows]
    return [list(row) for row in rows]
