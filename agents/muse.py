"""Agent Muse — chính là trợ lý AI của anh Tom (chạy trong chat Muse).

Không gọi API ngoài. Khi orchestrator giao việc cho 'muse', task được
ghi vào hàng đợi data/muse-queue/ để Muse (chat chính) xử lý.
Web UI hiển thị trạng thái 'chờ Muse xử lý'.
"""
import json
import os
import time
import uuid
from typing import Optional

from .base import BaseAgent, AgentResult

QUEUE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "data", "muse-queue")


class MuseAgent(BaseAgent):
    name = "muse"
    capabilities = [
        "plan",        # lập kế hoạch
        "code",        # việc khó, code phức tạp
        "reasoning",   # suy luận sâu
        "decide",      # quyết định
        "orchestrate", # điều phối việc phức tạp
        "browse",      # dùng trình duyệt, thao tác web
        "execute",     # chạy lệnh, thao tác máy
    ]
    parallel_safe = False  # Muse xử lý tuần tự từng việc cho chắc

    def run(self, prompt: str, system: Optional[str] = None, timeout: int = 120) -> AgentResult:
        os.makedirs(QUEUE_DIR, exist_ok=True)
        qid = uuid.uuid4().hex[:8]
        item = {
            "id": qid,
            "prompt": prompt,
            "system": system,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "status": "pending",
        }
        path = os.path.join(QUEUE_DIR, f"{qid}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(item, f, ensure_ascii=False, indent=1)
        return AgentResult(
            self.name, True,
            output=f"Đã chuyển cho Muse (chat chính). Task ID: {qid}. "
                   f"Muse sẽ xử lý và cập nhật kết quả.",
            meta={"queue_id": qid, "queue_path": path},
        )

    @staticmethod
    def pending_count() -> int:
        if not os.path.isdir(QUEUE_DIR):
            return 0
        return len([f for f in os.listdir(QUEUE_DIR) if f.endswith(".json")])

    @staticmethod
    def list_pending() -> list:
        items = []
        if not os.path.isdir(QUEUE_DIR):
            return items
        for f in sorted(os.listdir(QUEUE_DIR)):
            if not f.endswith(".json"):
                continue
            try:
                with open(os.path.join(QUEUE_DIR, f), encoding="utf-8") as fh:
                    d = json.load(fh)
                if d.get("status") == "pending":
                    items.append(d)
            except Exception:
                pass
        return items
