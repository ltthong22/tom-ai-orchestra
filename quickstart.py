#!/usr/bin/env python3
"""Chạy thử nhanh: kiểm tra agent nào sống + demo workflow parallel mini."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Load .env nếu có
env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(env_path):
    for line in open(env_path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

from agents import get_agent, list_agents
from orchestrator import Orchestrator
from workflows import parallel

print("== Kiểm tra agents ==")
for name in list_agents():
    try:
        ok = get_agent(name).check()
    except Exception as e:
        ok = False
    print(f"  {'🟢' if ok else '🔴'} {name}")

print("\n== Demo parallel (2 subtask qua agent đang sống) ==")
orch = Orchestrator()
res = parallel.run(orch, "demo", [
    {"kind": "quick_qa", "prompt": "Thủ đô của Việt Nam là gì? Trả lời 1 câu."},
    {"kind": "summarize", "prompt": "Tóm tắt 1 câu: AI là gì?"},
])
print("OK" if res["ok"] else "LỖI")
print(res["final"][:500])
