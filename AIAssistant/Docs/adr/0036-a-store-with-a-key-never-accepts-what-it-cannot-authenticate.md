# ADR 0036 — Một store có khoá không bao giờ chấp nhận nội dung nó không xác thực được

- Status: Accepted
- Date: 2026-10-21
- Supersedes: none

## Bối cảnh

ADR 0032 đóng một nửa của lớp lỗi "AEAD downgrade": cột đã seal bị thay
bằng plaintext thì bị từ chối khi đọc. Nửa còn lại được phát hiện khi
**sửa** lớp lỗi đó, và nó là lỗi của *thiết kế*, không phải của *cài đặt*:

```
F4a: thay cột bằng plaintext, giữ nguyên version  -> BỊ TỪ CHỐI   ✅
F4b: sửa PRAGMA user_version = 1                     -> SERVE 'substituted'  ❌
```

Cơ chế: cờ *"state này đã được niêm phong"* nằm **bên trong file**
(`PRAGMA user_version`). Kẻ tấn công có quyền ghi file sửa được cờ đó, và
store tưởng mình chưa từng được niêm phong nên chạy lại *migration một
lần* — và migration đó **niêm phong chính dòng dữ liệu bị thay** rồi phục
vụ nó như thể chính nền tảng đã ghi.

Điểm mấu chốt: **một file legacy và một file bị thay là *cùng một tập
byte***. Không có tín hiệu nào *bên trong file* phân biệt được chúng — file
legacy do chính ứng dụng ghi, và file bị thay là bản sao của file legacy mà
kẻ tấn công sửa một trường. Vì vậy **ứng dụng không thể quyết định an toàn**;
chỉ *người vận hành* mới quyết được, bằng cách nói rõ ra.

Cùng lớp lỗi đó, ở store thứ hai: `PendingActionStore` chấp nhận file
plaintext như một file "legacy" và niêm phong lại lúc đọc. Nghĩa là kẻ tấn công
thay cả envelope bằng một file plaintext, và *pending approval của họ* được
đưa cho người dùng để duyệt.

## Quyết định

### 1. Quy tắc: một store có khoá không bao giờ chấp nhận nội dung nó không xác thực được

Cùng một câu, hai store, một chủ sở hữu:

* **`SqliteTaskStore`** — khi được cấu hình khoá và schema version còn dưới 3,
  store **kiểm tra toàn bộ** cột nội dung và payload event; thấy bất kỳ giá
  trị nào không phải envelope seal thì **từ chối mở**, với thông báo nêu đúng
  lệnh cần chạy. Nó *không* tự migrate.
* **`PendingActionStore`** — khi được cấu hình khoá và file không phải envelope
  seal thì **từ chối đọc** (fail closed). Hàng đợi pending là tạm thời, nên
  từ chối tốn một lần duyệt lại, không tốn dữ liệu.

Không có store nào chấp nhận nội dung nó không xác thực được nữa — kể cả
trong trường hợp "nó có lẽ là legacy".

### 2. Migration trở thành hành động **tường minh** của người vận hành

`scripts/seal_plaintext_state.py`:

* niêm phong mọi cột nội dung và payload event của task store, và payload
  pending, dưới **đúng khoá mà ứng dụng sẽ mở** (đọc qua cùng `settings`
  mà composition root đọc — nên script không thể trôi khỏi ứng dụng và
  niêm phong bằng một khoá khác);
* mỗi phần chạy trong **một transaction**: một giá trị không đọc được thì
  huỷ và bỏ nguyên state;
* đánh dấu store đã seal (`PRAGMA user_version = 3`);
* `--verify` báo trạng thái mà **không thay đổi gì**;
* **không bao giờ** in ra khoá, và **từ chối** khi không có khoá cấu hình — nó
  không tự sinh một khoá, vì khoá sai thì state sẽ không đọc được.

### 3. Store đóng connection khi constructor từ chối

Một state bị từ chối **không được** để lại file bị khoá: connection giữ
lock trên nó (trên Windows file còn không thể bị xoá hay thay thế khi đang
mở), và một store nửa mở sẽ chặn chính người vận hành sửa lại state.
`SqliteTaskStore.__init__` nay đóng connection khi `_migrate()` ném.

### 4. Đường nâng cấp được ghi rõ, không giấu

Một triển khai đã có at-rest encryption và **state vẫn plaintext** (tức là nó
nâng cấp từ một bản build trước ADR 0027) sẽ không khởi động cho tới khi
người vận hành chạy:

```
python AIAssistant/scripts/seal_plaintext_state.py --verify   # xem trạng thái
python AIAssistant/scripts/seal_plaintext_state.py             # niêm phong một lần
```

Đây là **chi phí thật** của phương án này, và nó được ghi lại thay vì giấu.
Đổi lại: kẻ tấn công chỉ cần quyền ghi file state thì **không còn đổi được
gì** — trước đây họ có thể khiến ứng dụng phục vụ payload của chính họ.

### 5. Hệ quả với sản phẩm

Ứng dụng desktop khởi động tiến trình AI service; nếu state bị từ chối,
tiến trình đó dừng lại với thông báo nêu lệnh cần chạy. Đây là **hành vi
fail-closed** và là điều đúng cho một state bị can thiệp — nhưng nó cũng là
một bước nâng cấp mà người dùng phải biết. Bước tiếp theo tự nhiên (nằm
ngoài phạm vi gói Python) là phía Qt **tự chạy script một lần** khi nó thấy
đúng thông báo đó, hoặc chạy nó một lần ngay khi nâng cấp.

## Hậu quả

* Lớp lỗi "AEAD downgrade" **đóng trọn vẹn**: cả hai đường (thay cột, giả
  mạo version) đều bị từ chối, và cả hai đường của store pending nữa.
* Hai mutant mới — một cho mỗi store — và `MIN_MUTANTS` giữ ở 88 (một mutant
  cũ của migration ngầm bị thay bằng hai mutant của hai guard mới).
* Một script vận hành mới, có test riêng, và **không** in ra khoá.

### Gap còn lại, đã ghi nhận

* **Bước nâng cấp cần người vận hành** (xem mục 4). Đây là đánh đổi có ý
  thức: an toàn chống kẻ tấn công có quyền ghi file state, đổi lại một bước
  vận hành một lần cho người dùng nâng cấp từ bản build cũ.
* **Một kẻ tấn công có quyền ghi file state vẫn có thể xoá nó** — mất sẵn
  có, không phải tấn công tính toàn vẹn. Đây là giới hạn của mọi thiết kế
  at-rest không có neo tin cậy bên ngoài, và nó đã được ghi từ ADR 0027.
* **Ổ đĩa và ACL vẫn là trách nhiệm của operator** (BitLocker/LUKS, ACL của
  thư mục khoá) — không đổi so với ADR 0034.
