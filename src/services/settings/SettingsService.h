#ifndef SETTINGSSERVICE_H
#define SETTINGSSERVICE_H

#include "IAppContext.h"
#include "ISettingsService.h"
#include "Global.h"

class APP_EXPORT SettingsService : public ISettingsService {
public:
    /// Config path is injected by the composition root (AppShell passes
    /// IAppPaths::configPath()) — the service never reaches for a singleton.
    explicit SettingsService(const QString &configPath);

    QString getLastUsedPath(const QString &key = "default") const override;
    void setLastUsedPath(const QString &key, const QString &path) override;
    void saveSettings();
    QString getConfigPath() const;

private:
    QString m_configPath;
};

#endif // SETTINGSSERVICE_H
