# ADR 0039 — Plugin tool là công dân hạng nhất, cầu nối fail-closed, session state có biên

- Status: Accepted
- Date: 2026-10-22
- Supersedes: none

## Bối cảnh

Vòng review ngày 2026-10-22 đọc `AIAssistant/` theo ba câu hỏi hẹp hơn các
vòng trước: *tool của plugin có thật sự đi qua cùng một catalogue/chính sách
với tool builtin không*, *các cầu nối policy/alias/MCP có fail-closed không*,
và *state theo session có bị giữ vô hạn không*. Cả ba đều có lỗi đã tái hiện
được, trong khi mọi gate cũ vẫn xanh:

1. **Plugin tool không nhìn thấy được từ agent.** `PluginRegistry` đăng ký
   được tool và gateway trực tiếp chạy được nó, nhưng catalogue của agent chỉ
   lấy từ `Config/agent_tools.json`; `supervisor.authorise` lại chỉ tra bảng
   `ALLOWED_TOOLS` của builtin. Kết quả: plugin tool *executable* nhưng
   *invisible* và bị refuse nếu agent có nhìn thấy tên.
2. **Chính sách bị chia làm nhiều nguồn.** Policy builtin nằm trong
   `tool_contract.py`, routing nằm trong `supervisor.py`, danh sách alias nằm
   trong `agents/completion.py`, còn MCP tự đăng ký mọi spec plugin mà không
   hỏi principal/approval. Alias `app_action_*` có policy `read_only` dù
   `application_action` là `desktop_ack`/`restricted`; một policy label lạ
   rơi về `read_only`; một classification lạ rơi về `RESTRICTED` (chưa phải
   đỉnh lattice).
3. **Session lock sống theo process.** `AgentService` giữ
   `dict[session_id, RLock]` và không bao giờ xoá entry. Đo được: 10 000
   session id → 10 000 lock còn lại sau khi mọi run đã kết thúc.

Đồng thời scorecard ghi nhận `llm/` và `internal/` mới chỉ được *đọc*, chưa
có mutant — một lỗ trong gate, không phải trong kiến trúc.

## Quyết định

### 1. Một catalogue, một bảng quyền

- `domain/specialists.py` sở hữu vocabulary: `Specialist.PLUGIN`, các tool
  class (`RESEARCH_TOOLS`, `VERIFICATION_TOOLS`, `CODE_TOOLS`,
  `WORKFLOW_TOOLS`, `LSP_TOOLS`) và **một** reverse index
  `TOOL_SPECIALISTS`.
- `supervisor.ALLOWED_TOOLS` là projection thuần của `TOOL_SPECIALISTS`;
  `tool_contract_for()` đọc cùng bảng. Không còn bản sao thứ hai của
  least-privilege policy.
- LSP tools (`go_to_definition`, `find_references`) trở thành tool research
  read-only thật sự thay vì advertised-but-refused; do đó
  `REGISTERED_NOT_ENABLED_TOOLS` rỗng và được test giữ đúng rỗng.
- Alias desktop action nằm một chỗ trong `tools/aliases.py`; completion
  parser, policy contract và composition root cùng dùng
  `canonical_tool_name()`. Catalogue thực thi chỉ đăng ký canonical tool.
- `ToolDefinition` mang thêm `plugin_id`, `specialists`, `input_schema` và
  `spec` để bridge plugin không mất provenance/policy; `ToolRegistry` dùng
  `dataclasses.replace` nên mọi field sống sót qua dynamic catalog.
- `merge_plugin_tools()` đưa tool của plugin đã bật vào đúng catalogue agent
  nhìn thấy. Builtin id luôn được phép (allow-list của operator không thể
  tắt tool của sản phẩm), plugin không được shadow builtin, và mỗi plugin
  tool nhận `Specialist.PLUGIN`.
- Composition root bind row động
  `{**ALLOWED_TOOLS, Specialist.PLUGIN: plugin_tools}` vào `authorise`, và
  truyền cùng policy đó cho cả approval/desktop-ack resume. Row tĩnh của
  `PLUGIN` rỗng: một caller trần không thể chạy plugin tool.
- `PluginRegistry` giới hạn registration trong plugin vừa load
  (`registration_scope`), buộc mọi tool có side effect phải khai báo
  `required_scope`, và tool write/execute còn phải `requires_approval`.
- A2A card không quảng cáo `plugin`: đó là composition local, không phải
  remote skill.

### 2. Các cầu nối fail-closed

- `tool_contract_for()` chỉ dùng default read-only cho tên nằm trong
  `READ_ONLY_TOOLS`; tên lạ **raise** thay vì nhận contract yếu nhất.
- `tool_spec_from()` từ chối policy label lạ, từ chối alias như một
  execution spec, và coi classification thiếu/lạ là `REGULATED`.
- `build_container()` bỏ alias khỏi execution catalogue; plugin spec được
  bridge nguyên văn, không suy lại từ display label.
- MCP chỉ bind tool mà `MCP_PRINCIPAL` thật sự chạy được: bỏ tool cần
  approval (MCP không có HITL), tool vượt scope, tool vượt clearance, và
  alias. Không còn quảng cáo một tool rồi refuse mọi call.

### 3. Session state có biên

`SessionLockPool` reference-count cả holder **và** thread đang chờ trước khi
wait; entry bị xoá khi user cuối rời đi. Map vì vậy bị chặn bởi session đang
live, không phải bởi mọi id đã từng thấy. `session_id` của execute/approve/
cancel có `max_length=128` — nó là lock-pool key và checkpointer thread id.

### 4. Mutation phủ `llm/` và `internal/`

- `llm/inference.py`: policy cloud (AND, không OR), gate trước khi gửi bytes,
  bound kích thước response.
- `llm/model_loader.py`: unpinned artifact, so hash không phân biệt hoa/thường,
  tái dùng file đã pin, mismatch sau download, revision của mmproj.
- `internal/langgraph_engine.py`: forward checkpointer, forward config,
  `get_state`.

`MIN_MUTANTS` tăng 112 → **134**; mỗi guard mới có mutant và test giết nó.
Không còn mutant "registered-but-not-enabled" đã stale; nó được thay bằng
guard fail-closed của `tool_contract_for()`.

## Hệ quả

- Plugin tool đi đúng một đường: catalogue → route `Specialist.PLUGIN` →
  `authorise` row động → gateway/approval. Không cần sửa builtin specialist
  để cấp quyền cho plugin.
- Một alias không thể tạo policy thứ hai; một label lạ không thể hạ bậc
  tool; MCP không thể hứa điều principal không làm được.
- Bộ nhớ lock theo session không còn tăng theo lịch sử session.
- Đo lại ngày 2026-10-22: **2 200 test OK, 6 skipped; coverage 100.00 %
  (9 591 statements, 2 588 branches); mutation 134/134 killed, 0 survived,
  0 stale; mypy 0 issues/142 files; import-linter 9/9; ruff 0.**

## Không thay đổi

- Không nới approval hay scope nào. Plugin write/execute vẫn cần approval và
  scope; MCP vẫn không có HITL.
- Không đổi wire/disk format của chat session, pending action hay task store.
- Corpus/at-rest/RAG giữ nguyên các quyết định của ADR 0027/0028/0038.
