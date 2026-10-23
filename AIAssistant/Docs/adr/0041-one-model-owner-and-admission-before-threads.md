# ADR 0041 — Một chủ sở hữu model, và admission trước khi tạo thread

- Status: Accepted
- Date: 2026-10-23
- Supersedes: none

## Bối cảnh

Vòng review thứ chín không hỏi "gate có xanh không" mà hỏi hai tính chất mà
bảng gate không đo:

1. **Khi `/admin/release-vram` trả `ok`, weight có thực sự bị giải phóng?**
2. **Một burst A2A có thể tạo nhiều worker thread hơn mức process chịu được
   không?**

Cả hai đều có lỗi tái hiện được trong khi mọi gate xanh.

### 1. Model có hai chủ sở hữu

`LlmModule.llm` giữ backend, nhưng `llm/model_loader.py` cũng giữ
`_global_backend` (cùng `get_backend()`, `load_model()`, `reload_model()`).
Route release chỉ làm:

```python
old_llm, llm_module.llm = llm_module.llm, None
del old_llm
gc.collect()
```

`_global_backend` vẫn là reference sống, nên object không bị thu hồi,
`release_ml_memory()` không giải phóng được gì, và VRAM vẫn nằm đó. Tệ hơn,
`active_model_desc` không bị xoá: `/health` báo `llm_loaded: false` trong khi
`/v1/models` vẫn quảng cáo model đã "release". Test cũ chỉ khẳng định route đã
gán `None` và trả 200 — nó pin *hành vi của route*, không pin *tính chất cần
có*: object phải có thể bị thu hồi.

### 2. Admission không tồn tại

`TaskService.submit()` tạo một `threading.Thread` cho mỗi task, không có bound.
`AGENT_MAX_CONCURRENT_RUNS` được enforce muộn trong `AgentService` (trả 503),
tức là **sau khi** thread, stack và native memory (ONNX/OpenCV) đã được cấp.
`AGENT_RATE_LIMIT_PER_MINUTE` mặc định tắt. Một burst trả 503 nhưng process đã
phải trả giá trước đó.

Không có "decision" sai để mutation bắt: quyết định **thiếu**, không phải sai.

## Quyết định

### 1. `LlmModule` là chủ sở hữu duy nhất

- `model_loader` trở thành factory thuần: `load_backend()` dựng và trả backend,
  không publish. Xoá `_global_backend`, `get_backend`, `load_model`,
  `reload_model`.
- `LlmModule.release()` xoá reference **và** metadata (`active_model_desc`,
  `is_vision_model`) trong cùng một critical section, rồi mới gọi
  `release_ml_memory()`. Release lần hai là no-op.
- `LlmModule.load_model()` giải phóng model cũ trước khi tải model mới; hai
  model không cùng resident trên GPU.
- `/health` và `/v1/models` suy ra model đang phục vụ từ backend **đang giữ**,
  không từ một chuỗi metadata có thể lệch. Sau release, `/v1/models` trả
  `{"data": []}`.

### 2. Admission trước khi có thread

- `TaskService` nhận `max_concurrent_tasks` (0 = không bound cho test/embedded).
  Composition root đọc `AGENT_MAX_CONCURRENT_TASKS` / `[runtime]
  max_concurrent_tasks`, mặc định **8**.
- `submit()` giành slot bằng `BoundedSemaphore.acquire(blocking=False)` **trước**
  khi commit task và trước `_start()`. Hết slot → task `REJECTED` bền vững kèm
  event giải thích, không tạo thread.
- `resume()` cũng giành slot (resume cũng tạo worker). Hết slot →
  `TaskCapacityError` (kế thừa `ServiceUnavailableError`), route A2A trả **429**
  kèm `Retry-After`, JSON-RPC trả error `-32000`. Không trả 409 "not resumable"
  cho một task hoàn toàn resumable.
- Worker trả slot trong `finally`; nếu `_start()` từ chối lúc shutdown, caller
  trả slot vừa giành.

## Hệ quả

- `release-vram` giờ là sự thật: sau khi trả 200, không còn reference nào giữ
  backend, và hai endpoint trạng thái không thể nói ngược nhau.
- Một burst bị chặn **trước** khi cấp thread; `AgentService` vẫn giữ run limit
  như tầng thứ hai (bảo vệ model), nhưng process không còn trả giá cho thread
  bị từ chối.
- Giới hạn mặc định 8 là chính sách triển khai, không phải hằng số code: đổi
  bằng env/TOML mà không sửa `TaskService`.
- Không đổi wire/disk format. Task bị từ chối vẫn là một row + event như
  capability không hỗ trợ (ADR 0032).

## Ghim bằng test và mutant

- `tests/unit/test_bootstrap_coverage_gaps.py`: `weakref` sau `release()`
  phải là `None`; reload gọi `release_ml_memory()` trước khi adopt backend mới;
  release lần hai trả `False`; reload khi chưa load raise.
- `tests/unit/test_http_admin_routes.py`: sau `/admin/release-vram`, cả
  `llm`, `active_model_desc` và `is_vision_model` đều cleared.
- `tests/unit/test_http_health_routes.py`: `/health` không quảng cáo model khi
  chưa load; `/v1/models` rỗng sau release.
- `tests/unit/test_task_lifecycle_guards.py`: submit thứ hai bị `REJECTED`
  **và** chỉ có một worker; slot được trả sau khi chạy xong; resume lúc đầy
  raise `TaskCapacityError`; resume sau close trả slot.
- `tests/unit/test_a2a_http_router.py`: resume lúc đầy trả 429 +
  `Retry-After`; JSON-RPC trả `-32000`.
- Chín mutant: hai cho ownership/release, hai cho health/model-list, ba cho
  admission (submit, resume, worker slot), hai cho backpressure. `MIN_MUTANTS`
  141 → **150**.

Đo lại ngày 2026-10-23: xem `Docs/SCORECARD.md` — mục "Independent review
follow-up (2026-10-23, ADR 0041)".

## Không thay đổi

- ADR 0032 vẫn sở hữu drain contract (`model_reload`); ADR này chỉ làm cho
  release bên trong nó có hiệu lực thật.
- Không nới approval, scope, transport policy hay sandbox.
- `AgentService` run limit vẫn là bound của model; bound mới là của process.
