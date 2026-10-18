# RUNBOOK — AIAssistant vận hành production

Tài liệu này dành cho profile **server/nhiều người dùng**. Desktop single-user
không cần runbook này: mọi cổng dưới đây là opt-in (ADR 0023/0024) và mặc định
giữ nguyên hành vi cũ.

## 1. Checklist bật profile production

Đặt trong `.env` (hoặc secret store) rồi restart server:

| Biến | Giá trị production | Vì sao |
|------|--------------------|--------|
| `AGENT_SANDBOX_RUNTIME` | `docker` + `AGENT_SANDBOX_IMAGE=<image đã duyệt>` | OS boundary cho tool execution |
| `AGENT_SANDBOX_REQUIRE_ISOLATION` | `1` | Từ chối chạy nếu container runtime không bật (fail closed) |
| `AGENT_CHECKPOINT_BACKEND` | `sqlite` hoặc `postgres` (+ `AGENT_CHECKPOINT_URL`) | Durable resume sau restart |
| `AGENT_CHECKPOINT_REQUIRED` | `1` | Từ chối rơi về memory im lặng |
| `AGENT_MAX_CONCURRENT_RUNS` | `2`–`8` tuỳ RAM/GPU | Trần burst nhiều session, hết slot trả 503 |
| `AGENT_RATE_LIMIT_PER_MINUTE` | `60`–`300` tuỳ client | Chặn vòng lặp client lỗi, trả 429 + `Retry-After` |
| `AGENT_OBSERVABILITY` | `1` (+ `AGENT_OTLP_ENDPOINT` nếu có collector) | Bật counter/histogram cho cảnh báo bên dưới |

Kiểm tra sau khi bật:

```powershell
python AIAssistant/scripts/verify_models.py --require-all   # pin model, exit 0
python AIAssistant/scripts/generate_sbom.py --output sbom.json
```

Log khởi động **không được** có `Checkpoint backend unavailable` hay
`Using MemorySaver`; có nghĩa là cổng chưa ăn.

## 2. SLO tối thiểu (khởi điểm, chỉnh theo tải thật)

Các ngưỡng dưới đây là *đề xuất khởi điểm* cho deployment nội bộ; số đo thật
phải lấy từ metrics của chính bạn. Không có con số nào ở đây được đo sẵn.

| SLO | Nguồn đo | Gợi ý |
|-----|----------|-------|
| Availability (`/readyz`) | probe của orchestrator | ≥ 99% theo tuần |
| p95 độ trễ node plan/reason/tool/reflect | `p95_latency_by_node()` (SCORECARD #2) | Theo baseline máy chạy; cảnh báo khi tăng > 30% so với tuần trước |
| Tỉ lệ 5xx | `agent_requests_total{status="503"}` / tổng | < 1% giờ cao điểm |
| Tỉ lệ throttle | `rate(agent_http_throttled_total[5m])` | < 5% request; cao hơn là quá tải hoặc client lỗi |
| Recovery cancel-to-stop | p95 `agent_recovery_seconds` (SCORECARD #4) | < 2s |
| Nguyên vẹn artifact | log `ModelIntegrityError` | 0 lần |

## 3. Cảnh báo (Prometheus)

```promql
# Client bị chặn liên tục — xem peer nào, đừng nới limit ngay
sum by (surface) (rate(agent_http_throttled_total[5m])) > 0.5

# Hết slot run (503 do budget): điều tra tải, không phải lỗi hệ thống
rate(agent_requests_total{status="503"}[5m]) > 0.1

# Trade-off: tỉ lệ lỗi tổng
sum(rate(agent_requests_total{status=~"5.."}[5m])) / sum(rate(agent_requests_total[5m])) > 0.01
```

Log-based (chưa có counter riêng, xác nhận trước khi trông cậy):

- `ModelIntegrityError` / `does not match the pinned SHA-256` → dừng rollout,
  chạy mục 4.5; **không** tắt verify.
- `Sandbox isolation is required by policy` → runtime docker chết; xem mục 4.4.
- `Checkpoint backend unavailable` khi `AGENT_CHECKPOINT_REQUIRED=1` → startup
  đã fail (đúng thiết kế); khi =0 là đang chạy memory, không durable.

## 4. Quy trình

### 4.1 Chỉnh quota (rate limit / run budget)

1. Xác định nguồn: 429 (rate limit) hay 503 (hết slot).
2. Nới bằng env rồi restart: `AGENT_RATE_LIMIT_PER_MINUTE`,
   `AGENT_MAX_CONCURRENT_RUNS`. Không cần deploy code.
3. Rate limiter là **per-process**: chạy nhiều replica thì giới hạn thật nằm ở
   ingress/reverse proxy — đặt ở đó trước, env chỉ là lớp trong cùng.
4. Probe `/livez`, `/readyz` được miễn 429; nếu chúng đỏ thì không phải limiter.

### 4.2 Checkpoint: durability, backup, retention, restore

- Backend: `sqlite` mặc định ghi tại `AIAssistant/Cache/checkpoints.sqlite`
  (`AGENT_CHECKPOINT_PATH`); `postgres`/`redis` qua `AGENT_CHECKPOINT_URL`.
- Retention: `cleanup_old_checkpoints(max_days=30, max_size_mb=50)` chạy trong
  lifespan lúc startup; file sqlite phình > 50MB sẽ được VACUUM. Giữ nguyên
  hành vi này trừ khi có lý do đo được.
- Backup sqlite: dừng server (hoặc `sqlite3 checkpoints.sqlite ".backup <đích>"`)
  rồi copy file — copy nóng có thể ra bản hỏng.
- Backup postgres: `pg_dump` theo lịch của bạn; restore là quy trình của DBA.
- Restore sqlite: thay file khi server dừng, start lại với
  `AGENT_CHECKPOINT_REQUIRED=1`, chạy lại một phiên đã hủy trước đó để xác
  nhận resume.

### 4.3 Cổng checkpoint từ chối khởi động

`RuntimeError: AGENT_CHECKPOINT_REQUIRED=1 refuses the in-memory checkpointer`
→ đặt `AGENT_CHECKPOINT_BACKEND=sqlite|postgres|redis`. Backend cấu hình hỏng
(`RuntimeError: … failed to initialize`) → xem nguyên nhân chuỗi `from` trong
log (đường dẫn/DSN/package thiếu). Cả hai trường hợp là fail closed có chủ
đích: sửa cấu hình, không hạ cổng trừ khi chấp nhận mất durability.

### 4.4 Sandbox isolation

- Bật `AGENT_SANDBOX_RUNTIME=docker` + image đã duyệt trước khi bật
  `AGENT_SANDBOX_REQUIRE_ISOLATION=1`.
- Docker argv đã cố định `--network none`, `--cap-drop ALL`,
  `--security-opt no-new-privileges`; thêm cờ nới quyền là thay đổi bảo mật —
  cần review riêng.
- Khi lệnh bị từ chối vì policy: sửa lệnh/allow-list hoặc image; **không**
  hạ `AGENT_SANDBOX_REQUIRE_ISOLATION` để "chạy tạm".

### 4.5 Luân chuyển pin model (ADR 0023)

1. Chọn commit mới trên Hugging Face; lấy SHA-256 nội dung từ tree API
   (`?expand=true`, trường `lfs.oid`) hoặc hash file đã tải:
   `Get-FileHash -Algorithm SHA256 <file>`. Không dùng `x-linked-etag`.
2. Cập nhật **cùng lúc** `AIAssistant/config/models.toml` và hằng số tương ứng
   trong `src/ai_assistant/config/model_registry.py`.
3. `python AIAssistant/scripts/verify_models.py --require-all` → exit 0.
4. Chạy eval có so sánh: tải artifact `model-in-loop-report` của CI gần nhất,
   rồi `python AIAssistant/evals/run_model_in_loop_eval.py --model-path <gguf>
   --baseline <report.json>` — tụt quá `--baseline-tolerance` (mặc định 5%)
   thì dừng, điều tra trước khi rollout.

### 4.6 Chuỗi cung ứng định kỳ (mỗi release)

```powershell
cd AIAssistant
uv pip compile requirements.txt --python-version 3.11 --universal --generate-hashes -o requirements.lock
uv pip compile requirements-test.txt --python-version 3.11 --universal --generate-hashes -o requirements-test.lock
python scripts/generate_sbom.py --output sbom.json
git diff requirements.lock requirements-test.lock   # review version nhảy có chủ đích
```

CI job `test` cài `requirements-test.lock` bằng `pip install --require-hashes`
— hash lệch thì job đỏ ngay ở bước install, trước cả khi test chạy. Nếu pip
báo `THESE PACKAGES DO NOT MATCH THE HASHES`, dừng lại: hoặc wheel bị
republish (điều tra trước khi cập nhật hash), hoặc lock sinh từ index khác.
Sửa range trong `requirements*.txt` mà quên regenerate lock thì
`test_lockfile_sync` (trong suite CI) đỏ kèm tên requirement — không có
đường "cài bộ cũ trong im lặng". Hash ở đây chứng minh *danh tính artifact*,
không phải provenance: nếu audit cần provenance thì phải bật
attestation (GitHub artifact attestations hoặc cosign/SLSA) — repo này chưa
bật, ghi nhận thay vì giả vờ có.
SBOM (171 distribution kèm hash + 6 artifact model) cũng được CI ghi thành
artifact `aiassistant-sbom` mỗi lần chạy `agent-evals.yml`; diff SBOM giữa
hai release là cách kiểm tra dependency trôi.

### 4.7 Eval & regression

- CI (`agent-evals.yml`) chạy tool eval (16 case, gồm adversarial),
  agentic eval (25 scenario/114 case, gồm gateway policy) và model-in-loop
  (7 case vi/en, gate coverage 0.9 / accuracy 0.5, model pin theo revision).
  Fallback 3B đã pin đo cục bộ: 6/7 kind đúng, coverage 7/7, p95 ~3.9s.
- Muốn so theo thời gian: tải artifact `model-in-loop-report`, dùng `--baseline`
  như mục 4.5 bước 4. Eval đỏ nghĩa là hành vi đổi — điều tra, không cập nhật
  kỳ vọng cho tới khi hiểu nguyên nhân.
