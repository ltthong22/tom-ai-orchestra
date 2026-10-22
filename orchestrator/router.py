"""Slot-based router: giới hạn số task đồng thời trên mỗi AI agent,
ghi latency từng lần gọi, chọn agent nhanh nhất còn slot trống.

Học từ pattern "Live activity" dashboard: mỗi provider có slots giới hạn,
latency hiển thị trực tiếp, router tự chọn đường nhanh nhất.
"""
import sqlite3
import threading
import time
from datetime import datetime

DB_PATH = "data/orchestra.db"

# Slot mặc định cho từng agent (điều chỉnh theo khả năng provider)
DEFAULT_SLOTS = {
    "gemini": 8,      # API cloud, chịu tải tốt
    "deepseek": 4,    # API cloud (khi có key)
    "lmstudio": 2,    # local laptop, yếu hơn
    "muse": 1,        # chat chính, làm nối tiếp
    "claude": 4,      # dự phòng
}


class SlotRouter:
    """Singleton router: acquire/release slot + thống kê latency."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls, db_path: str = DB_PATH):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._init(db_path)
            return cls._instance

    def _init(self, db_path: str):
        self.db_path = db_path
        self._semaphores: dict[str, threading.Semaphore] = {}
        self._in_flight: dict[str, int] = {}
        self._init_db()
        self._init_semaphores()

    def _init_db(self):
        import os
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        con = sqlite3.connect(self.db_path)
        con.execute("""CREATE TABLE IF NOT EXISTS agent_stats(
            agent TEXT PRIMARY KEY,
            calls INTEGER DEFAULT 0,
            fails INTEGER DEFAULT 0,
            total_ms INTEGER DEFAULT 0,
            avg_ms INTEGER DEFAULT 0,
            last_ms INTEGER DEFAULT 0,
            updated_at TEXT)""")
        con.commit()
        con.close()

    def _init_semaphores(self):
        for agent, slots in DEFAULT_SLOTS.items():
            if agent not in self._semaphores:
                self._semaphores[agent] = threading.Semaphore(slots)
                self._in_flight[agent] = 0

    def _ensure_agent(self, agent: str):
        if agent not in self._semaphores:
            slots = DEFAULT_SLOTS.get(agent, 2)
            self._semaphores[agent] = threading.Semaphore(slots)
            self._in_flight[agent] = 0

    def max_slots(self, agent: str) -> int:
        return DEFAULT_SLOTS.get(agent, 2)

    def in_flight(self, agent: str) -> int:
        self._ensure_agent(agent)
        return self._in_flight.get(agent, 0)

    def has_free_slot(self, agent: str) -> bool:
        return self.in_flight(agent) < self.max_slots(agent)

    def acquire(self, agent: str, timeout: int = 300) -> bool:
        """Chờ slot trống (tối đa timeout giây). Trả về True nếu lấy được."""
        self._ensure_agent(agent)
        ok = self._semaphores[agent].acquire(timeout=timeout)
        if ok:
            with self._lock:
                self._in_flight[agent] = self._in_flight.get(agent, 0) + 1
        return ok

    def release(self, agent: str):
        self._ensure_agent(agent)
        with self._lock:
            self._in_flight[agent] = max(0, self._in_flight.get(agent, 0) - 1)
        self._semaphores[agent].release()

    def record(self, agent: str, ms: int, success: bool):
        """Ghi latency 1 lần gọi vào DB."""
        con = sqlite3.connect(self.db_path)
        row = con.execute(
            "SELECT calls, fails, total_ms FROM agent_stats WHERE agent=?",
            (agent,)).fetchone()
        if row:
            calls, fails, total = row
            calls += 1
            fails += 0 if success else 1
            total += ms
        else:
            calls, fails, total = 1, 0 if success else 1, ms
        avg = total // calls if calls else 0
        con.execute(
            """INSERT OR REPLACE INTO agent_stats
               VALUES(?,?,?,?,?,?,?)""",
            (agent, calls, fails, total, avg, ms,
             datetime.now().isoformat(timespec="seconds")))
        con.commit()
        con.close()

    def stats(self, agent: str) -> dict:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT * FROM agent_stats WHERE agent=?", (agent,)).fetchone()
        con.close()
        d = dict(row) if row else {
            "agent": agent, "calls": 0, "fails": 0,
            "total_ms": 0, "avg_ms": 0, "last_ms": 0, "updated_at": None}
        d["in_flight"] = self.in_flight(agent)
        d["max_slots"] = self.max_slots(agent)
        d["free"] = d["max_slots"] - d["in_flight"]
        return d

    def all_stats(self) -> list:
        agents = sorted(set(list(DEFAULT_SLOTS) + list(self._semaphores)))
        return [self.stats(a) for a in agents]

    def pick_fastest(self, candidates: list[str]) -> str | None:
        """Chọn agent nhanh nhất (avg_ms thấp nhất) trong số còn slot trống.
        Agent chưa có lịch sử (calls=0) được ưu tiên thử."""
        best, best_key = None, None
        for agent in candidates:
            if not self.has_free_slot(agent):
                continue
            s = self.stats(agent)
            # chưa từng gọi -> ưu tiên cao nhất (key = -1)
            key = -1 if s["calls"] == 0 else s["avg_ms"]
            if best_key is None or key < best_key:
                best, best_key = agent, key
        return best


def get_router() -> SlotRouter:
    return SlotRouter()
