# ADR 0049 — Xác thực Shutdown trong ServerManager theo chính sách Loopback Token

- Status: Accepted
- Date: 2026-11-03
- Deciders: Kiến trúc sư trưởng
- Consulted: AI Assistant Core Team

## Bối cảnh

ADR 0048 đã thiết lập nguyên tắc bảo mật: loopback (`127.0.0.1`) chỉ là cơ chế định tuyến mạng (routing), không phải chứng minh danh tính hay cấp quyền. Mọi endpoint đặc quyền và phá hủy thuộc `/admin/*` (trong đó có `/admin/shutdown`) bắt buộc phải có `AI_ADMIN_TOKEN` hợp lệ trong header `Authorization: Bearer <token>`, bất kể request xuất phát từ loopback hay từ xa.

Tuy nhiên, script supervisor `src/ai_assistant/bootstrap/server_manager.py` — thành phần do tiến trình C++ Qt (`AIServerProcessController`) khởi chạy để quản lý vòng đời và chuyển đổi model GGUF — khi thực hiện switch model đã gọi hàm `shutdown_running_server()` để dừng server cũ. Trước ADR 0049, request này gửi đến `/admin/shutdown` mà không mang theo `Authorization` header.

Khi Qt cấp phát `AI_ADMIN_TOKEN` cho child process (theo đúng thiết kế của ADR 0048), yêu cầu shutdown này bị guard của FastAPI từ chối với mã HTTP 401 Unauthorized. Server cũ không thể dừng duyên dáng (graceful stop), dẫn đến cảnh báo lỗi cổng hoặc xung đột tài nguyên khi server mới cố gắng bind vào cùng cổng 8080.

## Quyết định

1. **Gắn Bearer Token khi gọi Shutdown**: `server_manager.shutdown_running_server()` đọc `transport_tokens().admin` từ `ai_assistant.settings` (chủ sở hữu cấu hình duy nhất) và tự động gắn header `Authorization: Bearer <admin_token>` khi token có cấu hình. Khi không có token (ví dụ dev môi trường mở), request gửi đi bình thường và được server xử lý theo chính sách tương ứng.
2. **Kiểm chứng Hợp đồng (Test & Branch Coverage)**: Bổ sung ca kiểm thử `test_shutdown_running_server_attaches_bearer_token` trong `tests/unit/test_server_manager.py` để xác minh header Bearer được gửi chính xác, đồng thời duy trì ca kiểm thử không có token nhằm đảm bảo 100.00% statement & branch coverage.
3. **Ghim Đột biến (Mutation Floor)**: Bổ sung 1 mutant trong `scripts/mutation_check.py` để ghim quyết định gắn token tại `server_manager.py`. Nâng sàn đột biến `MIN_MUTANTS` từ **257 lên 258** và đưa `src/ai_assistant/bootstrap/server_manager.py` vào danh sách `PINNED_DECISION_FILES`.

## Bằng chứng

- **Test Suite**: 2 465 tests (tăng +1 test so với ADR 0048), 0 failures, 6 skipped.
- **Coverage**: 100.00% (10 413 statements, 2 830 branches, 0 missed).
- **Mutation Gate**: 258/258 mutants killed (0 survived, 0 stale), đạt sàn `MIN_MUTANTS = 258`.
- **Linter & Type Check**: Ruff 0 issues, mypy 151 files `strict = true` 0 issues, 9/9 import contracts được giữ trọn vẹn.

## Hệ quả

- `server_manager.py` và Qt tương thích hoàn toàn với chính sách bảo mật loopback của ADR 0048. Việc switch model và restart server diễn ra liền mạch và an toàn.
- Tài liệu kiến trúc và toàn bộ các số đếm (statements, branches, tests, mutants, ADRs) được đồng bộ tuyệt đối trong `README.md`, `SCORECARD.md`, `pyproject.toml` và được bảo vệ bởi `test_docs_contract.py`.

