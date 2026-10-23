"""Registry các AI agent. Thêm AI mới = thêm file + 1 dòng ở đây."""
from .base import BaseAgent, AgentResult
from .gemini import GeminiAgent
from .lmstudio import LMStudioAgent
from .muse import MuseAgent

# Muốn thêm Groq/DeepSeek/OpenRouter: tạo file mới rồi thêm vào dict này
AGENT_CLASSES = {
    "gemini": GeminiAgent,
    "lmstudio": LMStudioAgent,
    "muse": MuseAgent,
}


def get_agent(name: str) -> BaseAgent:
    cls = AGENT_CLASSES.get(name)
    if not cls:
        raise ValueError(f"Không có agent '{name}'. Có sẵn: {list(AGENT_CLASSES)}")
    return cls()


def list_agents() -> list:
    return list(AGENT_CLASSES.keys())
