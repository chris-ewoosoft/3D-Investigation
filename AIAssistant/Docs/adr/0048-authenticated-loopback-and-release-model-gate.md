# ADR 0048 — Xác thực loopback và nâng model eval thành release gate

- Status: Accepted
- Date: 2026-11-03
- Supersedes: phần “token tùy chọn trên loopback” của ADR 0008; sàn accuracy
  0.5 của ADR 0025

## Bối cảnh

Loopback giới hạn tuyến mạng nhưng không chứng minh danh tính tiến trình. Một
process khác của cùng máy vẫn có thể gọi admin/agent/chat/A2A khi token chưa
được cấu hình. MCP cũng từng được mount trên loopback mà không cần token, và
A2A tạo tenant `anonymous` khi không có credential. Đây là các nhánh fail-open.

Model-in-loop CI có bảy case nhưng sàn accuracy chỉ là 0.5. Baseline đã biết
6/7 vì `en_003` phân loại câu hỏi vị trí lưu cấu hình repository thành
`respond`; gate vẫn xanh nên không thực sự chặn regression chất lượng.

## Quyết định

1. `AIServerProcessController` sinh 32 byte ngẫu nhiên cho mỗi Python child,
   truyền cùng token qua `AI_ADMIN_TOKEN`, `AI_AGENT_TOKEN`, `AI_A2A_TOKEN` và
   `AI_MCP_TOKEN`. Token không được ghi vào command line hay log.
2. `AIAssistant` gắn `Authorization: Bearer <token>` vào mọi request đặc quyền.
   Các guard Python trả 503 nếu surface không có token cấu hình, kể cả peer
   loopback; credential sai trả 401 và peer/origin sai vẫn trả 403.
3. A2A không tạo tenant ẩn danh. MCP không được mount nếu thiếu token. Health và
   readiness là probe duy nhất cố ý không yêu cầu bearer.
4. CI model-in-loop dùng `--min-accuracy 0.9`. Với bảy case, 6/7 không đạt;
   release cần 7/7. Prompt planner định nghĩa rõ câu hỏi về vị trí storage/config
   của repository luôn dùng `rag_search`, là lỗi cụ thể của `en_003`.

## Bằng chứng

- C++: `TestAIServerProcessController` kiểm tra token 256-bit mới cho từng
  controller và bốn biến môi trường giống nhau; `TestAIAssistant` kiểm tra mọi
  lớp request dùng cùng bearer. Hai mutation tương ứng nâng sàn C++ 94 → 96.
- Python: route/security/contract/integration tests kiểm tra missing token,
  wrong token, loopback và A2A tenant. Hai mutation mới nâng `MIN_MUTANTS`
  255 → 257; docs contract ghim trực tiếp `--min-accuracy 0.9` trong workflow.
- Gate đo ngày 2026-11-03: 2 464 tests; 10 409 statements và 2 828 branches,
  100% coverage; Ruff, mypy và 9 import contracts xanh.

## Hệ quả

Qt và Python phải được khởi động như một cặp sở hữu chung process token; gọi
server thủ công phải cấu hình token và gửi bearer. CI model thật có thể đỏ cho
tới khi pinned 3B đạt 7/7 — đó là hành vi mong muốn, không hạ sàn để lấy màu
xanh. KMS/DPAPI, rollback anchor và volume encryption vẫn là control triển khai
được SCORECARD ghi rõ, không thuộc quyết định transport/model này.
