#ifndef AI_PREDICTION_LOGGER_H
#define AI_PREDICTION_LOGGER_H

#include <QString>
#include <opencv2/core.hpp>

class IAppPaths;

namespace AIPredictionLogger {

/// @param paths  injected path provider (resolved from the ServiceRegistry by
///               the caller); a null pointer makes the call a no-op.
void logAsync(IAppPaths* paths, const QString &mode, const QString &sourceImagePath, const cv::Mat &resultImage);

}

#endif // AI_PREDICTION_LOGGER_H
