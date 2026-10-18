#ifndef IRECONSTRUCTIONSERVICEMOCK_H
#define IRECONSTRUCTIONSERVICEMOCK_H

#include "core/IReconstructionService.h"

/**
 * @brief Mock implementation of IReconstructionService for architecture boundary tests.
 * Does not require gmock — proves plugin can depend only on interface.
 */
class MockReconstructionService : public IReconstructionService {
public:
    MockReconstructionService() = default;
    void setImages(const QStringList&) override {}
    bool loadCameraParams(const QString&) override { return true; }
    void configure(int, int, bool) override {}
    bool reconstruct() override { return true; }
    bool isRunning() const override { return false; }
    void stopReconstruction() override {}
    QStringList getImageList() const override { return {}; }
    const std::vector<Point3D>& getPointCloud() const override {
        static const std::vector<Point3D> s_empty;
        return s_empty;
    }
    const std::vector<PointColor>& getPointColors() const override {
        static const std::vector<PointColor> s_empty;
        return s_empty;
    }
    size_t pointCount() const override { return 0; }
    bool hasResult() const override { return false; }
    void processPointCloud() override {}
};

#endif
