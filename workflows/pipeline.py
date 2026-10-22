"""Workflow pipeline: chạy nối tiếp, output bước trước làm input bước sau.
Dùng {prev} trong prompt để chèn output bước trước.
"""
from orchestrator import Orchestrator, SubTask


def run(orch: Orchestrator, title: str, steps: list[dict]) -> dict:
    """steps: [{"kind":..., "prompt":..., "agent":... (optional)}]"""
    prev_output = ""
    all_ids = []
    for i, st in enumerate(steps):
        prompt = st["prompt"].replace("{prev}", prev_output)
        task = orch.run(f"{title} [pipeline bước {i+1}/{len(steps)}]", [
            SubTask(id=f"s{i}", kind=st.get("kind", "reasoning"),
                    prompt=prompt, agent_name=st.get("agent"), parallel=False),
        ])
        all_ids.append(task.id)
        if task.status != "done":
            return {"ok": False, "failed_step": i + 1,
                    "error": task.results[0].get("error", "?"), "task_ids": all_ids}
        prev_output = task.final
    return {"ok": True, "final": prev_output, "task_ids": all_ids}
