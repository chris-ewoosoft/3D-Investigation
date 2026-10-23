#ifndef AIPREDICTIONLOGSERVICE_H
#define AIPREDICTIONLOGSERVICE_H

#include "IAIPredictionLogService.h"
#include "Global.h"

#include <QObject>
#include <QThreadPool>

class IAppPaths;

/**
 * @brief The AppCore implementation of IAIPredictionLogService.
 *
 * Owns a single-thread pool so that:
 *  - the write never runs on the GUI thread, and
 *  - the worker is joined in the destructor, so no task outlives the
 *    service it uses.
 */
class APP_EXPORT AIPredictionLogService : public QObject, public IAIPredictionLogService {
    Q_OBJECT
public:
    explicit AIPredictionLogService(IAppPaths *paths, QObject *parent = nullptr);
    ~AIPredictionLogService() override;

    void logPrediction(const QString &mode, const QString &sourceImagePath,
                       const cv::Mat &resultImage) override;

private:
    IAppPaths *m_paths = nullptr; ///< injected, not owned
    QThreadPool m_pool;           ///< owned; joined in the destructor
};

#endif // AIPREDICTIONLOGSERVICE_H
