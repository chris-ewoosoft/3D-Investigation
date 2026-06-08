#ifndef IAPPCONTEXT_H
#define IAPPCONTEXT_H

#include <QString>
#include <QStringList>
#include <QMainWindow>
#include <vtkSmartPointer.h>
#include "ServiceRegistry.h"

class vtkRenderer;
class QVTKOpenGLNativeWidget;
class vtkActor;
class CustomProgressDialog;
class QMenu;
class QMenuBar;
class SignalBus;

class ISceneService;
class IViewerService;
class ISettingsService;

class IAppContext {
public:
    virtual ~IAppContext() = default;

    virtual ISceneService*    scene()    = 0;
    virtual IViewerService*   viewer()   = 0;
    virtual ISettingsService* settings() = 0;
    virtual QMainWindow*     mainWindow() = 0;
    virtual SignalBus*       signalBus() = 0;

    /**
     * @brief Trả về ServiceRegistry để plugin tự look-up bất kỳ service nào.
     *
     * Ví dụ:
     *   auto* svc = ctx->services()->get<IReconstructionService>();
     */
    virtual ServiceRegistry* services() = 0;

    virtual CustomProgressDialog*   getProgressDialog() = 0;
    virtual QMenu*  getMenu(const QString& id) = 0;
    virtual QMenuBar* menuBar() = 0;
    virtual void    updateMenuStates()      = 0;
    virtual QString translate(const QString& key) = 0;
    virtual void    setLanguage(const QString& lang) = 0;

    /**
     * Trả về QWidget* panel của tab tương ứng trong Tab Bar.
     * Plugin có thể dùng để thêm QToolButton vào tab.
     * Trả về nullptr nếu tab không tồn tại.
     */
    virtual QWidget* getTabPanel(const QString& tabName) { Q_UNUSED(tabName); return nullptr; }
};

#endif // IAPPCONTEXT_H
