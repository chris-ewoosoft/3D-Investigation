#include "AIService.h"

#include "AIInferenceGuard.h"

AIService::AIService(QObject* parent)
    : QObject(parent)
    , m_processor(std::make_unique<AIProcessor>())
{
}

bool AIService::loadDetectionModel(const QString& modelPath) {
    return m_processor->loadDetectionModel(modelPath);
}

bool AIService::loadSegmentationModel(const QString& modelPath) {
    return m_processor->loadSegmentationModel(modelPath);
}

bool AIService::isDetectionReady() const {
    return m_processor->isDetectionModelLoaded();
}

bool AIService::isSegmentationReady() const {
    return m_processor->isSegmentationModelLoaded();
}

std::vector<AIDetectionResult> AIService::detectObjects(const cv::Mat& inputImage) {
    // Every inference entry point goes through the guard: ONNX Runtime and
    // OpenCV throw, and an exception that escapes a Qt slot or a
    // QThread::run() aborts the whole process.
    return ai::runDetectionGuarded(
        "detection",
        [this](const cv::Mat& in) { return m_processor->detectObjects(in); },
        inputImage).results;
}

cv::Mat AIService::runDetection(const cv::Mat& inputImage) {
    return ai::runInferenceGuarded(
        "detection",
        [this](const cv::Mat& in) { return m_processor->runObjectDetection(in); },
        inputImage).image;
}

cv::Mat AIService::runSegmentation(const cv::Mat& inputImage) {
    return ai::runInferenceGuarded(
        "segmentation",
        [this](const cv::Mat& in) { return m_processor->runSegmentation(in); },
        inputImage).image;
}

bool AIService::loadTrackingModel(const QString& modelPath) {
    return m_processor->loadTrackingModel(modelPath);
}

bool AIService::isTrackingReady() const {
    return m_processor->isTrackingModelLoaded();
}

cv::Mat AIService::runTracking(const cv::Mat& inputImage) {
    // The tracking path runs on a BACKGROUND thread (VideoTrackerThread):
    // an exception that escaped it would reach std::terminate() and abort
    // the application, so it is contained here like every other inference.
    return ai::runInferenceGuarded(
        "tracking",
        [this](const cv::Mat& in) { return m_processor->runTracking(in); },
        inputImage).image;
}

void AIService::resetTrackingState() {
    m_processor->resetTrackingState();
}

void AIService::setConfidenceThreshold(float threshold) {
    m_processor->setConfidenceThreshold(threshold);
}

float AIService::confidenceThreshold() const {
    return m_processor->confidenceThreshold();
}
