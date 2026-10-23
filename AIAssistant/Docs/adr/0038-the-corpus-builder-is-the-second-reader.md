# ADR 0038 — Corpus builder là *người đọc thứ hai*: một quy tắc đọc file, một chủ sở hữu

- Status: Accepted
- Date: 2026-10-21
- Supersedes: none

## Bối cảnh

Vòng review thứ năm chạy trên các lớp **chưa ai rà sâu** — `rag/`, `llm/`,
`internal/`, `config/`, `observability/` — theo đúng khuôn mẫu mà bốn vòng
trước đã lặp lại: lỗi nằm ở lớp *vừa được sửa* ở vòng trước, và ở lớp
*chưa ai chạm* ở vòng này.

Lớp RAG là một **người đọc thứ hai** của cùng một hệ cây thư mục. Agent
đọc file qua `tools/builtin/path_utils.agent_safe_path` — cổng đã được siết
ba lần (ADR 0033/0034/0035) và nay dùng kernel `path_policy` chung. Nhưng
`rag/index.py::scan_documents` — thứ dựng corpus mà RAG phục vụ cho mô hình
— có **chính sách riêng**:

* **không có luật link**: nó đi theo link/junction ra ngoài cây;
* **danh sách thư mục riêng**, *thiếu* hai mục mà kernel chặn
  (`node_modules`, `.vs`) → hai người đọc không đồng ý về những gì được đọc;
* **không có luật tên bí mật** (`path_policy.is_secret_name`);
* lần quét của nó và lần quét tính **hash cache** là **hai lần quét khác
  nhau** → "corpus đã đổi chưa?" được hỏi trên một tập file khác với tập đã
  index.

### 1. Đọc ra ngoài cây (P1) — đã tái hiện được

Trên Windows, một **junction** (`mklink /J`) tạo được **không cần quyền
đặc biệt**, và `os.walk` đi theo nó. Kết quả đo được trước khi vá:

```
CHUNK: [Source MD: Docs\link\private.md] | PRIVATE DOCUMENT OUTSIDE THE PROJECT…
CHUNK: [Source: Docs\link\secrets.py]     | API_KEY = 'super-secret-value-1234567890'…
chunks indexed from OUTSIDE the project: 2
```

Tức là nội dung **ngoài dự án** đã vào corpus → đi tới **mô hình** (ngữ
cảnh truy hồi) và vào **cache đã seal**. Cache có seal không cứu được một bí
mật mà ta đã đọc nhầm.

### 2. Mỗi file bị index hai lần (P2) — đã tái hiện được

Hai tập phần mở rộng chồng nhau (`.jpg/.jpeg/.png/.webp` nằm ở cả hai), và
thư mục `Docs/` nằm *trong* project root nên được quét hai lần:

```
CHUNK: image | <proj>\Docs\pic.png | [Image: Docs\pic.png]
CHUNK: image | <proj>\Docs\pic.png | [Image: Docs\pic.png]   ← lần hai
```

Corpus (và cache seal) chứa ảnh đó **hai lần**, và một lần truy hồi có thể
trả về nó hai lần.

### 3. Hash cache và scan là hai lần quét (P2)

Hash quét *mọi* file dưới thư mục tài liệu, còn scan chỉ index tài liệu →
một file lạ làm cache vô hiệu hoá để dựng lại mà không đổi corpus.

## Quyết định

### 1. Corpus builder dùng **kernel chung**, không có chính sách riêng

`rag/index.py` nay dùng `path_policy` — cùng kernel mà cổng đọc của agent
dùng:

* `is_blocked_dir` — thư mục không được đi vào (danh sách của corpus chỉ
  *cộng thêm* các thư mục artifact; nó không được **bỏ** mục nào của kernel);
* `is_secret_name` — tên file bí mật không bao giờ được đọc;
* `canonical_path` + `relative_to` — **đích thật** phải nằm trong cây được
  quét (junction/symlink ra ngoài bị từ chối).

### 2. Mỗi file được quét **đúng một lần**, bởi lần quét sở hữu nó

Thư mục tài liệu có lần quét riêng của nó (nó có thể nằm *ngoài* project
root), và lần quét project **bỏ qua** các thư mục tài liệu. Không còn hai
tập phần mở rộng chồng nhau, nên không còn file nào bị index hai lần.

Lần quét tài liệu vì vậy phải phủ **đúng những gì** lần quét project phủ
(thêm `.docx/.pdf/.txt/.eml`) — nếu không thì một `.md` dưới `Docs/` đơn
giản là **biến mất** khỏi corpus. Bản vá đầu tiên làm rơi đúng những file
đó, và ca test "một file dưới `Docs/` vẫn phải được index" ra đời từ đó.

**Đo lại trên cây thật** (repo này), tập file được index *trước* và *sau* là
**giống hệt nhau** — 1 822 file, mất 0, thêm 0. Bản vá không lấy đi gì; nó
chỉ bỏ cái index hai lần và cái đọc ra ngoài cây.

### 3. Hash cache và scan dùng **cùng một lần quét**

"Corpus đã đổi chưa?" được hỏi trên đúng tập file đã index — nên một file
không được index không còn vô hiệu hoá cache, và một file được index thì
luôn vô hiệu hoá nó.

## Vòng review thứ sáu: rà lại chính code vừa viết

Năm vòng trước đều tìm lỗi ở lớp *vừa được sửa* — nên vòng này làm đúng
điều đó: rà lại ba thứ vừa thay đổi (ranh giới corpus, ngữ pháp lệnh, ràng
buộc dòng at-rest). Nó tìm ra **hai lỗi trong code của chính tác giả**:

### 1. Lần đi theo junction đi vào *cả một ổ đĩa* (P2)

Sau khi chặn được file ở ngoài cây, lần quét **vẫn đi vào** junction đó và
mới *từ chối từng file* — nên một junction trỏ tới `C:\` khiến lần quét
duyệt cả ổ đĩa trước khi bỏ qua từng file. Luật "đích thật phải nằm trong
cây" nay được áp **ở mức thư mục** (không đi vào) *và* ở mức file (không
index) — cùng một luật, hai cấp.

### 2. Hành động "seal state" có thể **phá** state đã seal (P1)

`seal_plaintext_content` ghi `user_version = 4` ở cuối, nhưng nó chỉ seal
*plaintext*: một file đã seal theo định dạng cũ đi qua nó sẽ giữ nguyên nội
dung cũ, chỉ có identity mới — rồi **tuyên bố version 4**. File đó mở
được (version đúng) nhưng nội dung **không xác thực được** → state không
đọc được, và bản vá của người vận hành đã phá state của họ.

Đã sửa theo hướng fail-closed: hành động seal **từ chối** một state đã seal
và chỉ đường đúng (`migrate_state_row_binding.py`). Không còn đường nào ghi
một version mà nó không thực sự đã viết.

## Vòng review thứ bảy: bề mặt *tuỳ chọn* của các công cụ được phép

Sáu vòng trước đều tìm lỗi ở lớp *vừa được sửa*; vòng này rà lại **bề mặt
tuỳ chọn** (options) của các binary trong allow-list — thứ chưa vòng nào nào
chạm tới. Nó tìm ra **một lỗi P1**, cùng lớp với LSP read gate (ADR 0035) và
corpus builder (ADR 0038):

```
$ ruff check ../outside
I001 [*] Import block is un-sorted or un-formatted
 --> outside\secret.py:1:1
  |
1 | import os
2 | TOKEN = "leaked-secret-value"
```

`ruff check <thư mục>` **in ra source của từng file** → một đường đọc file
ngoài workspace, chỉ với phê duyệt *lệnh* (không có phê duyệt *đọc file*).
Cùng kiểu, các tuỳ chọn sau đọc/ghi một đường dẫn mà sandbox không ràng buộc
được, và chúng **nấp sau verb** nên cổng "cờ dẫn" không thấy:

| Lệnh | Vì sao nguy hiểm |
|---|---|
| `ruff check <dir>` | in ra source của mọi file trong thư mục |
| `ruff --config <file>` | ruff **echo nội dung** file khi không parse được |
| `pytest --junitxml=<file>` / `--log-file` | **ghi** ra ngoài workspace |
| `pytest --confcutdir=<dir>` | đọc cấu hình ngoài workspace |
| `git diff --no-index <a> <b>` | in ra **hai file bất kỳ** |
| `git commit --template=<file>` | đọc template ngoài workspace |
| `git --reference <repo>` | đọc object của repo khác |

Đã sửa: các **path positional** của `python`/`pytest`/`ruff` phải nằm trong
workspace (thêm `ruff` vào nhóm đã ràng buộc), và một bảng **tuỳ chọn nguy
hiểm theo từng binary** được từ chối ở *mọi vị trí* trên dòng lệnh — không
chỉ trước verb. `pytest` cũng không còn là trường hợp đặc biệt: cùng một
bảng, cùng một luật.

## Hậu quả

* **Mutant 105 → 112**: năm mutant cho corpus builder (luật link, tên bí mật,
  thư mục bị chặn dùng chung, mỗi file một lần, hash cache phủ đúng tập file),
  một mutant cho hành động seal (từ chối state đã seal), và một mutant cho
  nhóm binary có path positional bị ràng buộc.
* **Test mới**: `tests/unit/test_rag_corpus_boundary.py` — tám ca, gồm cả ca
  junction (bỏ qua trên filesystem không tạo được link) và ca secret-name
  (kiểm trực tiếp trên helper, vì tập phần mở rộng hiện tại không phủ tên
  bí mật nào — luật phải được ghim *trước khi* một tập phần mới phủ nó).
* **Chi phí**: một index cũ đã build có thể phải dựng lại (tập file đổi →
  hash đổi → cache miss → dựng lại). Đây là hành vi *đúng*: corpus cũ chứa
  nội dung ngoài dự án.

### Bản vá của cổng mutation: nó có thể để lại **code đã mutate**

Trong lúc vá, cổng báo hai mutant "sống sót" mà kiểm tra tay lại **bị giết**
— tức là cổng đã *nói dối*. Nguyên nhân là chính cổng:

Python kiểm tra một file `.pyc` bằng **mtime (giây tròn)** và **kích thước**
của nguồn. Một mutate **giữ nguyên kích thước** (``"1"`` → ``"0"``) và được
restore trong *cùng giây* sẽ để lại bytecode cache vẫn chứa **code đã
mutate**: lần import sau nạp mutant, nên một test *đáng lẽ phải fail* lại
xanh, và mutant bị báo "sống sót" vô lý. Tệ hơn: cây còn giữ code mutated
sau khi cổng đã xong.

Cổng nay **xoá cache bytecode** của file nó vừa mutate (trước và sau lần
chạy) và chạy suite với `PYTHONDONTWRITEBYTECODE=1`, nên thứ được đo là
**nguồn đã mutate**, và không còn gì mutated sót lại. Hai ca test mới ghim
đúng điều đó.

Đây cũng là bài học chung của năm vòng review: **một cổng xanh không nói
lên được nó có đo đúng thứ nó tưởng** — kể cả khi nó là cổng của chính
repo này.

Một lần nữa trong cùng phiên này, ba mutant báo "sống sót" **không có lý do**:
chính tác giả đã xoá `__pycache__` (và tự kiểm mutant bằng tay) **trong lúc
cổng đang chạy** — nên lần import trong tiến trình test lấy bytecode của
*nguồn đã restore*, tức là mã **chưa mutate**, và mutant "sống sót" một cách
giả. Cổng không sai; **người vận hành** sai. Luật: không được đụng vào cây
(trừ `__pycache__`) khi cổng mutation đang chạy. Cổng nay in ra *output*
của suite cho mỗi mutant sống sót, để một suite "chạy 0 test" không trông
giống một suite "chạy và đồng ý".

### Gap còn lại, đã ghi nhận

* **Lớp `llm/` và `internal/` chỉ được rà ở mức đọc**: model artifact đều
  được pin SHA-256 trong `config/models.toml` (đủ 5 model + fallback), loader
  từ chối weights không khớp pin, và `internal/langgraph_engine.py` là một
  wrapper mỏng không chứa policy. Chưa có mutant cho lớp này — một gap của
  *gate*, không phải của *kiến trúc*.
* **Corpus vẫn là bản sao của tài liệu người dùng**: ADR 0028 đã seal bản
  *copy*; việc mã hoá chính cây tài liệu là việc của người dùng và vận
  hành viên (mã hoá ở tầng volume).
* **Tên bí mật mới**: luật tên bí mật là của kernel, nên một tên bí mật mới
  được thêm vào đó tự động áp cho cả hai người đọc — nhưng một tập phần mở
  rộng mới vẫn phải được kiểm bằng một ca test (đó là lý do ca test ghim
  luật trực tiếp trên helper).

### Phần C++: một phiên làm việc khác đang sửa cây đó

Hệ quả sản phẩm của ADR 0037 (cổng isolation mặc định bật) là: **desktop
một người dùng không có Docker thì mất `run_command`**, trừ khi khai báo
`AGENT_SANDBOX_REQUIRE_ISOLATION=0`. Lối thoát *không cần* sửa C++: file
`AIAssistant/.env` (mà `load_local_dotenv(PACKAGE_ROOT)` nạp lúc compose) là
đủ — và `.env.example` đã ghi rõ.

Lối thoát *tiện hơn* là cho launcher C++ khai báo luôn:

```cpp
// AIServerProcessController::start(), trước m_process->setProcessEnvironment(env)
if (!env.contains(QStringLiteral("AGENT_SANDBOX_REQUIRE_ISOLATION"))) {
    env.insert(QStringLiteral("AGENT_SANDBOX_REQUIRE_ISOLATION"), QStringLiteral("0"));
}
```

(`if (!contains(...))` để **không** ghi đè lựa chọn rõ ràng của người vận
hành.) Việc này **chưa** được đưa vào: khi tôi thử áp dụng, cây C++ đang
nằm giữa một đợt refactor **của một phiên làm việc khác** (`src/core/*` →
`src/include/*`, hash ABI của bề mặt plugin, `tests/CheckInterfaceAbi.cmake`),
và các file đó đổi nội dung **trong lúc tôi đang làm**. `TestAIServerProcessController`
bị gỡ khỏi `tests/CMakeLists.txt` giữa chừng, nên:

* mutant C++ cho quyết định này **báo INVALID** (target không tồn tại) → cổng
  mutation C++ đỏ;
* sửa `tests/CMakeLists.txt` để thêm lại target sẽ **đụng** file mà phiên kia
  đang sửa (rủi ro trùng target → CMake error).

Nên phần C++ được **giao lại** cho phiên đang giữ cây đó, kèm mã ở trên và
yêu cầu mutant + test theo đúng luật của `AGENTS.md`. Tôi chỉ giữ lại *một*
dòng sửa build (`tests/CMakeLists.txt`: `../src/core/IReconstructionService.h`
→ `../src/include/IReconstructionService.h`) — nó là đường dẫn cũ còn sót
sau đợt di chuyển, và không có nó thì **CMake không generate được** (cả cây
C++ không build được, kể cả các test không liên quan).

