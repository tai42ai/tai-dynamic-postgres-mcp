from typing import Optional

from tai42_dynamic_postgres_mcp.gen.builders.base_gen import Chunk, TableGen
from tai42_dynamic_postgres_mcp.gen.schema.introspect import TableInfo

_FUNC_PREFIX = "delete"

_IMPORTS = """# This file is auto-generated. Do not edit manually.

from typing import Optional
from tai42_dynamic_postgres_mcp.core.app import mcp_app
from tai42_dynamic_postgres_mcp.gen.templates.delete import delete_tmpl
from tai42_dynamic_postgres_mcp.gen.filters.models import WhereFilter

"""

_TOOL_TEMPLATE = '''
@mcp_app.tool(tags={{"postgres"}})
async def {func_name}(where: Optional[WhereFilter] = None) -> int:
    """
    Deletes rows from the `{table}` table.

    Parameters:
        where: Filters selecting the rows to delete, using `WhereFilter`.
               A WHERE filter is required unless the server was started with
               --allow-unfiltered; deleting with no filter otherwise raises.

    Returns:
        Number of rows deleted from the `{table}` table.
    """

    return await delete_tmpl("{table}", {col_list}, where, allow_unfiltered={allow_unfiltered})
'''


class DeleteGen(TableGen):
    writable_only = True

    def __init__(self, allow_unfiltered: bool = False) -> None:
        super().__init__(_FUNC_PREFIX, _IMPORTS, _TOOL_TEMPLATE)
        self.allow_unfiltered = allow_unfiltered

    def generate_tool(self, table_info: TableInfo) -> Optional[Chunk]:
        tool_code = self.template.format(
            func_name=self.func_name(table_info.qualified),
            table=table_info.qualified,
            # DELETE has no body columns, but WHERE may filter on any real column.
            col_list=repr(self.col_names(table_info)),
            allow_unfiltered=repr(self.allow_unfiltered),
        )
        return "", tool_code  # No model needed for delete
