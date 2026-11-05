import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agentrpa_bridge.mcp_client import McpError, McpStdioClient, McpTimeout, unwrap_agent_text

HERE = os.path.dirname(os.path.abspath(__file__))
FAKE = os.path.join(HERE, "fake_mcp_server.py")


def client(timeout=15.0):
    return McpStdioClient(sys.executable, [FAKE], timeout=timeout).start()


class McpClientTest(unittest.TestCase):
    def setUp(self):
        self.c = client()

    def tearDown(self):
        self.c.close()

    def test_handshake(self):
        self.assertEqual(self.c.server_info["server"]["name"], "fake-mcp")
        self.assertEqual(self.c.server_info["protocol"], "2025-06-18")

    def test_banner_on_stdout_is_tolerated(self):
        self.assertTrue(any("fake-mcp" in line for line in self.c.banners))

    def test_list_tools(self):
        names = [t["name"] for t in self.c.list_tools()]
        self.assertIn("echo", names)
        self.assertIn("agent", names)

    def test_call_tool_unicode(self):
        out = self.c.call_tool("echo", {"prompt": "中文与 emoji 🚀"})
        self.assertFalse(out["is_error"])
        self.assertEqual(out["text"], "中文与 emoji 🚀")

    def test_is_error_flag(self):
        out = self.c.call_tool("boom", {})
        self.assertTrue(out["is_error"])
        self.assertEqual(out["text"], "policy denied")

    def test_unknown_tool_raises(self):
        with self.assertRaises(McpError):
            self.c.call_tool("nope", {})

    def test_ask_unwraps_agent_envelope(self):
        out = self.c.ask("agent", "1+1")
        self.assertEqual(out["text"], "答案是：1+1")

    def test_timeout(self):
        slow = client(timeout=1.0)
        try:
            with self.assertRaises(McpTimeout):
                slow.call_tool("sleep", {"seconds": 3}, timeout=1.0)
        finally:
            slow.close()

    def test_dead_process_reports_stderr(self):
        dead = McpStdioClient(sys.executable, ["-c", "import sys;sys.exit(3)"], timeout=5)
        with self.assertRaises(McpError) as ctx:
            dead.start()
        self.assertIn("3", str(ctx.exception))
        dead.close()


class UnwrapTest(unittest.TestCase):
    def test_plain_text_passthrough(self):
        self.assertEqual(unwrap_agent_text("普通答案"), "普通答案")

    def test_string_content(self):
        self.assertEqual(unwrap_agent_text('{"threadId":"t1","content":"答案"}'), "答案")

    def test_list_content(self):
        self.assertEqual(
            unwrap_agent_text('{"content":[{"type":"text","text":"a"},{"type":"text","text":"b"}]}'),
            "a\nb")

    def test_broken_json_passthrough(self):
        self.assertEqual(unwrap_agent_text('{"content":'), '{"content":')


if __name__ == "__main__":
    unittest.main()
