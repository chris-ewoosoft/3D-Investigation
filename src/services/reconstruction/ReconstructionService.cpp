#include "ReconstructionService.h"
#include <QDir>

ReconstructionService::ReconstructionService()
    : m_pipeline(std::make_unique<ReconstructionPipeline>())
{
}

void ReconstructionService::setImages(const QStringList& paths) {
    std::vector<QString> stdPaths;
    stdPaths.reserve(static_cast<size_t>(paths.size()));
    for (const QString& p : paths)
        stdPaths.push_back(QDir::toNativeSeparators(p));
    m_pipeline->setImages(stdPaths);
}

bool ReconstructionService::loadCameraParams(const QString& filePath) {
    return m_pipeline->loadCameraParams(filePath);
}

bool ReconstructionService::reconstruct() {
    m_running = true;
    bool ok = m_pipeline->reconstruct();
    m_running = false;
    return ok;
}

bool ReconstructionService::isRunning() const {
    return m_running;
}

void ReconstructionService::stopReconstruction() {
    // ReconstructionPipeline doesn't have a stop API yet.
    // This is a placeholder — thread termination handled by ReconstructThread.
    m_running = false;
}

QStringList ReconstructionService::getImageList() const {
    QStringList result;
    for (const QString& p : m_pipeline->getImages())
        result.append(p);
    return result;
}

std::vector<cv::Point3f> ReconstructionService::getPointCloud() const {
    return m_pipeline->getPointCloud();
}

std::vector<cv::Vec3b> ReconstructionService::getPointColors() const {
    return m_pipeline->getPointColors();
}

bool ReconstructionService::hasResult() const {
    return !m_pipeline->getPointCloud().empty();
}

void ReconstructionService::processPointCloud() {
    m_pipeline->processPointCloud();
}
