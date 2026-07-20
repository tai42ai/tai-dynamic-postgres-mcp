from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastmcp import FastMCP

from tai_dynamic_postgres_mcp.database.connection import close_connection_pool, get_connection_pool


@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncGenerator[None, None]:
    """Open the connection pool on startup and close it on shutdown.

    Binding teardown to the app lifespan means the pool is closed whenever the
    server stops, regardless of which entry point (CLI, embedding host, tests)
    started it.
    """
    await get_connection_pool()
    try:
        yield
    finally:
        await close_connection_pool()


mcp_app: FastMCP = FastMCP(lifespan=lifespan)
