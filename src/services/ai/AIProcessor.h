#ifndef AIPROCESSOR_H
#define AIPROCESSOR_H

#include <QString>
#include <opencv2/opencv.hpp>
#include <onnxruntime_cxx_api.h>
#include <vector>
#include <memory>
#include <atomic>
#include <mutex>
#include <shared_mutex>
#include <map>

#include "Global.h"
#include "AIYoloPostProcessor.h"

struct AIResult {
    int class_id;
    float confidence;
    cv::Rect box;
    std::vector<float> mask_coeffs;
};

/**
 * @brief ONNX inference engine for detection / segmentation / tracking.
 *
 * Thread-safety contract: the instance is shared between the GUI thread
 * (interactive inference + model loading via QtConcurrent) and the video
 * tracking worker. Model load (session swap) takes the session mutex
 * exclusively; inference takes it shared — Ort::Session::Run is thread-safe
 * as long as the session is not being replaced/reset concurrently.
 * Loaded flags and the confidence threshold are atomics so state queries
 * never need the lock. Tracking state is guarded separately by
 * m_trackingMutex.
 */
class APP_EXPORT AIProcessor {
public:
    AIProcessor();
    ~AIProcessor();

    bool loadDetectionModel(const QString& modelPath);
    bool loadSegmentationModel(const QString& modelPath);
    bool loadTrackingModel(const QString& modelPath);

    bool isDetectionModelLoaded() const { return isDetModelLoaded.load(); }
    bool isSegmentationModelLoaded() const { return isSegModelLoaded.load(); }
    bool isTrackingModelLoaded() const { return isTrackingLoaded.load(); }

    /** Set confidence threshold [0.0, 1.0] used in detection & segmentation. */
    void setConfidenceThreshold(float threshold) { m_confidenceThreshold.store(threshold); }
    float confidenceThreshold() const { return m_confidenceThreshold.load(); }

    // Returns image with drawn bounding boxes
    cv::Mat runObjectDetection(const cv::Mat& inputImage);

    // Returns image with drawn segmentation masks
    cv::Mat runSegmentation(const cv::Mat& inputImage);

    // Tracking
    cv::Mat runTracking(const cv::Mat& inputImage);
    void resetTrackingState();

private:
    void tryInitGPUProvider();
    Ort::Value prepareInputTensor(const cv::Mat& img, int width, int height, std::vector<float>& tensorValues);

    /// Spatial extent the given session expects, read from its input tensor.
    static cv::Size inputSizeOf(const Ort::Session& session);

    std::unique_ptr<Ort::Env> env;
    std::unique_ptr<Ort::SessionOptions> sessionOptions;
    std::unique_ptr<Ort::Session> detSession;
    std::unique_ptr<Ort::Session> segSession;
    std::unique_ptr<Ort::Session> trackingSession;

    /// Guards the sessions + their input sizes. Load = unique lock
    /// (session swap must not race a Run); inference = shared lock.
    mutable std::shared_mutex m_sessionMutex;

    std::atomic_bool isDetModelLoaded;
    std::atomic_bool isSegModelLoaded;
    std::atomic_bool isTrackingLoaded;
    std::atomic<float> m_confidenceThreshold{0.5f};

    // Input size of each loaded model, read from the ONNX graph at load time.
    // A model exported with a non-default `--imgsz` is therefore driven with
    // its own shape instead of the historical hard-coded 640x640.
    cv::Size m_detInputSize{AIYoloPostProcessor::kDefaultModelInputSize,
                            AIYoloPostProcessor::kDefaultModelInputSize};
    cv::Size m_segInputSize{AIYoloPostProcessor::kDefaultModelInputSize,
                            AIYoloPostProcessor::kDefaultModelInputSize};
    cv::Size m_trackingInputSize{AIYoloPostProcessor::kDefaultModelInputSize,
                                 AIYoloPostProcessor::kDefaultModelInputSize};

    // Tracking state
    std::mutex m_trackingMutex;
    std::map<int, cv::Rect> currentTracks;
    int nextTrackId = 0;

    // Persists the TRT engine cache path for the lifetime of sessionOptions.
    // trt_options.trt_engine_cache_path points into this buffer.
    QByteArray m_trtCachePathBytes;
};

#endif // AIPROCESSOR_H
