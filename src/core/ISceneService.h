#ifndef ISCENESERVICE_H
#define ISCENESERVICE_H

#include <QString>
#include <vtkSmartPointer.h>

class vtkRenderer;
class QVTKOpenGLNativeWidget;
class vtkActor;

class ISceneService {
public:
    virtual ~ISceneService() = default;
    virtual vtkRenderer*            renderer() = 0;
    virtual QVTKOpenGLNativeWidget* vtkWidget() = 0;
    virtual void setTextureActor(vtkSmartPointer<vtkActor> actor) = 0;
    virtual void setPointCloudActor(vtkSmartPointer<vtkActor> actor) = 0;
    virtual void clear3DModel()    = 0;
    virtual void clear2DTexture()  = 0;
    virtual void clearPointCloud() = 0;
    virtual void resetToSingleRenderer() = 0;
    virtual void loadOBJwithMTL(const QString &objPath, const QString &mtlPath) = 0;
    virtual void onLoadDicom(const QString &path = "") = 0;
    virtual bool isPointCloudVisible() const = 0;
    virtual void setPointCloudVisible(bool visible) = 0;
};

#endif // ISCENESERVICE_H
