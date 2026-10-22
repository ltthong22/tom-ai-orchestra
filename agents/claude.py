"""Connector cho Claude (Anthropic).
Dùng cho việc khó: code, suy luận phức tạp, quyết định.
Key từ env ANTHROPIC_API_KEY. Nếu không có key, agent báo unavailable
(orchestrator sẽ fallback sang agent khác hoặc báo user).

Muốn thêm Groq / DeepSeek / OpenRouter sau này: copy file này,
đổi tên class, endpoint và env var tương ứng.
"""
import json
import os
import urllib.request
from typing import Optional

from .base import BaseAgent, AgentResult

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
DEFAULT_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-20250514")


class ClaudeAgent(BaseAgent):
    name = "claude"
    capabilities = [
        "code",           # viết/sửa code
        "reasoning",      # suy luận phức tạp
        "decide",         # quyết định
        "summarize",
        "extract",
        "verify",
        "plan",           # lập kế hoạch, chia subtask
    ]
    parallel_safe = True

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.model = model or DEFAULT_MODEL

    def run(self, prompt: str, system: Optional[str] = None, timeout: int = 180) -> AgentResult:
        if not self.api_key:
            return AgentResult(self.name, False, error="Thiếu ANTHROPIC_API_KEY")
        body = {
            "model": self.model,
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            body["system"] = system
        req = urllib.request.Request(
            API_URL, data=json.dumps(body).encode(),
            headers={
                "Content-Type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": API_VERSION,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                j = json.load(r)
            text = "".join(b.get("text", "") for b in j.get("content", []) if b.get("type") == "text")
            usage = j.get("usage", {})
            return AgentResult(self.name, True, output=text, meta={
                "model": self.model,
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
            })
        except Exception as e:
            return AgentResult(self.name, False, error=f"Claude lỗi: {e}")
