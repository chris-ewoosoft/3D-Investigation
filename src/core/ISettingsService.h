#ifndef ISETTINGSSERVICE_H
#define ISETTINGSSERVICE_H

#include <QString>

class ISettingsService {
public:
    virtual ~ISettingsService() = default;
    virtual QString getLastUsedPath(const QString& key = "default") const = 0;
    virtual void    setLastUsedPath(const QString& key, const QString& path) = 0;
};

#endif // ISETTINGSSERVICE_H
