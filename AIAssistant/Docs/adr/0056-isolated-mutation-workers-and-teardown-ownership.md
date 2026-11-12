# ADR 0056 — Mutation worker biệt lập và quyền sở hữu tài nguyên teardown

- Status: Accepted
- Date: 2026-11-12
- Supersedes: cách mutation in-place của ADR 0015/0032

## Bối cảnh

Full mutation sweep 303 trường hợp ghi trực tiếp vào `src/`, chạy tuần tự và
dùng lock không phân biệt process còn sống hay đã chết. Khi job bị ngắt, checkout
có thể còn source đang mutated hoặc lock mồ côi; thời gian chạy cũng vượt
ngân sách CI. Một owning suite đỏ sẵn có thể làm mutant trông như bị giết.

Full test suite đồng thời phát hiện handle log và `nul` chỉ được đóng khi
interpreter thoát. Nguyên nhân là logging re-compose bỏ handler cũ mà không đóng,
teardown import Torch chỉ để giải phóng cache, và test decoder import `llama_cpp`
thật dù chỉ cần một grammar sentinel.

## Quyết định

1. Mutation chạy trong temporary sandbox. Mỗi worker có một bản sao source/test
   riêng; checkout không bị ghi. Tất cả mutant cùng owning suite ở cùng worker
   và suite được preflight trên source nguyên bản trước khi mutation.
2. CI dùng `--jobs 2`. `--files` chỉ chấp nhận exact path, hỗ trợ danh sách
   phân cách bằng dấu phẩy và không hạ `MIN_MUTANTS`.
3. Lock ghi PID. Chỉ lock của process đã chết mới được thu hồi tự động;
   lock của process còn sống vẫn fail-fast.
4. `setup_logging()` đánh dấu handler nó sở hữu. Re-compose đóng handler
   owned, nhưng chỉ detach handler ngoại lai vì lifetime thuộc caller.
5. `release_ml_memory()` không import Torch. Nó chỉ xóa CUDA cache nếu module
   đã được load và chứa mọi lỗi driver trong boundary best-effort.
6. Test completion inject grammar double. Test gate nâng `ResourceWarning` thành lỗi
   để handle bị bỏ ngỏ không còn được xem là output vô hại.

## Kiểm chứng

- `tests/unit/test_mutation_gate.py` ghim selection, partition, sandbox ignore,
  baseline preflight và khôi phục source.
- `tests/unit/test_logging.py` ghim close-owned/detach-foreign.
- `tests/unit/test_hardware.py` ghim no-import, CUDA/no-CUDA và exception boundary.
- Ba mutant về lifecycle nâng `MIN_MUTANTS` 303 → **306**.
- Full gate đo 2 588 test, 6 skipped; 11 065 statement, 3 030 branch, coverage
  100%; 306/306 mutant killed, 0 survived, 0 stale.

## Hệ quả

Mutation có thể chạy song song mà không làm bẩn worktree, failure baseline không
thể giả làm mutant killed, và teardown có owner rõ ràng. Chi phí là mỗi worker
cần một bản sao nhỏ của package/test; model, cache, log và output sinh ra bị loại.
