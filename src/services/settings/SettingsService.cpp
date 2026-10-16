#include "SettingsService.h"
#include <QSettings>
#include <QDir>
#include <QFileInfo>
#include "AppConfig.h"

SettingsService::SettingsService() {
    m_configPath = AppConfig::instance().configPath();
    QDir().mkpath(QFileInfo(m_configPath).absolutePath());
}

QString SettingsService::getLastUsedPath(const QString &key) const { 
    QSettings settings(m_configPath, QSettings::IniFormat);
    return settings.value("Paths/" + key, "").toString(); 
}

void SettingsService::setLastUsedPath(const QString &key, const QString &path) {
    QSettings settings(m_configPath, QSettings::IniFormat);
    settings.setValue("Paths/" + key, path);
    settings.sync();
}

void SettingsService::saveSettings() {
    // Left for interface compatibility
}

QString SettingsService::getConfigPath() const { 
    return m_configPath; 
}
