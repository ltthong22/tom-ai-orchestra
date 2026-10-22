"""Core orchestrator: nhận task -> phân tích -> chia subtask ->
giao cho AI phù hợp -> thu kết quả -> double-check -> gộp -> trả kết quả.
"""
import concurrent.futures
import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from agents import get_agent, list_agents
from agents.base import AgentResult

try:
    from tokens.tracker import get_tracker, suggest_cheaper_model
    _tracker = get_tracker()
except Exception:
    _tracker = None

try:
    from skills.loader import suggest_skills
except Exception:
    def suggest_skills(desc, limit=3):
        return []

DB_PATH = "data/orchestra.db"


@dataclass
class SubTask:
    id: str
    kind: str          # capability: summarize/classify/code/verify/...
    prompt: str
    system: Optional[str] = None
    agent_name: Optional[str] = None  # ép dùng agent cụ thể, None = tự chọn
    parallel: bool = True


@dataclass
class Task:
    id: str
    title: str
    created_at: str
    status: str = "pending"   # pending/running/done/failed
    subtasks: list = field(default_factory=list)
    results: list = field(default_factory=list)
    final: str = ""


# Gợi ý chọn agent theo loại việc (có thể override bằng agent_name)
KIND_AGENT_HINT = {
    "code": "muse",          # việc khó → Muse (chat chính) xử lý
    "reasoning": "muse",
    "decide": "muse",
    "plan": "muse",
    "browse": "muse",        # thao tác web → Muse
    "execute": "muse",       # chạy lệnh/máy → Muse
    "orchestrate": "muse",
    "summarize": "gemini",
    "classify": "gemini",
    "extract": "gemini",
    "translate": "gemini",
    "verify": "gemini",      # verify độc lập nên dùng model khác với người làm
    "quick_qa": "lmstudio",  # việc nhẹ ưu tiên local cho rẻ
}


class Orchestrator:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init_db()

    # ---------- DB ----------
    def _init_db(self):
        import os
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        con = sqlite3.connect(self.db_path)
        con.execute("""CREATE TABLE IF NOT EXISTS tasks(
            id TEXT PRIMARY KEY, title TEXT, created_at TEXT,
            status TEXT, final TEXT)""")
        con.execute("""CREATE TABLE IF NOT EXISTS subtasks(
            id TEXT PRIMARY KEY, task_id TEXT, kind TEXT, prompt TEXT,
            agent TEXT, success INTEGER, output TEXT, error TEXT)""")
        con.commit()
        con.close()

    def _save_task(self, task: Task):
        con = sqlite3.connect(self.db_path)
        con.execute(
            "INSERT OR REPLACE INTO tasks VALUES(?,?,?,?,?)",
            (task.id, task.title, task.created_at, task.status, task.final))
        for st in task.subtasks:
            r = next((x for x in task.results if x.get("subtask_id") == st.id), {})
            con.execute(
                "INSERT OR REPLACE INTO subtasks VALUES(?,?,?,?,?,?,?,?)",
                (st.id, task.id, st.kind, st.prompt[:2000],
                 r.get("agent", ""), int(r.get("success", 0)),
                 (r.get("output", "") or "")[:8000], r.get("error", "")))
        con.commit()
        con.close()

    # ---------- Chia việc ----------
    def pick_agent(self, kind: str, prefer: Optional[str] = None) -> str:
        """Chọn agent theo loại việc. Ưu tiên agent được chỉ định.
        Task đơn giản -> tự động dùng model rẻ (tiết kiệm token)."""
        if prefer and prefer in list_agents():
            return prefer
        hint = KIND_AGENT_HINT.get(kind, "gemini")
        # Tối ưu: task đơn giản luôn dùng gemini flash (free)
        cheap = suggest_cheaper_model("", kind) if _tracker else None
        if cheap == "gemini-3-flash-preview" and kind in (
                "summarize", "classify", "extract", "translate", "quick_qa"):
            return "gemini"
        return hint

    def suggest_skills_for(self, st: "SubTask", limit: int = 3) -> list:
        """Gợi ý skill phù hợp cho 1 subtask."""
        desc = f"{st.kind} {st.prompt[:300]}"
        return suggest_skills(desc, limit=limit)

    def estimate_task(self, subtasks: list) -> dict:
        """Ước lượng token TRƯỚC khi chạy toàn bộ task."""
        if not _tracker:
            return {"est_total": 0, "warn": False}
        total = 0
        for st in subtasks:
            b = _tracker.check_budget(st.prompt, st.kind)
            total += b["est_total"]
        return {
            "est_total": total,
            "warn": total > _tracker.warn_threshold,
            "threshold": _tracker.warn_threshold,
        }

    def _run_subtask(self, st: SubTask, task_id: str = "") -> dict:
        agent_name = st.agent_name or self.pick_agent(st.kind)
        try:
            agent = get_agent(agent_name)
        except ValueError as e:
            return {"subtask_id": st.id, "agent": agent_name, "success": False, "error": str(e)}
        t0 = time.time()
        r: AgentResult = agent.run(st.prompt, system=st.system)
        ms = int((time.time() - t0) * 1000)
        # Log token usage
        if _tracker:
            meta = r.meta or {}
            _tracker.log(
                agent=r.agent_name,
                model=meta.get("model", agent_name),
                prompt=st.prompt,
                output=r.output or "",
                duration_ms=ms,
                task_id=task_id,
                subtask_id=st.id,
                input_tokens=meta.get("input_tokens", 0),
                output_tokens=meta.get("output_tokens", 0),
            )
        return {
            "subtask_id": st.id, "agent": r.agent_name,
            "success": r.success, "output": r.output, "error": r.error,
        }

    # ---------- Chạy task ----------
    def run(self, title: str, subtasks: list[SubTask]) -> Task:
        task = Task(
            id="task-" + uuid.uuid4().hex[:8],
            title=title,
            created_at=datetime.now().isoformat(timespec="seconds"),
            subtasks=subtasks,
        )
        task.status = "running"
        self._save_task(task)

        parallel = [s for s in subtasks if s.parallel]
        serial = [s for s in subtasks if not s.parallel]
        results = []

        if parallel:
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
                futs = {ex.submit(self._run_subtask, s, task.id): s for s in parallel}
                for fut in concurrent.futures.as_completed(futs):
                    results.append(fut.result())
        for st in serial:
            results.append(self._run_subtask(st, task.id))

        task.results = results
        ok = all(r["success"] for r in results)
        task.status = "done" if ok else "failed"
        # Gộp kết quả: nối output theo thứ tự subtask
        order = {s.id: i for i, s in enumerate(subtasks)}
        task.final = "\n\n---\n\n".join(
            r["output"] for r in sorted(results, key=lambda x: order.get(x["subtask_id"], 0))
            if r["success"] and r["output"]
        )
        self._save_task(task)
        return task

    def get_task(self, task_id: str) -> Optional[dict]:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            con.close()
            return None
        subs = con.execute("SELECT * FROM subtasks WHERE task_id=?", (task_id,)).fetchall()
        con.close()
        d = dict(row)
        d["subtasks"] = [dict(s) for s in subs]
        return d

    def list_tasks(self, limit: int = 20) -> list:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT id,title,created_at,status FROM tasks ORDER BY created_at DESC LIMIT ?",
            (limit,)).fetchall()
        con.close()
        return [dict(r) for r in rows]
