#ifndef ISCENESERVICEMOCK_H
#define ISCENESERVICEMOCK_H

#include "include/ISceneService.h"
#include <vtkSmartPointer.h>
#include <vtkActor.h>

/**
 * @brief Mock implementation of ISceneService for architecture boundary tests.
 */
class MockSceneService : public ISceneService {
public:
    MockSceneService() = default;
    vtkSmartPointer<vtkActor> loadImageActor(const QString&) override { return nullptr; }
    vtkSmartPointer<vtkActor> loadImageActor(const cv::Mat&) override { return nullptr; }
    bool showImage(const QString&) override { return true; }
    bool showImage(const cv::Mat&) override { return true; }
    void setTextureActor(vtkSmartPointer<vtkActor>) override {}
    void setPointCloudActor(vtkSmartPointer<vtkActor>) override {}
    void clear3DModel() override {}
    void clear2DTexture() override {}
    void clearPointCloud() override {}
    void resetToSingleRenderer() override {}
    void resetCamera() override {}
    void loadOBJwithMTL(const QString&, const QString&) override {}
    bool onLoadDicom(const QString&) override { return false; }
    vtkRenderer* renderer() override { return nullptr; }
    void render() override {}
    void setPointCloudData(const std::vector<Point3D>&, const std::vector<PointColor>&) override {}
    bool isPointCloudVisible() const override { return false; }
    void setPointCloudVisible(bool) override {}
    void applyViewSettings(const QString&) override {}
};

#endif
