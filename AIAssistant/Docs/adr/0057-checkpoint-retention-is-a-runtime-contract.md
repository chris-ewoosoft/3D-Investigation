# ADR 0057 — Checkpoint retention là hợp đồng runtime

- Status: Accepted
- Date: 2026-11-12
- Supersedes: phần retention chỉ theo dung lượng của ADR 0024

## Bối cảnh

`cleanup_old_checkpoints(max_days=30, max_size_mb=50)` công bố hai chiều
retention nhưng runtime chỉ dùng `max_size_mb`. Một database nhỏ có thể giữ
checkpoint quá hạn vô thời hạn trong khi RUNBOOK nói rằng cửa sổ 30 ngày được
thực thi. Coverage 100% vẫn xanh vì hàm đã chạy; bảy mutant cũ của adapter
checkpoint không đảo quyết định theo tuổi nên mutation gate cũng không thấy.

Schema SQLite của LangGraph không có cột thời gian riêng. Timestamp nằm trong
payload checkpoint, nhưng payload đó được AES-GCM seal. `checkpoint_id` do
LangGraph sinh là UUIDv6 và vì vậy mang timestamp có thứ tự mà không cần mở
nội dung đã mã hóa.

## Quyết định

1. Retention theo tuổi chạy ở mọi startup cho SQLite, không phụ thuộc file đã
   vượt ngưỡng dung lượng hay chưa.
2. Tuổi được đọc từ UUIDv6 theo UUID epoch. Chỉ thread có toàn bộ checkpoint id
   kiểm chứng được mới đủ bằng chứng để xoá.
3. Retention xoá **toàn bộ thread** khi checkpoint mới nhất của nó quá hạn,
   thay vì cắt một phần chain của session còn hoạt động.
4. Một thread chứa id legacy/custom được giữ fail-closed; runtime không đoán
   timestamp rồi xoá dữ liệu không thể chứng minh là cũ.
5. Khi size cap chạy, database giữ 100 thread mới nhất theo checkpoint id và
   xoá cả checkpoint lẫn pending write của thread bị loại.
6. Hai mutant ghim class quyết định: đảo chiều so sánh tuổi và bỏ guard cho id
   không kiểm chứng được. Test dùng schema SQLite thật và kiểm tra đồng thời
   checkpoint/writes.

## Hệ quả

- Contract `max_days=30` trong RUNBOOK trở thành hành vi runtime đo được.
- Session còn hoạt động giữ nguyên toàn bộ chain; session quá hạn không để lại
  orphan trong bảng `writes`.
- Dữ liệu legacy có thể cần một migration riêng nếu operator muốn áp retention
  theo tuổi; mặc định hiện tại ưu tiên không mất dữ liệu.
- Adapter checkpoint tăng hai mutant và `MIN_MUTANTS` từ 306 lên 308.

## Kiểm chứng

- `tests/unit/test_checkpointing.py::CleanupOldCheckpointsTests`
- `tests/unit/test_checkpoint_langsmith_guards.py::VacuumDatabaseTests`
- `python scripts/mutation_check.py --files src/ai_assistant/adapters/persistence/checkpointing.py`

