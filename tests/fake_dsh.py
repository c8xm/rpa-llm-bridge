"""一次打印 stdin 的内容，模拟 `dsh headless -` 的行为。"""
import sys

task = sys.stdin.read().strip()
print("DSH:" + task)
