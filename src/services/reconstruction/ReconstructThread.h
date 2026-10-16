#ifndef RECONSTRUCTTHREAD_H
#define RECONSTRUCTTHREAD_H

#include <QThread>
#include "IReconstructionService.h"
#include "Global.h"

class APP_EXPORT ReconstructThread : public QThread
{
    Q_OBJECT
public:
    explicit ReconstructThread(IReconstructionService *service, QObject *parent = nullptr);
    bool isSuccess() const { return m_success; }
protected:
    void run() override;
private:
    IReconstructionService *m_service;
    bool m_success;
};

#endif // RECONSTRUCTTHREAD_H
