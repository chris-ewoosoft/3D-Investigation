#ifndef IAIPREDICTIONLOGSERVICE_H
#define IAIPREDICTIONLOGSERVICE_H

#include <QString>
#include "Global.h"

namespace cv { class Mat; }

/**
 * @brief Persists an AI prediction image outside the plugin layer.
 *
 * The write is slow (a PNG encode) and must not run on the GUI thread, but
 * the code that produces the image lives in a plugin: a task scheduled on a
 * pool the plugin does not own would still execute plugin code after the
 * plugin was unloaded. The service therefore owns the worker and joins it in
 * its destructor — the same reason the model loading lives in AppCore
 * (IAIModelLoadService) instead of in the plugin.
 */
class IAIPredictionLogService {
public:
    /// ServiceRegistry lookup key (see ServiceRegistry.h).
    static constexpr const char *ServiceKey =
        "com.3dreconstruction.service.IAIPredictionLogService";

    virtual ~IAIPredictionLogService() = default;

    /**
     * @brief Write @p resultImage under the prediction folder of @p mode.
     *
     * Returns immediately: the write happens on the service's own worker.
     * A missing path provider or an unwritable folder is a warning, never
     * an exception — logging must not break the inference that produced it.
     */
    virtual void logPrediction(const QString &mode, const QString &sourceImagePath,
                                const cv::Mat &resultImage) = 0;
};

#endif // IAIPREDICTIONLOGSERVICE_H
