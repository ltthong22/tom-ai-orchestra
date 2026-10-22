"""Connector cho Google Gemini (free tier).
Key lấy từ env GEMINI_API_KEY. Không commit key vào repo.
"""
import json
import os
import urllib.request
from typing import Optional

from .base import BaseAgent, AgentResult

DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview")
API_BASE = "https://generativelanguage.googleapis.com/v1beta"


class GeminiAgent(BaseAgent):
    name = "gemini"
    capabilities = [
        "summarize",      # tóm tắt
        "classify",       # phân loại
        "extract",        # trích xuất thông tin
        "translate",      # dịch
        "verify",         # verify độc lập (double-check lớp 2)
        "quick_qa",       # hỏi đáp nhanh
    ]
    parallel_safe = True

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY", "")
        self.model = model or DEFAULT_MODEL

    def run(self, prompt: str, system: Optional[str] = None, timeout: int = 120) -> AgentResult:
        if not self.api_key:
            return AgentResult(self.name, False, error="Thiếu GEMINI_API_KEY")
        url = f"{API_BASE}/models/{self.model}:generateContent?key={self.api_key}"
        body = {"contents": [{"parts": [{"text": prompt}]}]}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        req = urllib.request.Request(
            url, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                j = json.load(r)
            text = j["candidates"][0]["content"]["parts"][0]["text"]
            # token thật từ API (usageMetadata), fallback ước lượng
            um = j.get("usageMetadata", {})
            meta = {
                "model": self.model,
                "input_tokens": um.get("promptTokenCount", 0),
                "output_tokens": um.get("candidatesTokenCount", 0),
            }
            return AgentResult(self.name, True, output=text, meta=meta)
        except Exception as e:
            return AgentResult(self.name, False, error=f"Gemini lỗi: {e}")
