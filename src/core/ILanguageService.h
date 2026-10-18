#ifndef ILANGUAGESERVICE_H
#define ILANGUAGESERVICE_H

#include <QObject>
#include <QString>
#include "Global.h"

/**
 * @brief Contract for application language/translation resolution.
 *
 * Registered in ServiceRegistry so consumers depend on the interface
 * (mockable / swappable) instead of the LanguageManager singleton:
 *   auto* lang = ctx->services()->get<ILanguageService>();
 *
 * The concrete implementation remains a QApplication-lifetime singleton;
 * legacy code may still call LanguageManager::instance().
 */
class APP_EXPORT ILanguageService : public QObject {
    Q_OBJECT
public:
    /// ServiceRegistry lookup key (see ServiceRegistry.h).
    static constexpr const char *ServiceKey =
        "com.3dreconstruction.service.ILanguageService";

protected:
    explicit ILanguageService(QObject* parent = nullptr) : QObject(parent) {}

public:
    virtual ~ILanguageService() = default;

    /** Current language code: "vi" or "en". */
    virtual QString currentLanguage() const = 0;

    /** Switch language and emit languageChanged(). */
    virtual void setLanguage(const QString& lang) = 0;

    /** Translate a key; returns the key itself when missing (easy debug). */
    virtual QString translate(const QString& key) const = 0;
    virtual QString translate(const char* key) const = 0;

    /** Human-readable name of the current language. */
    virtual QString displayName() const = 0;

    /** Flag emoji of the current language. */
    virtual QString flagEmoji() const = 0;

signals:
    void languageChanged(const QString& lang);
};

#endif // ILANGUAGESERVICE_H
