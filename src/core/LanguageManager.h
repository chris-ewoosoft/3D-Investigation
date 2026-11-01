#ifndef LANGUAGEMANAGER_H
#define LANGUAGEMANAGER_H

#include <QObject>
#include <QString>
#include <QMap>
#include <shared_mutex>
#include "Global.h"
#include "ILanguageService.h"

/**
 * @brief Quản lý ngôn ngữ ứng dụng (Tiếng Việt / English).
 *
 * Concrete implementation of ILanguageService, owned by the composition root
 * (AppShell) and resolved through ServiceRegistry:
 *   ctx->services()->get<ILanguageService>()->translate("key")
 *
 * Ngôn ngữ được lưu per-user vào Config_User.ini:
 *   [User_<username>]
 *   language=vi
 */
class APP_EXPORT LanguageManager : public ILanguageService {
    Q_OBJECT
public:
    LanguageManager();
    ~LanguageManager() override = default;
    LanguageManager(const LanguageManager&) = delete;
    LanguageManager& operator=(const LanguageManager&) = delete;

    // ILanguageService
    QString currentLanguage() const override;
    void setLanguage(const QString &lang) override;
    QString translate(const QString &key) const override;
    QString translate(const char *key) const override;
    QString displayName() const override;
    QString flagEmoji() const override;

private:
    void loadTranslations();

    mutable std::shared_mutex m_mutex;
    QString m_lang = "vi"; // default: Vietnamese

    // Map: key → {vi, en}
    struct Entry { QString vi; QString en; };
    QMap<QString, Entry> m_dict;
};

#endif // LANGUAGEMANAGER_H
