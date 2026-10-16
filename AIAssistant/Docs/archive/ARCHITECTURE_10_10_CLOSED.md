# ARCHITECTURE STATUS — KIỂM CHỨNG LẠI (2026-10-14)

Tài liệu này **không tuyên bố 10/10**. Nó ghi những gì đo được, cách đo lại,
và danh sách việc còn mở. Luật ADR 0015 ("claim phải được enforce hoặc xóa")
áp dụng cho chính tài liệu này: mọi con số dưới đây đều tái lập được bằng
một lệnh.

> **Ảnh chụp có ngày, không phải trạng thái hiện tại.** Các con số ở §1 là
> snapshot của đợt kiểm chứng 2026-10-14 (7 800 stmts / 2 064 branches,
> 1 625 test, 12 mutant). Giá trị đo mới nhất — 7 973 stmts / 2 096 branches,
> 1 721 test, 19/19 mutant (2026-10-15, sau đợt entry-point + ADR 0019) — nằm ở `ARCHITECTURE.md` §Verification và tái lập
> bằng đúng các lệnh ở mục 8 của tài liệu đó. Không chọn con số nào khi chúng
> lệch: lấy con số mới nhất và ghi ngày đo.

## 1. Số đo (2026-10-14, Python 3.11, Windows)

```bash
cd AIAssistant
python -m ruff check .                      # All checks passed
python -m mypy src/ai_assistant             # Success: no issues (130 files)
PYTHONPATH=src lint-imports --no-logo        # 4 kept, 0 broken
PYTHONPATH=src python -m coverage run -m unittest discover -s tests -p "test_*.py"
python -m coverage report                   # TOTAL 7800 stmts / 2064 branches, 0 miss, 0 partial
PYTHONPATH=src python scripts/mutation_check.py   # 12/12 mutants killed
```

- Test: **1625 chạy, 1 skip, 0 fail** (baseline trước khi đợt này: 1513 test,
  coverage 98.34% = 113 stmts miss + 34 partial).
- Coverage: **100.00%** statements + branches với `fail_under = 100` trong
  `pyproject.toml` — gate đi qua, không phải target treo trên tường.
- Mutation: **12/12 guard trọng yếu bị tiêu diệt** (`scripts/mutation_check.py`)
  — line/branch coverage không phát hiện được test-sai-logic, nên từng guard
  (sandbox allow-list, HITL approval, remote policy, /health, cache seal,
  cancel race) được đảo chiều một lần và test owning nó phải fail.
- Exclusion duy nhất: 2 điểm vào tiến trình `if __name__ == "__main__":`
  (`bootstrap/server.py` re-exec uvicorn, `bootstrap/server_manager.py` do Qt
  khởi động) — khai báo một lần bằng `exclude_also` trong `pyproject.toml`,
  không rải `# pragma: no cover`.
- Môi trường phải có `faiss-cpu`, `rank-bm25`, `numpy`, `Pillow`
  (`requirements-test.txt`): tests e2e RAG build index FAISS + BM25 thật.
  Thiếu chúng thì số đo thấp hơn — nhánh optional-dependency đổi theo môi
  trường, và `requirements-test.txt` ghi rõ điều đó thay vì để CI đo một
  con số khác với local.

## 2. Mâu thuẫn tài liệu trước đây và cách đóng

| Tuyên bố cũ | Nguồn | Thực tế / xử lý |
| --- | --- | --- |
| "10/10 khóa hoàn toàn" | bản cũ tài liệu này | Xóa; tài liệu viết lại theo số đo |
| Coverage 97%, 8 test lỗi | `ARCHITECTURE.md` | Cập nhật: 100.00%, 1625 test, 0 fail |
| `fail_under=100` nhưng "CI offline ~89%" | `pyproject.toml` | Cửa số đã đi kèm số đo + ngày + điều kiện môi trường |
| Floor CI = 90 | ADR 0012 §7 | Bị ADR 0016 thay thế (floor 100); giữ ADR cũ làm lịch sử |
| "không ghi 100% khi đo ~89%" | ADR 0018 | Không còn mâu thuẫn: giờ đo thật 100% |
| ~9.6/10, 1466 pass + 1 fail | `ARCHITECTURE_EXECUTION_REVIEW.md` | Số cũ; con số mới lấy ở mục 1 |

## 3. Đã đóng trong đợt này (sửa code, không sửa tài liệu)

### 3.1 P0 — lỗi thật

- **Composition root đọc sai config**: `ArchitectureSettings.load(base_dir,
  config_dir=None)` với default = `settings_config_dir()`; `bootstrap/server.py`
  trước đó truyền `src` làm base → TOML bị bỏ qua im lặng.
- **Sandbox allow-list được enforce thay vì khai báo**: `_policy_rejection`,
  `_subcommand_rejection` (chặn `cmake -E env/chdir`, `git -c`, subcommand ngoài
  danh sách), `_docker_argv`, `_PATH_POSITIONAL`; token-level block.
- **`/health` trung thực**: trả `status: degraded` + `reasons`; `/livez` luôn
  200; `/readyz` 503 `llm_not_loaded`, 200 `degraded` `rag_index_empty` (client
  Qt chỉ check HTTP 200 → tương thích).
- **`AgentExecuteRequest.history` `max_length` 20 → 200**: Qt gửi toàn bộ lịch
  sử, session dài từng trả 422.
- **`tokenize_vn`**: NFC-normalize + `\w+` unicode (trước: NFD tách ký tự).
- **Contract test Qt viết lại theo payload thật**; **A2A streaming test** đổi
  sang mount production (router tự sở hữu path, không prefix) + cursor
  `events(task_id, *, since=...)`.
- **Remote policy**: `constrained_completion` tách `_remote_leg_allowed`;
  test khẳng định remote leg bị từ chối khi thiếu `AGENT_HYBRID_POLICY=cloud_allowed`.
- **Eval tautological**:
  - `evals/run_model_in_loop_eval.py` viết lại — gọi **planner thật**
    (`PLANNER_PROMPT` + backend đã nạp), không còn import classifier đã bị xóa
    và so từ vựng không bao giờ khớp; không có backend thì in `SKIP` và **không
    in số accuracy**.
  - `evals/run_golden_set_eval.py` (test_assert fixture tồn tại) bị xóa.
- **Dead code xóa** (0 tham chiếu): `llm/vllm_backend.py`,
  `llm/inference_layer.py`, `llm/queue.py`, `adapters/persistence/{redis,postgres}_store.py`,
  `orchestration/specialists/classifier.py`, stub `_build_retrievers` trùng
  trong `bootstrap/server.py`.
- **Test phủ nhánh guard còn hở**: CAS race của `TaskService`, eviction +
  pending-cancel của `TaskCoordinator`, nhánh allow-list sandbox, loader encoder
  lười, `_resolve_model_idx` (test cũ tự viết lại logic thay vì gọi hàm),
  wiring `_wire_index` với corpus mini thật.
- **Chokepoint tool được quan sát được**: `build_container` giờ gắn
  `event_sink` mặc định (`observability/tool_events.py`) cho
  `ToolExecutionService` — mọi lần thực thi tool (vòng lặp agent, HITL đã
  duyệt, desktop action, worker A2A) đều ra một dòng JSON kèm tham số đã
  redact. Trước đó sink có sẵn trong code và có test, nhưng composition root
  không gắn nó, nên các execution không đi qua agent loop không để lại dấu
  vết nào.

### 3.2 P1/P2 — scale, observability, kiểm chứng guard

- **Ký HMAC cho cache pickle RAG** (`rag/cache_integrity.py`): cache được seal
  bằng HMAC-SHA256 với key nằm ngoài cache dir
  (`<data_dir>/.secrets/cache_hmac_key`, 0600). `load_cache` từ chối unpickle
  bất kỳ cache nào digest không khớp (thiếu key, thiếu record, file bị sửa,
  key của máy khác) — biến "cache bị gian lận chạy code" thành "cache miss +
  rebuild". Mô hình mối đe dọa được ghi rõ: không chống được attacker đã
  chạy đúng user này (họ đọc được key), nhưng chống được sửa đổi không đọc
  được và hỏng im lặng. Cache cũ không có record → từ chối một lần, build lại
  ký lại. Mọi nhánh có test riêng (`test_rag_cache_integrity.py`,
  `test_index.py`).
- **OTel span thật sự ra khỏi process được** (`observability/tracing.py` +
  `settings.otlp_exporter_endpoint`): với `AGENT_OBSERVABILITY=1` và
  `AGENT_OTLP_ENDPOINT=http://host:4318/v1/traces`, tracer được gắn
  `BatchSpanProcessor` + `OTLPSpanExporter`. Thiếu package exporter thì warning
  một dòng và span ở lại in-process — không crash. Trước đó: hook `span()` +
  `opentelemetry-api` có nhưng không exporter → span sinh ra rồi biến mất.
  Ba nhánh (có endpoint / thiếu package / không endpoint) đều có test với fake
  module trong `sys.modules`.
- **Recovery time được đo thật** (SCORECARD #4): `TaskCoordinator` nhận
  `recovery_observer` (inject từ composition root → `record_recovery` →
  histogram Prometheus `agent_recovery_seconds`); `cancel()` ghi
  `cancelled_at`, `finish()` (điểm runner thực sự dừng) báo
  `now - cancelled_at`. Coordinator vẫn không import observability (giữ import
  contract) — observer là tham số. Metric #1 (tỉ lệ eval có model) đã
  instrument và có gate trong CI (job `model-eval`, xem mục 4 — đã đóng).
- **Mutation test cho guard trọng yếu** (`scripts/mutation_check.py`):
  12/12 mutant bị tiêu diệt — allow-list executable, `cmake -E`, `git -c`,
  subcommand lạ, write-deny `.env`/policy, HITL approval, history bound,
  remote policy default, `/health` truthful, cache seal fail-closed, pending
  cancel, terminal-cancel no-op. Coverage 100% không phát hiện test-sai-lý
  logic; mutation check là lớp bảo hiểm riêng, chạy lại bằng một lệnh.
- **p95 latency theo node được trích thật** (SCORECARD #2): `span()` ghi mỗi
  lần quan sát vào cửa sổ mẫu thô (512 mẫu/node, có giới hạn);
  `p95_latency_by_node()` (`observability/metrics.py`) trả `{node: p95}`
  nearest-rank. Node có span timed: `plan`/`reason`/`tool`/`reflect`; `approval`
  là counter — không có p95 vì đo nó sẽ ra thời gian người phản hồi.
- **Tỉ lệ eval có model được báo ra thật** (SCORECARD #1): eval
  `run_model_in_loop_eval.py` thêm `model_coverage` — phần golden set mà model
  trả lời được (trả rỗng/không parse được làm giảm coverage, kể cả khi đã nạp
  backend). Không backend → in `SKIP` + `model_coverage=0`, không bịa số.
- **Trace context lan sang A2A (hai chiều)**: `current_traceparent()`
  (`observability/tracing.py`) trả W3C `traceparent` của span đang active (và
  `None` khi không có gì hợp lệ để lan — không bịa id); `TrustedA2AClient.submit`
  gắn header đó vào lệnh gọi đi (nửa outbound). Nửa inbound đã nối:
  `TraceContextMiddleware` (`bootstrap/app_factory.py`) đọc `traceparent` trên
  mọi request HTTP và `attach_inbound_traceparent()` nối caller làm remote
  parent; `TaskService._start` snapshot `contextvars` của người submit vào
  worker thread trần (threading.Thread không kế thừa contextvar), nên span của
  worker nằm chung trace với caller. Header thiếu/sai hoặc thiếu SDK → no-op:
  propagation có thể fail, request thì không.

## 4. Còn mở — chưa đóng, chưa claim

1. ~~**CI xanh chưa xác minh từ máy này**~~ — **đã đóng**: `gh` đã đăng nhập
   (`repo` + `workflow`), đã push `b180a9b..ab7ad37` và xác minh ba workflow
   cùng commit đều xanh — `Python CI` (38661493344), `C++ CI` (38661493292),
   `Agent tool-call evals` (38661493377, gồm job `model-eval` mới). Gate 100%
   (ruff/mypy/lint-imports/coverage/mutation) đã đi qua cả local lẫn CI.
2. ~~**Chạy model-in-loop eval trong CI (SCORECARD #1)**~~ — **đã đóng**: job
   `model-eval` (`agent-evals.yml`) nạp fallback mặc định của registry
   (`Qwen2.5-3B-Instruct-Q4_K_M`, wheel CPU `llama-cpp-python==0.3.36` + GGUF
   đều có cache) rồi chạy eval với gate `--min-model-coverage 0.9
   --min-accuracy 0.5`. Không nạp được model → gate FAIL (không cho pass khi
   chẳng chấm gì); câu trả lời không parse được → ungradeable làm giảm
   coverage thay vì crash (mirror đúng cách production
   `orchestration/nodes/plan.py` decode). Xác minh trên CI thật (run
   38661493377, ubuntu-latest): accuracy 4/4, coverage 4/4, p95 46.8s —
   pass. Report ghi `evals/model_in_loop_report.json` làm artifact + summary.
3. ~~**Điểm vào tiến trình** (`__main__`) vẫn nằm ngoài coverage theo `exclude_also`~~ —
   **đã đóng** (2026-10-15): thân hai entry tách thành
   `server.main()` và `server_manager.configure_stdio_for_qt()`/`qt_entry()`,
   test bằng `tests/integration/test_main_entry_points.py` (9 test: warm-up
   thất bại → exit 1, uvicorn termination → re-raise, shim win32/posix ×
   success/fail, thứ tự stdio → supervisor; `uvicorn` fake qua `sys.modules`
   như pattern OTel nên CI không cần cài thêm). `exclude_also` giờ chỉ còn
   hai dòng guard `if __name__ == "__main__":` một dòng — thân code được thực thi
   hết, không còn dòng nào sau guard là unmeasured. Đo lại: 7 973 stmts /
   2 096 branches = 100.00%, 1 721 test, `# pragma: no cover` còn 6 dòng.
4. **ResourceWarning `nul` ở cuối suite** — cosmetic, phát ra lúc shutdown
   (`sys:1:`), không làm suite fail (exit 0). Nguồn đã định danh bằng audit
   hook (`sys.addaudithook` bắt mọi `open`, kể cả C-level): chính là
   `site-packages/llama_cpp/_utils.py:7-8` — `outnull_file = open(os.devnull,
   "w")` / `errnull_file = open(os.devnull, "w")` mở ở **import-time**, là
   module-level global không bao giờ close, nên sống đến shutdown rồi GC phát
   warning (hai handle → hai lần warning). `grep` + instrumentation
   `builtins.open`/`io.open` xác nhận không có chỗ nào trong `src/` mở devnull;
   `subprocess.DEVNULL` (lsp_client) tạo raw fd chứ không phải TextIOWrapper nên
   không phải thủ phạm. Không sửa được (third-party, ngoài phạm vi repo), chỉ
   đóng được ở tầng `src/`: `lsp_client` giờ cũng đóng `stdout` PIPE trên path
   bình thường (leak handle thật, hardening kèm test). CI không cài `llama_cpp`
   (test fake `sys.modules`) nên CI sạch — nit chỉ xuất hiện ở môi trường local
   đã cài `llama-cpp-python`.
