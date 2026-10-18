#ifndef APPCONFIG_H
#define APPCONFIG_H

#include <QString>
#include <QDir>
#include <QFileInfo>
#include <shared_mutex>
#include "Global.h" // For APP_EXPORT
#include "IAppPaths.h"

/**
 * @brief Process-wide path provider. Implements IAppPaths so plugins and
 * services can resolve it from the ServiceRegistry; the singleton accessor
 * remains for bootstrap code (Main.cpp / Logger) that runs before the
 * registry exists.
 */
class APP_EXPORT AppConfig : public IAppPaths {
public:
    static AppConfig& instance();

    void initialize(const QString& appDir);

    QString appDir() const override;
    QString projectRootDir() const override;
    QString dataRootDir() const override;
    QString configPath() const override;
    QString logsDir() const override;
    QString modelsDir() const override;
    QString predictDir(const QString& type) const override;
    QString aiAssistantDir() const override;
    QString aiComputerVisionDir() const override;
    QString uploadDir() const override;
    QString thumbnailsDir() const override;
    QString pluginsDir() const override;

private:
    AppConfig() = default;
    ~AppConfig() = default;
    
    // Non-copyable
    AppConfig(const AppConfig&) = delete;
    AppConfig& operator=(const AppConfig&) = delete;

    mutable std::shared_mutex m_mutex;
    QString m_appDir;
    QString m_projectRoot; // Cache project root for convenience if running from build dir
    QString m_dataRoot;    // Writeable root directory (AppData in production)
};

#endif // APPCONFIG_H
