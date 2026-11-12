# ADR 0047 — Đóng ba ranh giới còn fail-open: side effect, retention, desktop ack

- Status: Accepted
- Date: 2026-11-02
- Supersedes: none

## Bối cảnh

Review kiến trúc 2026-11-01 đọc mã nguồn thay vì bảng cổng xanh và tìm ra ba
chỗ mà một giá trị/đối tượng **không được nhận diện** vẫn đi qua, hoặc một
tenant có thể tác động lên tenant khác. Cả ba đều nằm trong lớp "guard" mà
ADR 0032/0039/0043 đã tuyên bố là fail-closed, nên đây là lỗ hổng của *cùng
một quyết định* ở những method anh em chưa được ghim — đúng hình mẫu mà
`Docs/ARCHITECTURE_SRC.md` §4.8/§4.10 mô tả.

1. **Từ vựng side effect.** `ToolSpec.side_effect` được khai báo là
   `SideEffect`, nhưng dataclass không kiểm tra runtime. `PluginRegistry`
   chỉ hỏi `spec.side_effect in {DESKTOP, WRITE, EXECUTE}`; một giá trị lạ
   (`"delete"`) không thuộc tập đó nên **lọt** qua cả kiểm tra scope lẫn
   approval, rồi `tool_definition_from_plugin` tra
   `_SIDE_EFFECT_TO_POLICY.get(..., "read_only")` và cấp policy yếu nhất.
   Nhãn không nhận diện được **mở rộng** quyền thay vì đóng lại.
2. **Retention bộ nhớ.** `MemoryStore._shrink_to_bytes` xoá dòng cũ nhất
   trên **toàn cục**; `BoundedShortTermMemory` đẩy session cũ nhất trên
   **toàn cục**. Một tenant ghi nhiều (hoặc mint nhiều session id) đẩy bay
   bộ nhớ của tenant khác dù tenant đó đang rất nhỏ. Đây là lỗi công bằng
   và cô lập, không phải rò rỉ đọc chéo — nhưng nó phá vỡ chính lời hứa
   "owner scopes the write exactly like recall" của ADR 0043.
3. **Desktop ack.** `UIActionService.ui_action_result` chỉ xác thực bằng
   `request_id` (capability 128-bit), trong khi `ApprovalService.approve`
   còn kiểm `session_id`. Hai cửa cùng resume một run nhưng mạnh yếu khác
   nhau: một ack cũ/đang xếp hàng từ cửa sổ khác có thể resume nhầm run.

## Quyết định

### 1. Từ vựng side effect đóng tại `ToolSpec`, không tại nơi tra cứu

`ToolSpec.__post_init__` từ chối mọi `side_effect` không phải thành viên
`SideEffect`. `StrEnum` vẫn so sánh bằng với chuỗi thành viên, nhưng **kiểu**
mới là quyết định: một giá trị ngoài từ vựng bị chặn ngay khi record được
dựng, trước khi bất kỳ bảng policy nào được hỏi. Vì vậy
`tool_definition_from_plugin` dùng tra cứu trực tiếp
(`_SIDE_EFFECT_TO_POLICY[spec.side_effect]`) thay cho `.get(..., "read_only")`:
một fallback ở đây sẽ tái lập đúng lỗi vừa đóng.

Chokepoint là record, không phải registry: mọi đường dựng (catalogue legacy,
plugin đăng ký, fake trong test) đều đi qua nó, nên không có đường thứ hai
để quên. `PluginRegistry` giữ nguyên các kiểm tra scope/approval của nó.

### 2. Ngân sách retention chia theo owner

- `MemoryStore.prune` vẫn hết hạn theo **tuổi** trên toàn cục (cùng một
  cutoff cho mọi owner — tuổi là chính sách, không phải tài nguyên tranh
  chấp), nhưng ngân sách byte được chia đều cho các owner đang có mặt và
  mỗi owner bị thu về phần của mình, cũ nhất trước. Tổng vẫn ≤ `max_bytes`,
  và không owner nào bị xoá vì owner khác phình ra.
- `BoundedShortTermMemory` gắn `owner` vào từng session và đẩy LRU **trong
  phạm vi owner đó**. Tổng bộ nhớ bị chặn bởi `max_sessions × số tenant mà
  deployment admit` — số tenant là cấu hình, không phải dữ liệu request.
- `ShortTermMemoryPort.record` nhận `owner` (keyword-only); `AgentService`
  truyền đúng owner của run.

### 3. Desktop ack buộc vào session, và không tiêu capability khi từ chối

`AgentUiActionResultRequest` có `session_id` (cùng hình dạng với
`AgentApproveRequest`: mặc định rỗng, tối đa 128 ký tự). `ui_action_result`
so `request.session_id` với session đã park; **rỗng là mismatch**, không
bao giờ là pass. Khi mismatch, entry được **đặt lại** vào pending store
trước khi raise `AuthorizationError`, nên session sở hữu vẫn ack được —
từ chối trả lời không được phép phá capability của người khác.

Client Qt echo `session_id` trong payload ack (như `approveAgentAction` đã
làm); nhánh A2A `desktop_ack` của `LangGraphAgentOrchestrator` truyền
`task.id`, vì task được khôi phục resume đúng run của nó.

## Hệ quả

- Một plugin khai báo `side_effect` ngoài từ vựng **fail bootstrap** với
  thông báo nêu tên tool và giá trị, thay vì âm thầm thành read-only.
- Một tenant không còn đẩy được bộ nhớ của tenant khác; đổi lại, tenant
  phình to sẽ bị thu về phần chia đều của nó kể cả khi tổng store còn dư.
- Client cũ không gửi `session_id` trong ack sẽ nhận `AuthorizationError`
  (fail-closed). Bản Qt trong repo gửi kèm, và contract test
  `tests/contract/test_agent_contract_qt.py` ghim payload đó.
- `session_id` rỗng trong `AgentApproveRequest` vốn đã fail-closed; hai cửa
  resume giờ có cùng một luật.

## Kiểm chứng

- `tests/unit/test_plugins.py`: giá trị ngoài từ vựng bị từ chối tại
  construction.
- `tests/unit/test_memory_store.py`: ngân sách chia đều — owner nhỏ giữ
  nguyên dòng cũ nhất của mình trong khi owner lớn bị thu; store rỗng với
  cap vẫn trả 0.
- `tests/unit/test_short_term_memory.py`: eviction chỉ đụng session của
  owner đang ghi.
- `tests/unit/test_agent_resume_services.py`: ack sai session bị từ chối,
  entry còn nguyên, và ack đúng sau đó vẫn hoàn tất.
- `tests/unit/test_langgraph_adapter.py`: nhánh `desktop_ack` truyền
  `task.id`.
- Sáu mutant mới trong `scripts/mutation_check.py` đảo từng quyết định;
  `MIN_MUTANTS` tăng tương ứng (249 → 255).
