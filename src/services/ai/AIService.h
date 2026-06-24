#ifndef AISERVICE_H
#define AISERVICE_H

#include "IAIService.h"
#include "AIProcessor.h"
#include "AIAssistant.h"
#include "Global.h"
#include <memory>
#include <QObject>

/**
 * @brief Concrete implementation của IAIService.
 *
 * Wrap AIProcessor và AIAssistant — expose interface sạch cho plugin.
 * Plugin không cần include AIProcessor.h hoặc AIAssistant.h.
 */
class APP_EXPORT AIService : public QObject, public IAIService {
    Q_OBJECT
public:
    explicit AIService(QObject* parent = nullptr);
    ~AIService() override = default;

    // IAIService
    bool loadDetectionModel(const QString& modelPath) override;
    bool loadSegmentationModel(const QString& modelPath) override;
    bool loadTrackingModel(const QString& modelPath) override;
    
    bool isDetectionReady() const override;
    bool isSegmentationReady() const override;
    bool isTrackingReady() const override;
    
    cv::Mat runDetection(const cv::Mat& inputImage) override;
    cv::Mat runSegmentation(const cv::Mat& inputImage) override;
    cv::Mat runTracking(const cv::Mat& inputImage) override;
    void resetTrackingState() override;

    /**
     * Truy cập processor nội bộ (chỉ dùng trong app layer).
     * Plugin nên dùng IAIService interface.
     */
    AIProcessor* processor() { return m_processor.get(); }

    /**
     * Truy cập AI assistant nội bộ (chat/LLM).
     */
    AIAssistant* assistant() { return m_assistant.get(); }

private:
    std::unique_ptr<AIProcessor>  m_processor;
    std::unique_ptr<AIAssistant>  m_assistant;
};

#endif // AISERVICE_H
