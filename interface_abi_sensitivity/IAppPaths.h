#ifndef IAPPPATHS_H
#define IAPPPATHS_H

#include <QString>

/**
 * @brief Read-only application path provider (config/data/model directories).
 *
 * Registered by the composition root (AppShell) so plugins and services can
 * resolve paths through the ServiceRegistry instead of reaching for the
 * AppConfig process singleton. AppConfig implements this interface; Main.cpp
 * and Logger keep using the singleton during bootstrap, before the registry
 * exists.
 */
class IAppPaths {
public:
    /// ServiceRegistry lookup key (see ServiceRegistry.h).
    static constexpr const char *ServiceKey =
        "com.3dreconstruction.service.IAppPaths";

    virtual ~IAppPaths() = default;

    virtual QString appDir() const = 0;
    virtual QString projectRootDir() const = 0;
    virtual QString dataRootDir() const = 0;
    virtual QString configPath() const = 0;
    virtual QString logsDir() const = 0;
    virtual QString modelsDir() const = 0;
    virtual QString predictDir(const QString& type) const = 0;
    virtual QString aiAssistantDir() const = 0;
    virtual QString aiComputerVisionDir() const = 0;
    virtual QString uploadDir() const = 0;
    virtual QString thumbnailsDir() const = 0;
    virtual QString pluginsDir() const = 0;
};

#endif // IAPPPATHS_H
