#ifndef RECONSTRUCTIONSERVICE_H
#define RECONSTRUCTIONSERVICE_H

#include "IReconstructionService.h"
#include "ReconstructionPipeline.h"
#include "Global.h"
#include <memory>

/**
 * @brief Concrete implementation của IReconstructionService.
 *
 * Wrap ReconstructionPipeline, expose interface sạch cho plugin sử dụng.
 * Plugin không cần include ReconstructionPipeline.h.
 */
class APP_EXPORT ReconstructionService : public IReconstructionService {
public:
    ReconstructionService();
    ~ReconstructionService() override = default;

    // IReconstructionService
    void setImages(const QStringList& paths) override;
    bool loadCameraParams(const QString& filePath) override;
    bool reconstruct() override;
    bool isRunning() const override;
    void stopReconstruction() override;
    QStringList getImageList() const override;
    std::vector<cv::Point3f> getPointCloud() const override;
    std::vector<cv::Vec3b>   getPointColors() const override;
    bool hasResult() const override;
    void processPointCloud() override;

    /**
     * @brief Truy cập pipeline nội bộ (chỉ dùng trong app layer — không expose ra plugin).
     * Cần thiết để ReconstructThread nhận pipeline pointer.
     */
    ReconstructionPipeline* pipeline() { return m_pipeline.get(); }

private:
    std::unique_ptr<ReconstructionPipeline> m_pipeline;
    bool m_running = false;
};

#endif // RECONSTRUCTIONSERVICE_H
