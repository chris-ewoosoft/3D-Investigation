# 3D Reconstruction & AI Medical Assistant

Dự án phần mềm y tế tích hợp hiển thị DICOM MPR, tái tạo 3D từ ảnh 2D (Structure from Motion) và trợ lý ảo AI thông minh. Dự án được xây dựng trên kiến trúc **Service-Oriented Modular Plugin**, cho phép mở rộng tính năng linh hoạt và hoàn toàn độc lập, loại bỏ triệt để Anti-pattern "God Object".

## 🚀 Kiến trúc & Nền tảng Kỹ thuật (Mới)

### 1. Kiến trúc Service-Oriented Plugin
Hệ thống được thiết kế hoàn toàn Decoupled (lỏng lẻo) thông qua các mẫu thiết kế hiện đại:
- **`IAppContext` (Service Locator)**: Thay vì phụ thuộc trực tiếp vào `MainWindow` (God Object), các Plugin giao tiếp với lõi ứng dụng thông qua interface `IAppContext`. 
- **Sub-Services**: Cung cấp các service chuyên biệt bao gồm `ISceneService` (Quản lý 3D/VTK), `IViewerService` (Quản lý 2D/Image), và `ISettingsService` (Cấu hình hệ thống).
- **`SignalBus`**: Luồng sự kiện (Events) như đổi ảnh, thay đổi trạng thái UI được truyền tải độc lập qua Event Bus, ngắt hoàn toàn kết nối cứng (Hard-coupling) giữa các Plugin và giao diện chính.
- **Plugin Lifecycle**: Giao thức `IPlugin` hỗ trợ `loadOrder()` và `onAppReady()` để kiểm soát chặt chẽ thứ tự khởi tạo của các module.

### 2. Quản lý Môi trường Tập trung (AppConfig)
- Sử dụng mô hình Singleton `AppConfig` để quản lý tập trung toàn bộ đường dẫn của dự án (Models, Logs, Predict, Plugins, Config...).
- Loại bỏ hoàn toàn các Macro biên dịch phụ thuộc phần cứng (như `__FILE__`), giúp dự án an toàn và dễ dàng đóng gói (Deploy) ra môi trường Production.

---

## 🌟 Các Mô-đun Tính năng Chính

### 1. Viewer Plugin (Xem ảnh y tế & DICOM MPR)
- **Đồng bộ 3 mặt cắt**: Axial, Sagittal, Coronal đồng bộ mượt mà qua tâm điểm Crosshair.
- **Volume Rendering**: Dựng khối 3D từ dữ liệu DICOM, điều hướng đa góc nhìn.
- Hỗ trợ xem nhiều định dạng ảnh 2D và cuộn ảnh (Auto-navigation).

### 2. Reconstruction Plugin (Tái tạo 3D - SfM)
- **Pipeline SfM**: Trích xuất đặc trưng (SIFT/ORB), khớp điểm (RANSAC), và ước lượng Camera Pose.
- **Triangulation**: Sinh đám mây điểm 3D (Point Cloud) có màu từ chuỗi ảnh 2D đa góc chụp.
- Quản lý Point Cloud thông qua thư viện PCL (lọc nhiễu, hiển thị).

### 3. AI Processor Plugin (Object Detection & Segmentation)
- **YOLOv11 Inference**: Sử dụng mô hình nhận diện và phân vùng chạy trên GPU qua ONNX Runtime.
- **Predict Logs**: Tự động lưu kết quả dự đoán vào thư mục `Predict/detection` và `Predict/segmentation` với định dạng tên file kèm timestamp.
- Tự động re-run inference khi người dùng cuộn xem hình ảnh khác.

### 4. AI Assistant Plugin (Trợ lý ảo y tế)
- **Local LLM**: Tích hợp mô hình Qwen2.5 chạy cục bộ, phản hồi nhanh chóng không cần Internet.
- **RAG (Retrieval-Augmented Generation)**: Chatbot có khả năng trích xuất thông tin từ tài liệu đính kèm (Docx) và mã nguồn dự án.
- **Chat UI Hiện đại**: Hỗ trợ Markdown, tải hình ảnh/tài liệu đính kèm, bong bóng chat tự co giãn, và trình xem ảnh phóng to (Image Viewer) mờ nền.

---

## 🛠 Yêu cầu hệ thống

- **HĐH**: Windows 10/11 x64.
- **Compiler**: MSVC 2022 (Yêu cầu `vcvars64.bat` cho môi trường CLI).
- **Thư viện chính**:
  - **Qt 6.9.3**: Framework giao diện và plugin.
  - **VTK 9.6.0**: Render 3D và DICOM.
  - **OpenCV 4.x**: Xử lý ảnh và SfM.
  - **PCL 1.15.1**: Xử lý đám mây điểm.
  - **ONNX Runtime 1.20.1**: Chạy mô hình AI trên GPU (CUDA).

---

## 📂 Cấu trúc mã nguồn

- `src/core/`: Chứa các thành phần cốt lõi của hệ thống (`IAppContext`, `IPlugin`, `SignalBus`, `AppConfig`, `AppConstants`), quy định toàn bộ chuẩn giao tiếp và cấu hình.
- `src/app/`: Lõi ứng dụng (Cài đặt `MainWindow`, `StyleManager`, khởi chạy hệ thống).
- `src/modules/`: Các Plugin động (.dll) đại diện cho các tính năng nghiệp vụ.
- `src/utils/`: Tiện ích dùng chung (Dialogs, Progress UI, FileUtilities).
- `Predict/`: Thư mục lưu trữ kết quả dự đoán AI.
- `AITraining/`: Scripts Python cho server AI và huấn luyện model.

---

## ⚙️ Hướng dẫn Biên dịch & Triển khai

1. **Cấu hình CMake**: 
   - Mở dự án trong Qt Creator. Đảm bảo các thư viện (VTK, OpenCV, PCL) được cấu hình đúng đường dẫn tại `CMakeLists.txt`.
2. **Build**: 
   - Sử dụng Kit **MSVC 2022 64-bit**.
   - Khuyến nghị **Release mode** để đạt hiệu suất Render và AI tốt nhất.
   - *Lưu ý*: Hãy thực hiện Clean & Rebuild toàn bộ nếu có sự thay đổi trong thư mục `src/core/`.
3. **Triển khai**: 
   - Khi Build thành công, các file `.dll` của Plugin sẽ tự động được copy vào thư mục `plugins/` nằm cạnh tệp thực thi chính.

---

## 📝 Bảo trì & Mở rộng
Dự án áp dụng mô hình kiến trúc lỏng lẻo cực cao (Decoupled Architecture). Việc phát triển một tính năng mới chỉ đơn giản là tạo ra một Plugin implement `IPlugin` và đăng ký với `IAppContext`, hoàn toàn không cần can thiệp hay sửa đổi source code của lõi `MainWindow`.

---

## 📚 Tài liệu Code (Doxygen)
Dự án sử dụng Doxygen để tự động sinh tài liệu code. Để tạo tài liệu:
1. Mở thư mục `Doxygen/`.
2. Chạy lệnh `doxygen Doxyfile`.
3. Mở file `Doxygen/html/index.html` trên trình duyệt để xem tài liệu chi tiết của toàn bộ Class và API trong source code.
