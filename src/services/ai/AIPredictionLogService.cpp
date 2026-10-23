#include "AIPredictionLogService.h"

#include "IAppPaths.h"

#include <QDateTime>
#include <QDir>
#include <QFileInfo>
#include <QThread>
#include <opencv2/core.hpp>
#include <opencv2/imgcodecs.hpp>

AIPredictionLogService::AIPredictionLogService(IAppPaths *paths, QObject *parent)
    : QObject(parent)
    , m_paths(paths)
{
    // One worker: the writes are small and sequential; more threads would
    // only interleave the file names.
    m_pool.setMaxThreadCount(1);
    m_pool.setExpiryTimeout(30000);
}

AIPredictionLogService::~AIPredictionLogService()
{
    // The pool's destructor waits for the pending write. It is the last
    // thing that touches the service's state, so it happens here and not
    // somewhere in the middle of the teardown.
    m_pool.waitForDone();
}

void AIPredictionLogService::logPrediction(const QString &mode, const QString &sourceImagePath,
                                           const cv::Mat &resultImage)
{
    if (!m_paths || resultImage.empty()) return;

    const QString currentDate = QDateTime::currentDateTime().toString("yyyy-MM-dd");
    const QString predictDir = m_paths->predictDir(mode) + "/" + currentDate;
    if (!QDir().mkpath(predictDir)) {
        qWarning() << "[AIPredictionLog] could not create" << predictDir;
        return;
    }

    const QString logPath = predictDir + "/" + QFileInfo(sourceImagePath).baseName() + "_" +
                            QDateTime::currentDateTime().toString("yyyyMMdd_HHmmss") + ".png";

    // The Mat is reference counted: the copy keeps the pixels alive for as
    // long as the worker needs them, whatever the caller does next.
    const cv::Mat resultClone = resultImage.clone();
    m_pool.start([logPath, resultClone]() {
        cv::imwrite(logPath.toStdString(), resultClone);
    });
}
