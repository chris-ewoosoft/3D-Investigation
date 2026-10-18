#ifndef ISHELLSERVICE_H
#define ISHELLSERVICE_H

#include <QString>

class QMenu;
class QMenuBar;
class QWidget;
class CustomProgressDialog;

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
    virtual CustomProgressDialog* getProgressDialog() = 0;

    /** Đăng ký panel cho một tab key (gọi từ MainWindow khi build UI). */
    virtual void registerTabPanel(const QString& key, QWidget* panel) = 0;
};

#endif // ISHELLSERVICE_H
