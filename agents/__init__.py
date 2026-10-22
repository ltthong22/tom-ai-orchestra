"""Registry các AI agent. Thêm AI mới = thêm file + 1 dòng ở đây."""
from .base import BaseAgent, AgentResult
from .claude import ClaudeAgent
from .gemini import GeminiAgent
from .lmstudio import LMStudioAgent

# Muốn thêm Groq/DeepSeek/OpenRouter: tạo file mới rồi thêm vào dict này
AGENT_CLASSES = {
    "claude": ClaudeAgent,
    "gemini": GeminiAgent,
    "lmstudio": LMStudioAgent,
}


def get_agent(name: str) -> BaseAgent:
    cls = AGENT_CLASSES.get(name)
    if not cls:
        raise ValueError(f"Không có agent '{name}'. Có sẵn: {list(AGENT_CLASSES)}")
    return cls()


def list_agents() -> list:
    return list(AGENT_CLASSES.keys())
