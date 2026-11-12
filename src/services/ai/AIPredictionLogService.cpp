#include "AIPredictionLogService.h"

#include "IAppPaths.h"

#include <QDateTime>
#include <QDebug>
#include <QDir>
#include <QFileInfo>
#include <opencv2/core.hpp>
#include <opencv2/imgcodecs.hpp>

namespace {

/// The production writer. `cv::imwrite` does not return false on every failure:
/// it THROWS `cv::Exception` when it cannot encode or open the destination, so
/// the call site below needs a barrier, not just a boolean check.
bool writeImage(const QString &path, const cv::Mat &image)
{
    return cv::imwrite(path.toStdString(), image);
}

} // namespace

AIPredictionLogService::AIPredictionLogService(IAppPaths *paths, QObject *parent)
    : QObject(parent)
    , m_paths(paths)
    , m_writer(&writeImage)
{
}

AIPredictionLogService::~AIPredictionLogService()
{
    // A diagnostic write must not freeze application shutdown. The task
    // captures only copies, so returning after this bounded wait is safe even
    // when a codec or filesystem call remains stuck in its worker thread.
    const int unfinished = m_tasks.waitForFinished(m_teardownTimeoutMs);
    if (unfinished > 0) {
        qWarning() << "[AIPredictionLog] teardown left" << unfinished
                   << "stuck diagnostic write(s) to finish in the background";
    }
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
    const Writer writer = m_writer;
    static_cast<void>(blocking::run([logPath, resultClone, writer]() {
        // ── One owner for the worker exception barrier ─────────────────
        // blocking::run owns the worker-thread exception barrier. Keeping a
        // second catch here would duplicate that policy and make its mutant
        // vacuous: a diagnostic write cannot escape blocking::run either way.
        if (!writer(logPath, resultClone))
            qWarning() << "[AIPredictionLog] could not write" << logPath;
    }, &m_tasks));
}

void AIPredictionLogService::setWriterForTesting(Writer writer)
{
    m_writer = writer ? std::move(writer) : Writer(&writeImage);
}

void AIPredictionLogService::setTeardownTimeoutForTesting(int timeoutMs)
{
    m_teardownTimeoutMs = timeoutMs >= 0 ? timeoutMs : 0;
}
