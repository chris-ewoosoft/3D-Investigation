#ifndef ISETTINGSSERVICE_H
#define ISETTINGSSERVICE_H

#include <QString>

/**
 * @brief Per-key "last used path" persistence for plugins.
 *
 * Plain abstract interface — implemented by SettingsService in AppCore and
 * exposed through the ServiceRegistry.
 */
class ISettingsService {
public:
    /// ServiceRegistry lookup key (see ServiceRegistry.h).
    static constexpr const char *ServiceKey =
        "com.3dreconstruction.service.ISettingsService";

    virtual ~ISettingsService() = default;
    virtual QString getLastUsedPath(const QString& key = "default") const = 0;
    virtual void    setLastUsedPath(const QString& key, const QString& path) = 0;
};

#endif // ISETTINGSSERVICE_H
