#include "AIPredictionLogger.h"

#include "IAppPaths.h"

#include <QDateTime>
#include <QDir>
#include <QFileInfo>
#include <QThreadPool>
#include <opencv2/imgcodecs.hpp>

namespace AIPredictionLogger {

void logAsync(IAppPaths* paths, const QString &mode, const QString &sourceImagePath, const cv::Mat &resultImage)
{
    if (!paths) return;

    const QString currentDate = QDateTime::currentDateTime().toString("yyyy-MM-dd");
    const QString predictDir = paths->predictDir(mode) + "/" + currentDate;
    QDir().mkpath(predictDir);

    const QString logPath = predictDir + "/" + QFileInfo(sourceImagePath).baseName() + "_" +
                            QDateTime::currentDateTime().toString("yyyyMMdd_HHmmss") + ".png";

    const cv::Mat resultClone = resultImage.clone();
    QThreadPool::globalInstance()->start([logPath, resultClone]() {
        cv::imwrite(logPath.toStdString(), resultClone);
    });
}

}
