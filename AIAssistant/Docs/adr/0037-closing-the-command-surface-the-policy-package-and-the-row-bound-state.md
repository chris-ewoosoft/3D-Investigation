# ADR 0037 — Bề mặt lệnh, gói chính sách, clearance và trạng thái at-rest: đóng bằng *luật*, không bằng *danh sách*

- Status: Accepted
- Date: 2026-10-21
- Supersedes: none

## Bối cảnh

Một review kiến trúc độc lập chạy lại **mọi** gate (2105 test, coverage
100.00 %, mutation 88/88, 9/9 import contract, mypy strict) rồi đọc source.
Kết quả: **mọi gate đều xanh**, và review vẫn tìm ra bốn lỗi cùng một lớp —
*"bảng gate xanh là đo được của gate, không phải của hệ thống"*, đúng như
ADR 0034/0035 đã kết luận. Lần này lớp lỗi là: **một quy tắc được viết
thành một danh sách, và danh sách đó hở chỗ khác**.

### 1. Bề mặt lệnh: allow-list chỉ kiểm *token đầu tiên*

`_subcommand_rejection` coi **bất kỳ** token nào bắt đầu bằng `-` là "cờ
thường" và trả `None` — nghĩa là **verb thật chưa bao giờ được kiểm**. Từ đó:

```
git -C <dir> clean -fdx        → verb thật là `clean`, không nằm trong allow-list
git -ccore.sshCommand=<cmd>    → dạng gắn liền của `-c`, vốn bị chặn
pytest -p <module>             → import module tuỳ ý
pytest -o addopts=-p <module>  → import module qua cấu hình
ctest --build-and-test … --test-command <cmd> → chạy lệnh tuỳ ý
git clone <url> <đường dẫn ngoài workspace>   → ghi ra ngoài workspace
```

Và cổng "cần isolation" mặc định **tắt** (`AGENT_SANDBOX_REQUIRE_ISOLATION=0`,
`AGENT_SANDBOX_RUNTIME=local`), nên đường allow-list *là* đường mặc định và
không có OS boundary nào phía sau.

### 2. Gói chính sách của nền tảng: deny-list là một *bảng liệt kê*

`_DENIED_WRITE_TREES` liệt kê các cây policy. Bản rà soát trước bỏ sót
`agents/` (vòng phê duyệt), `adapters/persistence/` (mật mã at-rest),
`config/`, `settings.py`, và hai lá kernel mọi cổng đi qua —
`path_policy.py` / `exec_policy.py`. Một danh sách phải được bổ sung bằng
tay thì sẽ hở; luật phải là "nguồn của nền tảng không ghi được".

### 3. Cổng classification: so sánh **ngược chiều** và trần cố định

`classification_at_least(spec.maximum_classification, principal.classification)`
so sánh `trần_công_cụ >= clearance_caller` — tức **principal yếu hơn thì
được**, **principal mạnh hơn thì bị chặn**. Và *mọi* tool đều mang trần
`RESTRICTED` (hằng số trong `bootstrap/container.py`), nên cổng chỉ là một
phép so sánh với hằng số. Test cũ **pin** hành vi sai: nó khẳng định một
principal `REGULATED` bị từ chối tool `RESTRICTED`.

### 4. Trạng thái at-rest: ràng buộc theo **cột**, không theo **dòng**

AAD của AEAD là *tên field* (`task.message`, `event.data`). Một ciphertext
chép từ task này sang task khác **mở ra hoàn hảo** và được phục vụ như nội
dung của task thứ hai. Và các cột quyết định *ai được đọc* — `owner`
(tenant), `classification` (có ra khỏi ranh giới tin cậy không), `status` —
là **plaintext không xác thực**. Người có quyền ghi file state (đúng mô hình
đe doạ ADR 0036 nêu) có thể:

* đổi `owner` → task của tenant khác được phục vụ **cho tenant đó** (server
  giải mã rồi trả: đường rò rỉ nội dung qua A2A);
* đổi `classification` → hạ mức phân loại để vượt luật egress;
* đổi `status` → báo `completed` một việc chưa làm;
* hoán chuyển ciphertext giữa các dòng.

ADR 0036 đã tuyên bố *"kẻ tấn công chỉ cần quyền ghi file state thì không
còn đổi được gì"*. Câu đó **sai**.

## Quyết định

### 1. Bề mặt lệnh: verb là token vị trí đầu tiên, cờ dẫn là allow-list

* Chỉ những cờ **được khai báo** được đứng trước verb; mọi cờ khác bị từ
  chối kèm thông điệp nêu rõ. `-C`, `-c` (kể cả dạng gắn liền), `--config-env`
  là cờ thoát; `pytest -p/--pyargs/-o/--override-ini/-c/--rootdir/--basetemp/…`
  bị từ chối ở **bất kỳ vị trí nào** trên dòng lệnh; `ctest` chỉ nhận các
  cờ báo cáo đọc.
* `git clone` / `git init` bị ràng buộc đích đến trong workspace (URL không
  phải đường dẫn).
* `cmake -E` chỉ nhận sub-sub lệnh vô hại; `-P/-S/-B/--build/--install` bị
  ràng buộc đường dẫn như trước.
* Cổng isolation **mặc định bật**: một deployment không cấu hình gì thì
  chạy tool trong container. Local runtime (không container) là đường *không
  có OS boundary* — allow-list chỉ lọc lệnh, còn `cmake -P`/`ctest`/`pytest`
  vốn dĩ thực thi code — nên nó phải là một quyết định được viết ra
  (`AGENT_SANDBOX_REQUIRE_ISOLATION=0`), không phải một mặc định.

### 2. Gói chính sách: luật là *gói nền tảng*, không phải danh sách cây

`_DENIED_WRITE_TREES` giờ là `AIAssistant/src/ai_assistant` (toàn bộ nguồn
của nền tảng) cùng `AIAssistant/config`, `Config` và `src/core`. Một module
mới thêm vào nền tảng không thể ghi được nếu không được liệt kê thêm. Cổng
deny áp cho `create_directory` nữa: tạo thư mục trong nguồn của nền tảng là
bước đầu tiên của việc cài module vào đó.

### 3. Clearance: theo **scope được cấp**, không theo tuyên bố của caller

* `Principal.classification` là **clearance** — mức dữ liệu cao nhất mà
  principal được phép làm việc — và nó **suy ra từ scope** mà deployment cấp
  (`clearance_for_scopes`), không bao giờ từ `task.classification` (đó là
  *độ nhạy cảm của payload* mà caller tự khai).
* `ToolSpec.maximum_classification` là **trần dữ liệu của tool**, lấy từ
  hợp đồng riêng của tool (`tools/tool_contract.py`): tool đọc dữ liệu dự án
  là `internal`, tool ghi dự án / chạy lệnh / điều khiển desktop (mang thông
  tin đăng nhập) là `restricted`.
* So sánh là `clearance(principal) >= trần(tool)`. Cổng nay là hai trục
  khác nhau: scope nói *được làm gì*, clearance nói *dữ liệu nhạy cảm cỡ nào*.
* Nhãn lạ không được hạ trần: nhãn không ai nhận ra là `restricted`.

### 4. Trạng thái at-rest: ràng buộc theo dòng + identity được seal

* AAD của mỗi cột task mang **id của dòng** (`task.v2:<id>:<field>`), và
  payload của event mang id của task sở hữu nó (`event.v2:<id>:data`). Một
  giá trị bị chép sang dòng khác **không mở được**.
* Mỗi dòng mang thêm một **identity bundle đã seal** (`task.v2:<id>:identity`)
  chứa `owner`, `classification`, `status`, `context_id`, `origin`. Khi đọc,
  store **đối chiếu** bundle với các cột plaintext và **từ chối dòng** nếu
  lệch; một dòng không có identity thì bị từ chối (không có "không có thì
  bỏ qua").
* Một file state đã seal nhưng **có** dòng mà chưa ràng buộc theo dòng thì
  store **từ chối mở** và nêu đúng lệnh vận hành — không tự migrate (luật
  của ADR 0036: file legacy và file bị thay là *cùng một tập byte*, chỉ
  người vận hành mới quyết được).
* `scripts/migrate_state_row_binding.py` là hành động tường minh của người
  vận hành: mở mọi giá trị bằng khoá (context cũ nếu có, context mới nếu
  không — nên chạy lại vô hại), seal lại theo dòng, ghi identity, đánh dấu
  `user_version = 4`, **một transaction**; một giá trị không mở được thì huỷ
  và để nguyên state.

### 5. Một quyết định, một chỗ

* **Luật "success" của tool** thuộc về domain (`domain.tools.tool_result_succeeded`):
  vắng mặt `error` chưa đủ, một lệnh có thể kết thúc với exit code khác 0,
  và một mutation có thể báo `success: false`. Lớp application đã **chép lại
  một nửa** luật đó và **bỏ rơi `return_code`** — nên một lệnh thất bại
  được báo là thành công (tới event sink, tới audit log và tới model). Nay
  cả hai nơi gọi cùng một luật; `orchestration.supervisor.verify_result` chỉ
  còn *giải thích* vì sao, không còn *quyết định*.
* **Signature của store được hỏi, không được bắt.** Đường fallback cho store
  cũ (không có tham số `since`) được nhận ra bằng `inspect.signature`, thay
  vì bằng cách bắt `TypeError` mà lệnh gọi ném ra — vì cách đó **cũng nuốt
  `TypeError` nổi ra từ bên trong store**, rồi gọi store lần thứ hai theo
  signature cũ. Một lỗi thật trong store giờ nổi lên, và store chỉ được gọi
  một lần.
* **Cổng attachment chỉ mở file thường.** Một FIFO hay device sẽ **chặn**
  worker thread trên một đường dẫn do request body chọn — DoS mà client
  kích hoạt được bằng một dòng JSON. Và "envelope đã seal" là một *dấu
  hiệu*, không phải bằng chứng nguồn gốc: ai cũng viết được sáu byte đó;
  thứ bảo vệ nội dung là bước **unseal** sau đó, nơi AEAD từ chối mọi thứ
  nền tảng không seal.

## Hậu quả

* **Mutant 88 → 105** (mười bảy mutant mới): sáu cho bề mặt lệnh (allow-list
  cờ dẫn, ràng buộc đích `git`, dạng gắn liền `-c`, cờ nguy hiểm của pytest,
  allow-list cờ của ctest, cổng deny-tree khi tạo thư mục — một mutant cũ
  được *neo lại* vì câu chữ đã đổi), một cho cổng isolation, ba cho clearance
  (chiều so sánh, suy ra clearance, nhãn lạ), ba cho trạng thái at-rest (AAD
  theo dòng, đối chiếu identity, từ chối file cũ) và bốn cho "một quyết định,
  một chỗ" (luật success, dò cursor, cách dùng cursor, cổng file thường).
* **Định dạng state đổi**: `user_version` 3 → 4. Một state đã seal bằng
  định dạng cũ cần một lần chạy script vận hành — chi phí nâng cấp thật,
  được ghi ra thay vì giấu.
* **Desktop một người dùng**: nếu không có Docker, phải khai rõ
  `AGENT_SANDBOX_REQUIRE_ISOLATION=0` trong `AIAssistant/.env` (file mà
  `load_local_dotenv(PACKAGE_ROOT)` nạp lúc compose) hoặc trong environment
  của tiến trình Qt. Đây là quyết định của người vận hành, được viết ra.
* Bốn lỗi của review được đóng bằng test hồi quy và bằng mutant: mỗi quyết
  định mới có một mutant **trong cùng commit**, theo đúng luật của repo.

### Gap còn lại, đã ghi nhận

* **`cmake -P`, `ctest --build-and-test` và `pytest` vốn dĩ thực thi code.**
  Không có allow-list nào làm cho chúng vô hại; ranh giới thật của chúng là
  container. Đó là lý do cổng isolation mặc định bật, và là lý do luật cũ
  ("allow-list là một OS boundary") đã bị ghi lại thành đúng.
* **Test của nền tảng vẫn ghi được** (`AIAssistant/tests`). Một agent được
  phê duyệt có thể sửa một test để nó ngừng ghim một guard. Neo tin cậy của
  lớp này là **CI**: CI chạy lại gate trên cây đã commit, nên một test bị sửa
  ở máy cục bộ không làm giảm gate.
* **Rollback toàn tệp**: kẻ tấn công có quyền ghi vẫn có thể thay file
  state bằng một bản seal cũ hơn của chính nó (tính toàn vẹn theo thời gian).
  Ràng buộc theo dòng không chống được việc phục hồi một bản cũ; chống
  rollback cần một neo bên ngoài file.
* **Tính nhất quán giữa hai ngôn ngữ**: hợp đồng seal của tệp đính kèm chat
  (`enc:v1:` + AAD cố định) **không đổi** — chỉ state của task store đổi —
  nên hợp đồng chéo với C++ (`tests/unit/test_at_rest_cipher_contract`) vẫn
  đúng.
