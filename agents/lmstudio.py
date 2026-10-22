"""Connector cho LM Studio local (OpenAI-compatible API).
Mặc định http://localhost:1234/v1 — đổi qua env LMSTUDIO_BASE_URL nếu cần.
Dùng cho task đơn giản, riêng tư, không tốn API cloud.
"""
import json
import os
import urllib.request
from typing import Optional

from .base import BaseAgent, AgentResult


class LMStudioAgent(BaseAgent):
    name = "lmstudio"
    capabilities = [
        "summarize",
        "classify",
        "extract",
        "quick_qa",
    ]
    parallel_safe = True

    def __init__(self, base_url: Optional[str] = None, model: Optional[str] = None):
        self.base_url = (base_url or os.environ.get("LMSTUDIO_BASE_URL", "http://localhost:1234/v1")).rstrip("/")
        self.model = model or os.environ.get("LMSTUDIO_MODEL", "")

    def run(self, prompt: str, system: Optional[str] = None, timeout: int = 120) -> AgentResult:
        url = f"{self.base_url}/chat/completions"
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        body = {"messages": messages, "temperature": 0.3}
        if self.model:
            body["model"] = self.model
        req = urllib.request.Request(
            url, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                j = json.load(r)
            text = j["choices"][0]["message"]["content"]
            usage = j.get("usage", {})
            return AgentResult(self.name, True, output=text, meta={
                "model": j.get("model", self.model or "local"),
                "input_tokens": usage.get("prompt_tokens", 0),
                "output_tokens": usage.get("completion_tokens", 0),
            })
        except Exception as e:
            return AgentResult(self.name, False, error=f"LMStudio lỗi: {e}")
