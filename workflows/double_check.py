"""Workflow double_check 3 lớp:
Lớp 1 - agent chính làm việc
Lớp 2 - Gemini verify độc lập (không biết kết quả lớp 1)
Lớp 3 - gộp + chốt kết quả
"""
from orchestrator import Orchestrator, SubTask


def run(orch: Orchestrator, title: str, work_prompt: str,
        work_kind: str = "reasoning", work_agent: str = None,
        check_prompt_extra: str = "") -> dict:
    # Lớp 1: làm việc chính
    t1 = orch.run(title + " [lớp 1: thực hiện]", [
        SubTask(id="w1", kind=work_kind, prompt=work_prompt,
                agent_name=work_agent, parallel=False),
    ])
    if t1.status != "done":
        return {"ok": False, "stage": "work",
                "error": t1.results[0].get("error", "lớp 1 thất bại"),
                "task_id": t1.id}

    work_output = t1.final

    # Lớp 2: Gemini verify độc lập
    verify_prompt = (
        "Bạn là người kiểm tra độc lập. Dưới đây là YÊU CẦU GỐC và KẾT QUẢ do một AI khác làm.\n"
        "Hãy kiểm tra: (1) kết quả có đáp ứng đúng yêu cầu không, "
        "(2) có lỗi logic/số liệu sai không, (3) có thiếu sót gì không.\n"
        "Trả lời ngắn gọn: ĐẠT hoặc CHƯA ĐẠT + liệt kê vấn đề cụ thể (nếu có).\n\n"
        f"YÊU CẦU GỐC:\n{work_prompt}\n\nKẾT QUẢ CẦN KIỂM:\n{work_output}\n"
        + (f"\nTiêu chí thêm: {check_prompt_extra}" if check_prompt_extra else "")
    )
    t2 = orch.run(title + " [lớp 2: verify]", [
        SubTask(id="v1", kind="verify", prompt=verify_prompt,
                agent_name="gemini", parallel=False),
    ])
    verdict = t2.final if t2.status == "done" else "KHÔNG VERIFY ĐƯỢC"

    # Lớp 3: chốt
    passed = "ĐẠT" in verdict.upper() and "CHƯA ĐẠT" not in verdict.upper()
    return {
        "ok": passed,
        "stage": "done",
        "work_output": work_output,
        "verdict": verdict,
        "task_ids": [t1.id, t2.id],
    }
