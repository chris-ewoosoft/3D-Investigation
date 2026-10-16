#ifndef VIDEOTRACKERTHREAD_H
#define VIDEOTRACKERTHREAD_H

#include <QThread>
#include <QString>
#include <QMutex>
#include <QWaitCondition>
#include <opencv2/opencv.hpp>
#include "IAIService.h"

class VideoTrackerThread : public QThread {
    Q_OBJECT
public:
    explicit VideoTrackerThread(IAIService* aiService, const QString& videoPath, QObject* parent = nullptr);
    ~VideoTrackerThread() override;

    void pause();
    void resume();
    void stop();

signals:
    void frameProcessed(const cv::Mat& frame);
    void finishedTracking();
    void errorOccurred(const QString& errorMsg);

protected:
    void run() override;

private:
    IAIService* m_aiService;
    QString m_videoPath;
    
    QMutex m_mutex;
    QWaitCondition m_pauseCondition;
    
    bool m_stopRequested;
    bool m_paused;
};

#endif // VIDEOTRACKERTHREAD_H
