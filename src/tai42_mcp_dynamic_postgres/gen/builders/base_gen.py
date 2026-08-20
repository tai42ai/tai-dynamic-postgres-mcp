from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from tai42_mcp_dynamic_postgres.config.settings import pg_settings
from tai42_mcp_dynamic_postgres.gen.schema.introspect import ColumnInfo, ForeignKey, TableInfo

OUTPUT_DIR = Path(pg_settings.tools_dir)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

TOOLS_SUFFIX = "tools"

# (model_code, tool_code) for one generated tool; model_code may be empty.
Chunk = Tuple[str, str]


class BaseGen(ABC):
    def __init__(self, prefix: str, imports: str, template: str, ignore_columns: Optional[List[str]] = None) -> None:
        self.prefix = prefix
        self.imports = imports
        self.template = template
        self.ignore_columns = ignore_columns or []

    @abstractmethod
    def tool_chunks(self, tables: Dict[str, TableInfo], fks: List[ForeignKey]) -> Iterable[Chunk]:
        """Yield a ``(model_code, tool_code)`` pair per generated tool."""

    @property
    def output_path(self) -> Path:
        return OUTPUT_DIR / f"{self.prefix}_{TOOLS_SUFFIX}.py"

    @property
    def module_name(self) -> str:
        return f"{self.prefix}_{TOOLS_SUFFIX}"

    @property
    def is_exists(self) -> bool:
        return self.output_path.exists()

    def func_name(self, table: str) -> str:
        return f"{self.prefix}_{table.replace('.', '_')}"

    def generate_tools(self, tables: Dict[str, TableInfo], fks: List[ForeignKey]) -> List[str]:
        chunks = [self.imports]
        for model_code, tool_code in self.tool_chunks(tables, fks):
            chunks.append(model_code)
            chunks.append(tool_code)
        return chunks

    def generate_file(self, tables: Dict[str, TableInfo], fks: List[ForeignKey]) -> None:
        # Generate fully before truncating the target, so a generation failure
        # leaves any existing file intact rather than half-written.
        code = "".join(chunk for chunk in self.generate_tools(tables, fks) if chunk)
        self.output_path.write_text(code)


class TableGen(BaseGen):
    """Base for generators that emit one tool per table."""

    # Write generators (insert/update/delete) override this: views and
    # materialized views are read-only and get no write tools.
    writable_only: bool = False

    def tool_chunks(self, tables: Dict[str, TableInfo], fks: List[ForeignKey]) -> Iterable[Chunk]:
        for table_info in tables.values():
            if self.writable_only and not table_info.writable:
                continue
            chunk = self.generate_tool(table_info)
            if chunk is not None:
                yield chunk

    @abstractmethod
    def generate_tool(self, table_info: TableInfo) -> Optional[Chunk]:
        """Build the (model_code, tool_code) for a single table, or None to skip it."""

    @staticmethod
    def col_names(table_info: TableInfo) -> List[str]:
        return [col.name for col in table_info.columns]

    def included(self, table_info: TableInfo) -> List[ColumnInfo]:
        """Columns kept after applying ``ignore_columns``."""
        return [col for col in table_info.columns if col.name not in self.ignore_columns]
