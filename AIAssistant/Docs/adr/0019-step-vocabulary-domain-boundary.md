# ADR 0019 — Step vocabulary về domain, runtime gate ở hai biên giới

- **Chủ sở hữu từ vựng step là domain.** `Step`/`StepType`/`has_step_type`
  chuyển từ `orchestration.state` sang `domain.steps` (stdlib-only, không
  framework). Lý do: `application` không được import `orchestration`
  (import-linter contract), nên khi `AgentState.steps` thành `list[Step]`
  (finding 19), `AgentRunResult.steps` không còn cách nào khác là
  `tuple[dict[str, Any], ...]` — comment cũ trong `state.py` mô tả đúng
  ràng buộc này nhưng đồng thời khẳng định sai rằng `AgentState.steps` "vẫn
  là `dict`"; comment đó đã bị sửa và ranh giới nó nêu được đóng bằng slice
  này. `orchestration.state` re-export (`Step`, `StepType`, `has_step_type`)
  để 13 file import sẵn có không đổi đường dẫn; test
  `test_state_reexports_the_domain_vocabulary` ghim hai đường dẫn về một
  object.
- **`AgentRunResult.steps: tuple[Step, ...]`** — biên giới
  application→HTTP typed; ghim bằng
  `test_agent_run_result_steps_are_typed` (introspection `get_type_hints`).
- **`validate_steps(raw)` là song runtime của TypedDict**, thuộc domain
  (cùng triết lý với `domain/approvals`: policy ở trong core):
  container phải là `list`, phần tử phải là `dict`, và `type` phải nằm trong
  `get_args(StepType)` — kiểm tra **đọc từ chính literal**, nên vocabulary
  thêm giá trị thì gate tự nới theo, không có bảng thứ hai để lệch.
  - `type` sai → `ValueError` → **fail-closed** (client switch không có arm
    cho discriminator lạ, và entry nửa-parse không được phép tới resume).
  - Khóa payload lạ → **giữ nguyên** (step log là dữ liệu hiển thị; drop khóa
    mà server mới hơn ghi xuống sẽ hỏng nó sau khi downgrade).
  - `ValueError` (không phải `TypeError`) theo convention `a2a_client`: caller
    bỏ qua payload hỏng bằng một nhánh bắt duy nhất.
- **Đúng hai biên giới mà step list đi qua dữ liệu không do process này tạo**,
  cả hai đều gate + log (quy ước finding 25 — refused phải thấy được, nuốt
  thì không):
  1. `PendingActionStore.load()` — file pending trên đĩa, nơi A2A resume
     sống lại sau khi khởi động lại process. Entry có step log không parse
     được bị **drop whole**, warn `"Dropping pending action …"`; entry hỏng
     không làm chìm entry lành bên cạnh.
  2. `DeterministicAgentOrchestrator._restore` — `task.metadata` chứa
     continuation, và submitter A2A truyền được `metadata` vào
     `TaskService.submit`; step element không hợp lệ → trả `None` →
     nhánh `"Task continuation cannot be restored"` sẵn có.
  LangGraph resume **không** phải biên giới thứ ba: continuation của nó chỉ
  mang `kind`/`action_id`/`request_id`, còn `steps` đọc từ pending store
  (biên giới 1).
- **Không model HTTP/A2A inbound nào có field `steps`** — pin bằng test
  (`InboundModelPinTests`): ai thêm field `steps` vào request model sẽ làm
  test fail trước, buộc chủ ý phải đi qua gate thay vì thành crossing ngầm.
- **Ngoài phạm vi slice này** (đã có chủ khác): quyết định *ai được approve
  gì* là ADR 0009 (grant dùng-một-lần, bound theo invocation); mô hình tin
  cậy grant của adapter deterministic chỉ chạy trong embed/tests theo ADR
  0003; `history`/`metadata` inbound là message, không phải step.
- **Enforcement:** `tests/unit/test_step_boundary_validation.py` (18 test:
  validator, hai crossing, pin inbound, re-export) + 3 mutant trong
  `scripts/mutation_check.py` (bỏ kiểm vocabulary ở validator, bỏ gate ở
  load, bỏ gate ở `_restore` — cả ba phải bị test owning giết).
