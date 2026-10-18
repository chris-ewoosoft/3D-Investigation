# ADR 0024 — Admission governor: rate limit theo peer + budget run + SLO

Review (8.3/10) nêu "chưa có rate limit/quota/concurrency budget/SLO/runbook".
Batch này thêm đúng bốn thứ đó ở tầng **admission** — nơi request bị chặn
trước khi tốn model/tool — thay vì trả lời lỗi mềm trong payload, và mọi
ngưỡng đều là env opt-in để desktop single-user giữ nguyên hành vi.

- **Rate limit theo peer.** `adapters/http/rate_limit.py`:
  `PeerRateLimiter` cửa sổ trượt cố định theo `client.host` (fallback bucket
  `"unknown"`), prune bảng khi vượt ngưỡng để bảng không phình theo số peer.
  `RateLimitMiddleware` trả **429 + `Retry-After`** (giây, ceil của phần cửa
  sổ còn lại) và đếm `agent_http_throttled_total{surface="middleware"}`.
  `/livez` và `/readyz` được miễn trong middleware: probe bị 429 sẽ khiến
  orchestrator restart một server khoẻ mạnh.
- **Bật bằng env, không hard-code.** `AGENT_RATE_LIMIT_PER_MINUTE` (0/tắt =
  mặc định desktop). `create_app` chỉ gắn middleware khi ngưỡng > 0; thứ tự
  chồng middleware: `TraceContextMiddleware` ngoài cùng (ADR 0001) →
  `RateLimitMiddleware` → `BodySizeLimitMiddleware` → CORS.
- **Budget run toàn cục.** `AgentService(max_concurrent_runs=…)` giữ một
  `BoundedSemaphore` **không chặn**: hết slot raise `ServiceUnavailableError`
  → handler domain sẵn có dịch thành `503 {"detail": …}` trước khi run chiếm
  session. Slot nhả trong `finally` trên *mọi* đường thoát, kể cả run crash.
  Lock per-session vẫn là trần thứ nhất (1 run/session); budget là trần thứ
  hai cho burst nhiều session — đúng chỗ local model không theo kịp.
  `AGENT_MAX_CONCURRENT_RUNS=0` = không giới hạn (desktop).
- **Wiring ở composition root.** `server.py` truyền `max_concurrent_runs` và
  chạy retention `cleanup_old_checkpoints()` trong lifespan;
  `app_factory.create_app` gắn limiter như trên.
- **Bảo vệ bằng test:** `test_rate_limit` (ngân sách theo peer và cửa sổ,
  prune, exempt probes, 429 + `Retry-After`, counter qua metrics facade, và
  burst qua ASGI stack thật: 5 admit/3 throttle/0 lỗi 5xx);
  `test_session_serialization` (budget đóng trên nhiều session rồi nhả, run
  crash vẫn nhả slot, budget 0 = không giới hạn); `test_final_edges_b` (chỉ
  gắn middleware khi được cấu hình); `test_settings` (parse env).
- **SLO & runbook.** `Docs/RUNBOOK.md` định nghĩa SLO tối thiểu, cảnh báo
  gắn vào counter/histogram **có thật** trong `observability/metrics.py`
  (`agent_http_throttled_total`, `agent_requests_total`, `agent_recovery_seconds`,
  p95 in-process của SCORECARD #2), cùng quy trình chỉnh quota không cần
  deploy code và quy trình xử lý 503 khi hết slot.
- **Hệ quả:** mặc định desktop không đổi; deployment chia sẻ nhiều người dùng
  đặt hai biến trên và theo dõi throttle rate. Giới hạn đã biết: limiter là
  per-process (deploy nhiều replica cần limiter ở reverse proxy/ingress) —
  ghi trong RUNBOOK, không giả vờ là distributed.
