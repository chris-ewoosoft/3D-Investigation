#include "VideoTrackerThread.h"
#include <QDebug>
#include <QElapsedTimer>

VideoTrackerThread::VideoTrackerThread(IAIService* aiService, const QString& videoPath, QObject* parent)
    : QThread(parent), m_aiService(aiService), m_videoPath(videoPath), m_stopRequested(false), m_paused(false) {
}

VideoTrackerThread::~VideoTrackerThread() {
    stop();
    wait();
}

void VideoTrackerThread::pause() {
    QMutexLocker locker(&m_mutex);
    m_paused = true;
}

void VideoTrackerThread::resume() {
    QMutexLocker locker(&m_mutex);
    m_paused = false;
    m_pauseCondition.wakeAll();
}

void VideoTrackerThread::stop() {
    QMutexLocker locker(&m_mutex);
    m_stopRequested = true;
    m_paused = false;
    m_pauseCondition.wakeAll();
}

void VideoTrackerThread::run() {
    cv::VideoCapture cap(m_videoPath.toStdString());
    if (!cap.isOpened()) {
        emit errorOccurred("Could not open video file.");
        return;
    }

    double fps = cap.get(cv::CAP_PROP_FPS);
    int delay = (fps > 0) ? (1000 / fps) : 30;

    cv::Mat frame;
    QElapsedTimer timer;

    while (true) {
        {
            QMutexLocker locker(&m_mutex);
            if (m_stopRequested) {
                break;
            }
            while (m_paused) {
                m_pauseCondition.wait(&m_mutex);
                if (m_stopRequested) {
                    break;
                }
            }
        }

        if (m_stopRequested) break;

        timer.start();

        if (!cap.read(frame)) {
            // End of video
            break;
        }

        if (frame.empty()) continue;

        // Run tracking
        cv::Mat trackedFrame = m_aiService->runTracking(frame);
        
        emit frameProcessed(trackedFrame.clone());

        // Sleep to match FPS
        int elapsed = timer.elapsed();
        int sleepTime = delay - elapsed;
        if (sleepTime > 0) {
            msleep(sleepTime);
        }
    }

    cap.release();
    emit finishedTracking();
}
