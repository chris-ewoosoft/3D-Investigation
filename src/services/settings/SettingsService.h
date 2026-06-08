#ifndef SETTINGSSERVICE_H
#define SETTINGSSERVICE_H

#include "IAppContext.h"
#include "ISettingsService.h"

class SettingsService : public ISettingsService {
public:
    SettingsService();

    QString getLastUsedPath(const QString &key = "default") const override;
    void setLastUsedPath(const QString &key, const QString &path) override;
    void saveSettings();
    QString getConfigPath() const;

private:
    QString m_configPath;
};

#endif // SETTINGSSERVICE_H
