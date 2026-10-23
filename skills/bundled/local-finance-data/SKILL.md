---
name: local-finance-data
description: Đọc số liệu tài chính THẬT từ Finance Manager của TomStudio (tổng tài sản, tổng nợ, số dư ví, công nợ phải thu, doanh thu). Dùng khi user hỏi "tháng này thu bao nhiêu", "còn nợ bao nhiêu", "số dư VCB". Chỉ đọc qua HTTP GET sau khi login, không ghi/sửa dữ liệu.
---

# Finance Data — đọc số liệu Finance Manager

Skill này cho phép AI trong Tom AI Orchestra trả lời câu hỏi tài chính
bằng SỐ LIỆU THẬT từ app Finance Manager, không bịa số.

## Dùng khi
User hỏi về tiền: "tháng này thu bao nhiêu", "tổng tài sản bao nhiêu",
"còn công nợ bao nhiêu", "số dư VCB/MoMo", "doanh thu năm nay".

## Cách hoạt động
Orchestra tự inject dữ liệu vào prompt (xem `web/app.py::_data_context`).
Module đọc: `data_providers/finance_data.py` — đăng nhập HTTP vào
Finance Manager rồi GET trang dashboard (cache 5 phút).

## Cấu hình (env, Railway Variables hoặc .env)
```
FINANCE_APP_URL=https://mediafinance.up.railway.app
FINANCE_APP_USER=<tài khoản app tài chính>
FINANCE_APP_PASS=<mật khẩu app tài chính>
```
Chưa cấu hình → AI trả lời rõ là chưa nối, nhờ user cung cấp.

## API (Python)
```python
from data_providers import finance_data
finance_data.is_configured()   # True nếu đủ env
finance_data.format_summary()  # text: tổng tài sản, nợ, số dư ví, công nợ quá hạn
finance_data.fetch_dashboard() # dict thô: {"stats": {...}, "overdue": [...]}
```

## Nguyên tắc
- CHỈ ĐỌC. Module không có hàm ghi/sửa nào.
- Mật khẩu chỉ nằm trong env, không log, không đưa vào prompt.
