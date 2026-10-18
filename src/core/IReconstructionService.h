#ifndef IRECONSTRUCTIONSERVICE_H
#define IRECONSTRUCTIONSERVICE_H

#include <QObject>
#include <QString>
#include <QStringList>
#include <vector>
#include <opencv2/core.hpp>
#include "Global.h"

/**
 * @brief Interface trừu tượng cho dịch vụ tái tạo 3D.
 *
 * Plugin sử dụng interface này thay vì phụ thuộc trực tiếp vào
 * ReconstructionPipeline (concrete class) hay lớp worker-thread nội bộ.
 *
 * Cách dùng trong Plugin:
 *   auto* svc = ctx->services()->get<IReconstructionService>();
 *   if (svc) svc->reconstruct();          // non-blocking; busy → returns false
 *   connect(svc, &IReconstructionService::reconstructFinished, ...);
 */
class APP_EXPORT IReconstructionService : public QObject {
    Q_OBJECT
protected:
    explicit IReconstructionService(QObject* parent = nullptr) : QObject(parent) {}

public:
    /// ServiceRegistry lookup key (see ServiceRegistry.h).
    static constexpr const char *ServiceKey =
        "com.3dreconstruction.service.IReconstructionService";

    virtual ~IReconstructionService() = default;

    // --- Input ---
    /** Đặt danh sách ảnh đầu vào cho quá trình tái tạo. */
    virtual void setImages(const QStringList& paths) = 0;

    /** Tải camera parameters từ file. Trả về true nếu thành công. */
    virtual bool loadCameraParams(const QString& filePath) = 0;

    /** Cấu hình các tham số tái tạo từ Settings (mật độ, tính năng, lọc nhiễu). */
    virtual void configure(int densityLevel, int maxFeatures, bool autoClean) = 0;

    // --- Execution ---
    /**
     * @brief Bắt đầu tái tạo ở chế độ nền (worker nội bộ của service).
     * @return false nếu một tiến trình khác đang chạy — không xếp hàng.
     */
    virtual bool reconstruct() = 0;

    /** Kiểm tra có đang chạy không (dùng cho async impl). */
    virtual bool isRunning() const = 0;

    /**
     * @brief Yêu cầu dừng tái tạo (cooperative, không block).
     *
     * Trả về ngay; pipeline thoát tại checkpoint kế tiếp rồi phát
     * reconstructFinished(false). Nếu không có gì đang chạy, không có hiệu ứng.
     */
    virtual void stopReconstruction() = 0;

    // --- Output / Query ---
    /** Lấy danh sách đường dẫn ảnh đã set. */
    virtual QStringList getImageList() const = 0;

    /** Lấy point cloud kết quả (3D points). */
    virtual std::vector<cv::Point3f> getPointCloud() const = 0;

    /** Lấy màu của từng point cloud point. */
    virtual std::vector<cv::Vec3b> getPointColors() const = 0;

    /** Kiểm tra có kết quả hợp lệ không. */
    virtual bool hasResult() const = 0;

    /** Lọc và xử lý point cloud sau khi reconstruct. */
    virtual void processPointCloud() = 0;

signals:
    /** Phát một lần duy nhất sau mỗi lần reconstruct(); emitter ở GUI thread. */
    void reconstructFinished(bool success, const QString& errorMessage);
};

#endif // IRECONSTRUCTIONSERVICE_H
