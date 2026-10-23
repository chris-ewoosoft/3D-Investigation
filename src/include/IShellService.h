#ifndef ISHELLSERVICE_H
#define ISHELLSERVICE_H

#include <QString>

class QMenu;
class QMenuBar;
class QWidget;
class IProgressDialog;

/**
 * @brief UI-shell contract: named menus, tab panels and the shared progress
 *        dialog owned by the application shell.
 *
 * Split out of IAppContext so that the context interface stays a pure
 * service-locator/kernel contract instead of accumulating every UI-shell
 * concern. Implemented by AppShell; plugins resolve it from the
 * ServiceRegistry (or via IAppContext::shell()).
 */
class IShellService {
public:
    /// ServiceRegistry lookup key (see ServiceRegistry.h).
    static constexpr const char *ServiceKey =
        "com.3dreconstruction.service.IShellService";

    virtual ~IShellService() = default;

    /** Trả về QMenu theo id; tạo mới nếu chưa tồn tại (owned by the shell). */
    virtual QMenu*    getMenu(const QString& id) = 0;
    virtual QMenuBar* menuBar() = 0;
    virtual void      updateMenuStates() = 0;

    /**
     * Trả về QWidget* panel của tab tương ứng trong Tab Bar.
     * Plugin có thể dùng để thêm QToolButton vào tab.
     * Trả về nullptr nếu tab không tồn tại.
     */
    virtual QWidget* getTabPanel(const QString& tabName) = 0;

    /** Progress dialog dùng chung (owned by the shell, không owned by caller). */
    virtual IProgressDialog* getProgressDialog() = 0;

    /** Đăng ký panel cho một tab key (gọi từ MainWindow khi build UI). */
    virtual void registerTabPanel(const QString& key, QWidget* panel) = 0;

    /** Gắn QDockWidget vào shell UI. */
    virtual void addDockWidget(Qt::DockWidgetArea area, class QDockWidget* dockWidget) = 0;

    /**
     * Remove AND delete a dock widget created by a plugin. Plugin::cleanup()
     * must call this for every dock it owns: the shell outlives the plugin,
     * so a dock that is still there after the cleanup is dead UI whose
     * behaviour belongs to a plugin that is already gone. The delete is
     * SYNCHRONOUS on purpose (deleteLater may not run before the shell
     * tears the widget down).
     */
    virtual void deleteDockWidget(class QDockWidget* dockWidget) = 0;

    /** Widget cha chuẩn cho các hộp thoại (dialogs/message boxes). */
    virtual QWidget* dialogParent() = 0;

    /** Widget trung tâm của shell (MainWindow centralWidget). */
    virtual QWidget* centralWidget() = 0;

    /** Ẩn/hiện toàn bộ cửa sổ shell (dùng khi re-authenticate / lock). */
    virtual void setVisible(bool visible) = 0;
};

#endif // ISHELLSERVICE_H
