#include <QApplication>
#include <QSurfaceFormat>
#include <QIcon>
#include <vtkOpenGLRenderWindow.h>
#include "MainWindow.h"
#include "Logger.h"
#include "AppConfig.h"
#include "StyleManager.h"

int main(int argc, char* argv[])
{
    QApplication app(argc, argv);
    app.setWindowIcon(QIcon("f:/PROJECTS/QT/3D-Reconstruction/src/app/app_icon.png"));
    StyleManager::applyTheme(&app);
    
    // Initialize AppConfig
    AppConfig::instance().initialize(QApplication::applicationDirPath());
    
    Logger::initialize();
    
    // Tắt multisampling để tránh xung đột với Qt
    vtkOpenGLRenderWindow::SetGlobalMaximumNumberOfMultiSamples(0);

    QSurfaceFormat::setDefaultFormat(QSurfaceFormat::defaultFormat());
    MainWindow window;
    window.show();
    return app.exec();
}
