"""
MCP Client for Inventory Tools
================================
Async client to connect to the inventory MCP server.

The InventoryMCPClientSync wrapper has been removed — it was broken
because it tried to share an async context manager across separate
asyncio.run() calls (each creates a different event loop).

Use InventoryMCPClient directly inside async code, or use the
_run_async_safely() helper in mcp_tools.py for sync callers.
"""

import sys
from pathlib import Path
from typing import Optional, Any, Dict
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from contextlib import asynccontextmanager


class InventoryMCPClient:
    """
    Async client for the inventory MCP server.

    Usage (inside async code):
        client = InventoryMCPClient(server_script_path)
        async with client.connect():
            result = await client.call_tool("get_stock_status", {"sku": "SKU-001"})

    For synchronous callers (e.g. LangChain @tool functions), use the
    _run_async_safely() helper in mcp_tools.py which handles the
    event-loop-already-running problem transparently.
    """

    def __init__(self, server_script_path: str):
        self.server_script_path = server_script_path
        self.session: Optional[ClientSession] = None
        self.available_tools: Dict[str, Any] = {}

    @asynccontextmanager
    async def connect(self):
        """
        Connect to the MCP server subprocess, yield self, then close.

        Adds the backend root to PYTHONPATH so the server subprocess
        can import project modules without manual sys.path manipulation.
        """
        import os

        env = os.environ.copy()
        backend_root = str(
            Path(self.server_script_path).resolve().parent.parent.parent
        )
        env["PYTHONPATH"] = (
            f"{backend_root}{os.pathsep}{env['PYTHONPATH']}"
            if "PYTHONPATH" in env
            else backend_root
        )
        # Force UTF-8 on the server subprocess stdout/stderr.
        # Without this, Windows uses the system code page (e.g. cp1252)
        # and the MCP client fails with UnicodeDecodeError when reading responses.
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"

        server_params = StdioServerParameters(
            command="python",
            args=[self.server_script_path],
            env=env,
        )

        try:
            async with stdio_client(server_params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    self.session = session

                    tools_list = await session.list_tools()
                    self.available_tools = {t.name: t for t in tools_list.tools}

                    yield self
        except Exception as exc:
            import traceback

            print(
                f"[MCP Client] Connection error: {exc}\n{traceback.format_exc()}",
                file=sys.stderr,
            )
            raise
        finally:
            self.session = None
            self.available_tools = {}

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        """
        Call a tool on the MCP server.

        Must be called while inside the `async with client.connect()` block.
        """
        if not self.session:
            raise RuntimeError(
                "Not connected. Use `async with client.connect():` first."
            )
        if tool_name not in self.available_tools:
            available = list(self.available_tools.keys())
            raise ValueError(
                f"Tool '{tool_name}' not available. Available: {available}"
            )

        result = await self.session.call_tool(tool_name, arguments)
        if result.content:
            return result.content[0].text
        return ""

    def get_available_tools(self) -> list:
        return list(self.available_tools.keys())

    def get_tool_schema(self, tool_name: str) -> Optional[Dict[str, Any]]:
        tool = self.available_tools.get(tool_name)
        if tool:
            return {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.inputSchema,
            }
        return None