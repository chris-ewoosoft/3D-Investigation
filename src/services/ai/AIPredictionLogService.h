#ifndef AIPREDICTIONLOGSERVICE_H
#define AIPREDICTIONLOGSERVICE_H

#include "IAIPredictionLogService.h"
#include "Global.h"
#include "BlockingCall.h"

#include <QObject>

#include <functional>

class IAppPaths;

/**
 * @brief The AppCore implementation of IAIPredictionLogService.
 *
 * Each write runs on a dedicated background thread. The task captures only
 * values (path, image and writer), never this, so teardown can wait for a
 * bounded interval without creating a use-after-free when a filesystem or
 * codec call is stuck.
 */
class APP_EXPORT AIPredictionLogService : public QObject, public IAIPredictionLogService {
    Q_OBJECT
public:
    explicit AIPredictionLogService(IAppPaths *paths, QObject *parent = nullptr);
    ~AIPredictionLogService() override;

    void logPrediction(const QString &mode, const QString &sourceImagePath,
                       const cv::Mat &resultImage) override;

    /// The platform writer: returns false when the image could not be encoded.
    using Writer = std::function<bool(const QString &path, const cv::Mat &image)>;

    /**
     * @brief Test seam for the writer.
     *
     * The worker path is the one that must be proven to CONTAIN a throwing
     * encoder (`cv::imwrite` raises `cv::Exception` on a full disk, a path
     * OpenCV cannot encode, ...). Without a seam the only way to exercise it
     * would be to depend on the host filesystem's failure modes.
     */
    void setWriterForTesting(Writer writer);

    /// Shortens the bounded teardown wait in the hung-writer contract test.
    void setTeardownTimeoutForTesting(int timeoutMs);

private:
    IAppPaths *m_paths = nullptr; ///< injected, not owned
    blocking::TaskTracker m_tasks;
    Writer m_writer;              ///< defaults to cv::imwrite
    int m_teardownTimeoutMs = 3000;
};

#endif // AIPREDICTIONLOGSERVICE_H
