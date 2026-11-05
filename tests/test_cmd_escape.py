"""Windows `.cmd` shim 的参数转义：不能让 prompt/参数变成第二条命令。"""
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agentrpa_bridge.mcp_client import argv_for  # noqa: E402

CMD_BODY = "@echo off\r\necho ARG=[%~1]\r\n"


@unittest.skipUnless(os.name == "nt", "仅 Windows 的 cmd /c 语义")
class CmdEscapeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        cls.cmd = os.path.join(cls.dir, "echoarg.cmd")
        with open(cls.cmd, "w", encoding="ascii", newline="") as fh:
            fh.write(CMD_BODY)

    def run_arg(self, arg):
        proc = subprocess.run(argv_for(self.cmd, [arg]), capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=20)
        return proc.stdout.strip()

    def test_ampersand_does_not_start_new_command(self):
        out = self.run_arg("a & echo INJECTED")
        self.assertIn("a & echo INJECTED", out)
        self.assertNotIn("INJECTED\n", out.replace("a & echo INJECTED", ""))

    def test_pipe_and_redirect_are_literal(self):
        for payload in ("x | whoami", "x > out.txt", "x < in.txt"):
            out = self.run_arg(payload)
            self.assertIn(payload, out, f"{payload!r} 被 cmd 解释了：{out}")

    def test_caret_is_literal(self):
        self.assertIn("a ^ b", self.run_arg("a ^ b"))

    def test_quoted_path_survives(self):
        self.assertIn(r"C:\Program Files\x y", self.run_arg(r"C:\Program Files\x y"))

    def test_empty_arg_does_not_break_invocation(self):
        # 空串经 subprocess 列表 + cmd /c 会被再包一层引号，值不是空但调用不崩、不注入
        out = self.run_arg("")
        self.assertIn("ARG=[", out)


class ArgvShapeTest(unittest.TestCase):
    def test_non_shim_untouched(self):
        argv = argv_for(sys.executable, ["-V"])
        self.assertEqual(argv[0], sys.executable)
        self.assertEqual(argv[1], "-V")

    @unittest.skipUnless(os.name == "nt", "仅 Windows")
    def test_shim_wrapped_with_cmd(self):
        argv = argv_for("nope.cmd", ["x & y"])
        self.assertEqual(argv[:2], ["cmd", "/c"])
        self.assertEqual(argv[2], "nope.cmd")
        self.assertNotIn("x & y", argv)          # 原始串不应原样出现


if __name__ == "__main__":
    unittest.main()
