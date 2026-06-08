#include "ReconstructThread.h"

ReconstructThread::ReconstructThread(IReconstructionService *service, QObject *parent)
    : QThread(parent), m_service(service), m_success(false)
{
}

void ReconstructThread::run()
{
    m_success = m_service->reconstruct();
}
