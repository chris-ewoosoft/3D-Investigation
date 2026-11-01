# ADR 0040 — Một model lease cho mọi entry point chạy graph (và cho chat)

- Status: Accepted
- Date: 2026-10-22
- Supersedes: none

## Bối cảnh

ADR 0032 đưa **drain contract** vào port `LLMRuntime`: một run giữ
`model_lease()` suốt thời gian chạy, còn `/admin/reload-model` và
`/admin/release-vram` đi qua `LlmModule.model_reload()`, chỉ swap/giải phóng
backend sau khi `_active_leases == 0`. Hợp đồng đó đúng — nhưng nó mới được
áp dụng cho **một** entry point.

Vòng review ngày 2026-10-22 đọc lại các đường chạy graph và thấy cùng một lớp
lỗi ở ba chỗ còn lại:

1. `AgentService.approve()` resume graph qua `AgentRunContext.run` nhưng không
   lấy lease. Chỉ `execute()` có lease.
2. `AgentService.ui_action_result()` (desktop ack) resume đúng graph đó qua
   cùng context, cũng không lấy lease.
3. `POST /v1/chat/completions` snapshot `llm_module.llm` dưới `llm_lock` rồi
   **generate ngoài lock**. `llm_lock` chỉ bảo vệ lúc đọc handle; nó không
   tham gia drain. Reload có thể đặt `llm = None`, `del` backend và
   `gc.collect()` trong lúc stream đang chạy.

Hệ quả không phải "model cũ vẫn trả lời": một resume hoặc một chat đang giữ
con trỏ backend có thể bị giải phóng giữa lúc chạy — use-after-free/crash, hoặc
tệ hơn là một request thấy hai model khác nhau (prompt dựng theo vision flag
của model cũ, token sinh bằng model mới).

Gate cũ vẫn xanh vì mỗi mảnh đều đúng cục bộ: `execute` có lease, `approve`
được test là "delegate đúng", chat được test là "stream đúng". Không test nào
hỏi *mọi* đường dùng model có đi qua cùng một lease hay không.

## Quyết định

### 1. Lease thuộc về **mọi** entry point chạy graph

- `AgentService.execute`, `AgentService.approve` và
  `AgentService.ui_action_result` đều mở `self._model_lease()`.
- Thứ tự khoá giữ nguyên như `execute`: session lock → (global run slot) →
  model lease. Một resume xếp hàng sau session lock không giữ model trong lúc
  chờ.
- `cancel` tiếp tục **không** lấy lease và không lấy session lock: nó phải trả
  lời được trong khi một run đang giữ cả hai.

### 2. Chat giữ lease từ prompt đến token cuối

`chat_routes` lấy `with llm_module.model_lease() as backend:` và dùng
`backend` **do lease trả về**, không đọc lại `llm_module.llm`:

- lease bắt đầu trước `ChatbotAgent.build_messages` — prompt được dựng theo
  đúng `is_vision_model` của backend sẽ sinh token;
- lease bao token estimation, kiểm context, toàn bộ vòng stream và phần đọc
  metadata của response;
- fast-path `if llm_module.llm is None` ở đầu route vẫn giữ để từ chối sớm
  trước khi đọc attachment/RAG, nhưng **không** còn là bảo đảm: nếu reload
  thắng race giữa fast-path và lease, backend lease trả về là `None` và route
  trả 503.

### 3. Hợp đồng được khai báo, không duck-type

`LLMModuleLike` trong `adapters/http/_types.py` khai báo `model_lease()`.
Adapter không tự đoán method trên handle runtime; cùng lý do ADR 0032 bỏ
`getattr` khỏi `AgentService._model_lease`.

## Hệ quả

- Reload/release-VRAM giờ chờ **mọi** request đang dùng model: fresh run,
  approval resume, desktop-ack resume và chat. Đây là trade-off có chủ đích:
  chậm một lần reload tốt hơn giải phóng backend dưới một stream đang chạy.
- Một request chat thấy **một** model từ định dạng prompt (vision/không
  vision) đến token cuối, kể cả khi RAG retrieval chậm: lease được giữ trong
  lúc dựng prompt.
- Đường lỗi vẫn giải phóng lease (context manager); một inference ném exception
  không khoá reload vĩnh viễn.
- Không đổi wire/disk format, không đổi chữ ký `model_reload`, không đổi
  hành vi `cancel`.

## Ghim bằng test và mutant

- `tests/unit/test_agent_service_guards.py`: mỗi entry point
  (`execute`/`approve`/`ui_action_result`) được gọi với một runtime ghi lại
  `entered`/`exited`; test khẳng định lease **đang mở** tại thời điểm
  sub-service chạy và **đã đóng** sau khi trả về.
- `tests/unit/test_http_chat_routes.py`: lease đang mở khi `build_messages`
  chạy và khi generator sinh chunk; đóng sau response 200 và sau lỗi 500;
  backend bị reload thắng race → 503.
- `tests/integration/test_agent_ui_continuation.py`: desktop ack vẫn chạy
  được với runtime chỉ có lease (không cần backend thật) — lease là hợp đồng
  của runtime, không phải của model.
- Bốn mutant, một cho mỗi entry point: bỏ `with self._model_lease()` ở
  `execute`, `approve`, `ui_action_result`, và bỏ `with
  llm_module.model_lease()` ở chat. `MIN_MUTANTS` 134 → **138**.

Đo lại ngày 2026-10-22: xem `Docs/SCORECARD.md` — mục "Follow-up on the
2026-10-22 review (ADR 0040)".

## Không thay đổi

- ADR 0032 vẫn là chủ sở hữu của drain contract (`LlmModule.model_reload`);
  ADR này chỉ mở rộng **ai** phải tham gia.
- Không nới approval, scope hay transport policy.
- Không thêm cơ chế khoá mới: session lock, run slot và model lease vẫn là ba
  tầng hiện có, đúng thứ tự cũ.
