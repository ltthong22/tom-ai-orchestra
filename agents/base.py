"""Base class cho mọi AI agent connector."""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AgentResult:
    """Kết quả trả về từ một agent."""
    agent_name: str
    success: bool
    output: str = ""
    error: str = ""
    meta: dict = field(default_factory=dict)


class BaseAgent(ABC):
    """Mọi connector AI phải kế thừa class này."""

    name: str = "base"
    # Mô tả năng lực để orchestrator chọn agent phù hợp
    capabilities: list = field(default_factory=list) if False else []
    # True = chạy song song được, False = phải chạy nối tiếp
    parallel_safe: bool = True
    # Số task đồng thời tối đa (slot) — router dùng để giới hạn tải
    max_slots: int = 2

    @abstractmethod
    def run(self, prompt: str, system: Optional[str] = None, timeout: int = 120) -> AgentResult:
        """Chạy 1 prompt, trả về AgentResult."""
        ...

    def check(self) -> bool:
        """Kiểm tra agent có sẵn sàng không (key/server sống)."""
        try:
            r = self.run("Trả lời đúng 1 từ: OK", timeout=20)
            return r.success
        except Exception:
            return False
