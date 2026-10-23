#ifndef IVIEWERSERVICE_H
#define IVIEWERSERVICE_H

#include <QString>
#include <QStringList>
#include "AIMode.h"

/**
 * @brief 2D image browsing state for plugins (DICOM series paging, AI mode).
 *
 * Plain abstract interface (no signals needed) — implemented by ViewerService
 * in AppCore and exposed through the ServiceRegistry.
 */
class IViewerService {
public:
    /// ServiceRegistry lookup key (see ServiceRegistry.h).
    static constexpr const char *ServiceKey =
        "com.3dreconstruction.service.IViewerService";

    virtual ~IViewerService() = default;
    virtual QString getCurrent2DImagePath() const = 0;
    virtual void    setCurrent2DImagePath(const QString& path) = 0;
    virtual void    setImageList(const QStringList& list, int index) = 0;
    virtual void    loadCurrentIndexImage() = 0;
    virtual void    onNextImage() = 0;
    virtual void    onPrevImage() = 0;
    virtual void    onAutoNext() = 0;
    virtual void    onAutoPrev() = 0;
    virtual AIMode  getCurrentAIMode() const = 0;
    virtual void    setAIMode(AIMode mode) = 0;
    virtual bool    isUpdateSceneEnabled() const = 0;
    virtual void    setUpdateSceneEnabled(bool enabled) = 0;
};

#endif // IVIEWERSERVICE_H
