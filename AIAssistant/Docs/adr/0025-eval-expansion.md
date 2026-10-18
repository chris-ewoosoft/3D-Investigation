# ADR 0025 — Eval mở rộng: adversarial, gateway policy, multilingual, regression

Review yêu cầu eval suite phủ thêm adversarial/injection, rollback/cancel,
multilingual, model-version regression, load/chaos. Nguyên tắc giữ nguyên từ
ADR 0015: mỗi case chạy trên **code thật** và chỉ pass khi hệ thật cho ra
hành vi đúng — không có case nào chấm điểm bằng cách hỏi lại code under test,
và một case "không chạy được" không bao giờ tính là pass.

- **Tool eval adversarial.** `tool_call_cases.json` thêm 9 case tấn công:
  tool name bịa (`shell_exec`), tham số lạ (extra bị forbid), thiếu required,
  type không được coerce (`"many"` cho int, `"true"` cho bool), params không
  phải object, action desktop không tồn tại, envelope JSON cụt.
  `run_tool_eval.py` hiểu `expect_error` (validator thật phải từ chối) và
  `expect_parse_error` (JSON phải không parse được); nếu validator nhận một
  case đáng lẽ bị chặn, eval đỏ. Thêm case unicode path để chắc rằng đường
  hợp lệ không bị chặn nhầm: 16 case, tất cả pass.
- **Gateway policy scenarios (injection/rollback).** `run_agentic_eval.py`
  thêm 8 scenario `gateway-*` chạy trên `ToolExecutionService` thật, registry
  thật, grant store thật: thiếu scope → `forbidden`; đúng scope → executor
  chạy; principal vượt classification trần → `data_policy_denied`; tool lạ
  không bao giờ tới executor; write thiếu approval → `approval_required`
  (executor không chạy); grant chỉ mở **đúng** invocation đã duyệt — params
  khác bị chặn; dùng lại grant lần hai → từ chối (single-use); `revoke_all`
  (đường cancel/rollback) → grant hết hiệu lực. Tổng: 25 scenario / 114 case.
- **Multilingual golden.** Bộ model-in-the-loop thêm `vn_003`/`vn_004`/`en_003`
  (7 case vi/en). Gate CI vẫn
  `--min-model-coverage 0.9 --min-accuracy 0.5`: với 7 case, sàn coverage 0.9
  đòi **cả 7** câu trả lời parse được, còn accuracy chỉ cần hơn một nửa. Đo
  cục bộ trên fallback 3B đã pin (SHA-256 khớp `9c9f56a3…`): **6/7 kind đúng
  (86%), coverage 7/7, p95 3.94s** — case trượt duy nhất là `en_003` (câu hỏi
  thông tin nội bộ dự án nhưng model trả `direct`), ghi nguyên trong report
  thay vì sửa kỳ vọng. Hành vi SKIP khi không có backend (không in accuracy)
  giữ nguyên.
- **Model-version regression.** Cờ `--baseline PATH` +
  `--baseline-tolerance` (mặc định 0.05) so `accuracy` và `model_coverage`
  với report cũ; tụt quá ngưỡng → exit 1 kèm số đo hai bên. File baseline
  không đọc được → exit 2 **trước khi** load model: một so sánh không chạy
  được không được phép "pass". Metric không có trong baseline bị bỏ qua (report
  cũ vẫn dùng được), và so sánh có epsilon 1e-9 để "đúng bằng tolerance"
  không thành regression vì sai số float. 8 test pin arithmetic + CLI.
- **Load/chaos.** Burst qua ASGI stack thật (`test_rate_limit`: 5 admit,
  3 throttle, **0 lỗi 5xx**); budget stress bằng thread thật
  (`test_session_serialization`: 2 session chạy song song, session thứ ba bị
  `ServiceUnavailableError`, slot nhả sau khi run kết thúc, run crash vẫn nhả).
- **CI pin theo revision** (ADR 0023): job model-eval tải đúng commit và
  `sha256sum -c`, cache key gắn revision — "model-version regression" không
  thể đến từ model trôi.
- **Bảo vệ bằng test:** eval scripts trả exit code theo từng case trong
  `agent-evals.yml`; `tests/unit/test_model_in_loop_eval.py` pin arithmetic,
  gate và 8 case baseline; `tests/unit/test_rate_limit.py` + 
  `tests/unit/test_session_serialization.py` pin load/chaos.
- **Hệ quả:** kết quả eval giờ so sánh được theo thời gian (artifact
  `evals/model_in_loop_report.json`), và mọi case adversarial fail nếu biên
  giới thật hở — đó là mục đích, không phải side effect.
