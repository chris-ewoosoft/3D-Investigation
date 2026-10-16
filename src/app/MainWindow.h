#ifndef MAINWINDOW_H
#define MAINWINDOW_H

#include <QMainWindow>
#include <QVTKOpenGLNativeWidget.h>
#include <vector>
#include <QStringList>
#include <QMap>
#include <QPushButton>
#include <QStackedWidget>
#include <QHBoxLayout>
#include <memory>

#include "IAppContext.h"
#include "AppShell.h"

QT_BEGIN_NAMESPACE
namespace Ui { class MainWindow; }
QT_END_NAMESPACE

class IPlugin;

/**
 * @brief MainWindow — Pure UI Shell.
 *
 * Sau refactor, MainWindow chỉ chịu trách nhiệm:
 *  1. Xây dựng layout/widgets (title bar, tab bar, VTK frame)
 *  2. Load và init plugins
 *  3. Delegate IAppContext sang AppShell
 *
 * Không còn implement IAppContext trực tiếp.
 * Dùng context() để lấy IAppContext* khi cần.
 */
class MainWindow : public QMainWindow {
    Q_OBJECT

public:
    explicit MainWindow(QWidget *parent = nullptr);
    ~MainWindow() override;

    /** Trả về IAppContext để truyền vào Plugin::initialize(). */
    IAppContext* context() { return m_shell.get(); }

protected:
    void mousePressEvent(QMouseEvent *event) override;
    void mouseMoveEvent(QMouseEvent *event) override;
    void mouseReleaseEvent(QMouseEvent *event) override;

private slots:
    void onLanguageChanged(const QString &lang);

private:
    // ── Build UI ─────────────────────────────────────────────────────────────
    void setupUI();
    void setupTitleBar(QWidget* root, QVBoxLayout* rootLayout);
    void setupTabContent(QWidget* root, QVBoxLayout* rootLayout);
    void setupVtkFrame(QWidget* root, QVBoxLayout* rootLayout);
    void activateTab(const QString &tabName);

    // ── Plugin system ─────────────────────────────────────────────────────────
    void loadPlugins();
    void discoverPlugins();
    void sortPlugins();
    void initPlugins();

    // ── Core ──────────────────────────────────────────────────────────────────
    Ui::MainWindow*           ui;
    std::unique_ptr<AppShell> m_shell;          ///< IAppContext owner
    QVTKOpenGLNativeWidget*   m_vtkWidget = nullptr;

    // ── Title / Tab bar ───────────────────────────────────────────────────────
    QWidget     *m_titleBar     = nullptr;
    QWidget     *m_tabBarWidget = nullptr;
    QHBoxLayout *m_tabBarLayout = nullptr;
    QMap<QString, QPushButton*> m_tabButtons;
    QMap<QString, QWidget*>     m_tabPanels;
    QStackedWidget *m_tabContent = nullptr;
    QString         m_activeTab;

    // ── Frameless drag ────────────────────────────────────────────────────────
    bool   m_dragging = false;
    QPoint m_dragPos;

    // ── Plugins ───────────────────────────────────────────────────────────────
    QList<IPlugin*> m_plugins;
};

#endif // MAINWINDOW_H
