"""MCP Server 的协议级冒烟测试（需要安装官方 mcp SDK，否则跳过）。

运行：python -m unittest discover -s tests -v
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MCP_SERVER = ROOT / "mcp_server.py"
HAS_MCP = importlib.util.find_spec("mcp") is not None


@unittest.skipUnless(HAS_MCP, "官方 mcp SDK 未安装（pip install -r requirements-mcp.txt）")
class MCPServerTests(unittest.TestCase):
    def test_list_tools_and_status(self):
        from mcp.client import Client
        from mcp.client.stdio import StdioServerParameters

        async def run() -> None:
            params = StdioServerParameters(
                command=sys.executable,
                args=[str(MCP_SERVER)],
                cwd=str(ROOT),
            )
            async with Client(params, read_timeout_seconds=30) as client:
                tools = await client.list_tools()
                names = {tool.name for tool in tools.tools}
                self.assertTrue(
                    {"status", "import_bvid", "capture", "export_score"}.issubset(names),
                    names,
                )
                result = await client.call_tool("status", {})
                self.assertFalse(result.is_error)
                if result.structured_content is not None:
                    payload = result.structured_content.get(
                        "result", result.structured_content
                    )
                else:
                    payload = json.loads(result.content[0].text)
                self.assertTrue(payload.get("ok"))

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
