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

    def test_apply_stitch_hides_duplicates_and_crops(self):
        from mcp_server import _apply_stitch_to_images

        images = [
            {"file": "a", "crop": {"l": 0, "r": 1, "t": 0, "b": 1}},
            {"file": "b", "crop": {"l": 0, "r": 1, "t": 0, "b": 1}},
            {"file": "c", "crop": {"l": 0, "r": 1, "t": 0, "b": 1}},
        ]
        details = {
            "seams": [[0.6, 0.4], [1.0, 0.0]],
            "valid": [True, True],
            "duplicate": [False, True],
        }
        hidden = _apply_stitch_to_images(images, details, hide_duplicates=True)
        self.assertEqual(hidden, 1)
        self.assertAlmostEqual(images[0]["crop"]["r"], 0.6)
        self.assertAlmostEqual(images[1]["crop"]["l"], 0.4)
        self.assertTrue(images[2]["hidden"])
        self.assertTrue(images[2]["autoHidden"])

    def test_apply_stitch_hides_fully_covered_image(self):
        from mcp_server import _apply_stitch_to_images

        images = [
            {"file": "a", "crop": {"l": 0, "r": 1, "t": 0, "b": 1}},
            {"file": "b", "crop": {"l": 0, "r": 1, "t": 0, "b": 1}},
            {"file": "c", "crop": {"l": 0, "r": 1, "t": 0, "b": 1}},
        ]
        details = {
            "seams": [[0.5, 0.5], [0.5, 0.5]],
            "valid": [True, True],
            "duplicate": [False, False],
        }
        hidden = _apply_stitch_to_images(images, details, hide_duplicates=True)
        self.assertEqual(hidden, 1)
        self.assertTrue(images[1]["hidden"])
        self.assertTrue(images[1]["autoHidden"])


if __name__ == "__main__":
    unittest.main()
