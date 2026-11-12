#ifndef RECONSTRUCTIONWORKER_H
#define RECONSTRUCTIONWORKER_H

#include <QObject>
#include <QString>

class ReconstructionService;

/**
 * @brief Worker-object (Qt moveToThread pattern) thay cho lớp QThread
 *        subclass cũ. Chỉ chứa slot run() gọi lõi đồng bộ của service và
 *        phát finished() — không sở hữu thread nào; ReconstructionService
 *        move object này vào QThread nội bộ.
 *
 * Không APP_EXPORT: chỉ phục vụ nội bộ AppCore, không thuộc ABI plugin.
 */
class ReconstructionWorker : public QObject {
    Q_OBJECT
public:
    explicit ReconstructionWorker(ReconstructionService *service, QObject *parent = nullptr)
        : QObject(parent), m_service(service) {}

public slots:
    void run();

signals:
    /** Phát từ worker thread; receiver (service) ở thread khác → queued auto.
     *  Lý do thất bại (nếu có) được service tự đọc từ m_lastError trong handler. */
    void finished(bool ok);

private:
    ReconstructionService *m_service; ///< không thuộc hữu; service sống lâu hơn worker
};

#endif // RECONSTRUCTIONWORKER_H
