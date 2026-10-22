"""Workflow parallel: chia nhiều subtask chạy song song, gộp kết quả."""
from orchestrator import Orchestrator, SubTask


def run(orch: Orchestrator, title: str, items: list[dict]) -> dict:
    """items: [{"kind":..., "prompt":..., "agent":... (optional), "system":... (optional)}]"""
    subtasks = [
        SubTask(id=f"p{i}", kind=it.get("kind", "quick_qa"),
                prompt=it["prompt"], system=it.get("system"),
                agent_name=it.get("agent"), parallel=True)
        for i, it in enumerate(items)
    ]
    task = orch.run(title + " [parallel]", subtasks)
    return {
        "ok": task.status == "done",
        "task_id": task.id,
        "results": task.results,
        "final": task.final,
    }
