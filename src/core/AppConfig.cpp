#include "AppConfig.h"
#include "../utils/ModernMessageBox.h"
#include <QDir>
#include <QApplication>
#include "LanguageManager.h"

AppConfig& AppConfig::instance() {
    static AppConfig instance;
    return instance;
}

void AppConfig::initialize(const QString& appDir) {
    m_appDir = appDir;
    
    // Find project root by looking for CMakeLists.txt
    QDir dir(m_appDir);
    while (!dir.isRoot() && !dir.exists("CMakeLists.txt")) {
        dir.cdUp();
    }
    m_projectRoot = dir.absolutePath();

    QFileInfo configInfo(configPath());
    QDir configDir = configInfo.absoluteDir();
    if (!configDir.exists() && !configDir.mkpath(".")) {
        ModernMessageBox::critical(nullptr, LM_TR("app.critical_error"), 
            LM_TR("app.config_error").arg(configDir.absolutePath()));
    }
}

QString AppConfig::appDir() const {
    return m_appDir;
}

QString AppConfig::configPath() const {
    return QDir::cleanPath(m_projectRoot + "/Config/Config.ini");
}

QString AppConfig::logsDir() const {
    return QDir::cleanPath(m_projectRoot + "/Logs");
}

QString AppConfig::modelsDir() const {
    return QDir::cleanPath(m_projectRoot + "/AITraining/Models");
}

QString AppConfig::predictDir(const QString& type) const {
    return QDir::cleanPath(m_projectRoot + "/Predict/" + type);
}

QString AppConfig::aiTrainingDir() const {
    return QDir::cleanPath(m_projectRoot + "/AITraining");
}

QString AppConfig::uploadDir() const {
    return QDir::cleanPath(m_projectRoot + "/Upload");
}

QString AppConfig::pluginsDir() const {
    return QDir::cleanPath(m_appDir + "/plugins");
}
